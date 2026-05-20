#!/usr/bin/env python3
import argparse
import base64
import fcntl
import json
import os
import random
import re
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request


STATE_DIR = Path(os.environ.get("OPENCLAW_STATE_DIR", Path.home() / ".openclaw"))
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
ROOT_DIR = Path(__file__).resolve().parents[1]
CODEX_CWD = Path(os.environ.get("WEIXIN_CODEX_CWD", os.environ.get("CODEX_CWD", Path.cwd())))
PLUGIN_DIR = STATE_DIR / "openclaw-weixin"
ACCOUNTS_DIR = PLUGIN_DIR / "accounts"
LOCAL_DATA_DIR = Path(os.environ.get("WEIXIN_CODEX_DATA_DIR", ROOT_DIR / "data"))
SESSIONS_PATH = LOCAL_DATA_DIR / "weixin-codex-sessions.json"
LOCK_PATH = LOCAL_DATA_DIR / "weixin-codex-direct.lock"
SEEN_PATH = LOCAL_DATA_DIR / "weixin-codex-seen.json"
DEFAULT_CODEX_COMMAND = (
    f"codex exec --json --cd {shlex.quote(str(CODEX_CWD))} --sandbox workspace-write {{prompt}}"
)
DEFAULT_CODEX_RESUME_COMMAND = (
    "codex exec resume --json {thread_id} {prompt}"
)
SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
SPEED_TO_SERVICE_TIER = {
    "fast": "priority",
    "quick": "priority",
    "priority": "priority",
    "快速": "priority",
    "快": "priority",
}
STANDARD_SPEED_VALUES = {"standard", "normal", "default", "reset", "标准", "普通", "默认", "清空"}
EFFORT_TO_REASONING = {
    "low": "low",
    "低": "low",
    "medium": "medium",
    "med": "medium",
    "中": "medium",
    "high": "high",
    "高": "high",
    "xhigh": "xhigh",
    "extra-high": "xhigh",
    "extra_high": "xhigh",
    "ultra": "xhigh",
    "超高": "xhigh",
}
DEFAULT_EFFORT_VALUES = {"default", "reset", "默认", "清空"}
CHANNEL_VERSION = "2.1.8"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = str((2 << 16) | (1 << 8) | 8)
PROGRESS_PREVIEW_CHARS = 1200


def log(message):
    print(message, flush=True)


def load_account(account_id=None):
    if account_id:
        path = ACCOUNTS_DIR / f"{account_id}.json"
    else:
        candidates = sorted(ACCOUNTS_DIR.glob("*.json"))
        candidates = [p for p in candidates if not p.name.endswith((".sync.json", ".context-tokens.json"))]
        if not candidates:
            raise RuntimeError(f"No Weixin account found under {ACCOUNTS_DIR}")
        path = candidates[0]
    payload = json.loads(path.read_text(encoding="utf-8"))
    account_id = path.stem
    token = payload.get("token")
    base_url = payload.get("baseUrl", "https://ilinkai.weixin.qq.com").rstrip("/")
    if not token:
        raise RuntimeError(f"Missing token in {path}")
    return {"account_id": account_id, "token": token, "base_url": base_url, "path": path}


def sync_path(account_id):
    return ACCOUNTS_DIR / f"{account_id}.sync.json"


def load_sync_buf(account_id):
    path = sync_path(account_id)
    if not path.exists():
        return ""
    return json.loads(path.read_text(encoding="utf-8")).get("get_updates_buf", "")


def save_sync_buf(account_id, value):
    path = sync_path(account_id)
    path.write_text(json.dumps({"get_updates_buf": value or ""}, ensure_ascii=False) + "\n", encoding="utf-8")


def load_sessions():
    if not SESSIONS_PATH.exists():
        return {}
    return json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))


def save_sessions(sessions):
    LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_PATH.write_text(json.dumps(sessions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_codex_session_index(thread_id, thread_name):
    if not thread_id:
        return
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    index_path = CODEX_HOME / "session_index.jsonl"
    entry = {
        "id": thread_id,
        "thread_name": thread_name or "微信 ClawBot",
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_codex_session_index():
    index_path = CODEX_HOME / "session_index.jsonl"
    if not index_path.exists():
        return []
    entries = []
    for line in index_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("id"):
            entries.append(entry)
    return entries


def find_codex_session_index_entry(thread_id):
    for entry in reversed(read_codex_session_index()):
        if entry.get("id") == thread_id:
            return entry
    return None


def find_codex_session_file(thread_id):
    sessions_dir = CODEX_HOME / "sessions"
    if not sessions_dir.exists():
        return None
    return next(sessions_dir.rglob(f"*{thread_id}.jsonl"), None)


def recent_codex_sessions(limit=8):
    seen = set()
    result = []
    for entry in reversed(read_codex_session_index()):
        thread_id = entry.get("id")
        if not thread_id or thread_id in seen:
            continue
        seen.add(thread_id)
        result.append(entry)
        if len(result) >= limit:
            break
    return result


def thread_name_for_message(text):
    compact = " ".join((text or "").split())
    if not compact:
        return "微信 ClawBot"
    return f"微信 ClawBot: {compact[:40]}"


def acquire_lock():
    LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = LOCK_PATH.open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError(f"Another weixin-codex-direct instance is already running ({LOCK_PATH})")
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def load_seen():
    if not SEEN_PATH.exists():
        return []
    payload = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else []


def save_seen(seen):
    LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(list(seen)[-2000:], ensure_ascii=False) + "\n", encoding="utf-8")


def session_key(message):
    return message.get("from_user_id") or message.get("session_id") or "unknown"


def get_history(sessions, key):
    session = sessions.get(key) or {}
    history = session.get("history")
    return history if isinstance(history, list) else []


def append_history(sessions, key, role, content, max_turns):
    session = sessions.setdefault(key, {})
    history = session.setdefault("history", [])
    history.append({"role": role, "content": content, "ts": int(time.time())})
    session["history"] = history[-max_turns * 2 :]
    session["updated_at"] = int(time.time())
    save_sessions(sessions)


def reset_history(sessions, key):
    sessions[key] = {"history": [], "updated_at": int(time.time())}
    save_sessions(sessions)


def bind_session(sessions, key, thread_id, thread_name=None):
    session = sessions.setdefault(key, {})
    session["codex_thread_id"] = thread_id
    session["codex_thread_name"] = thread_name or f"Codex Session: {thread_id[:8]}"
    session["updated_at"] = int(time.time())
    save_sessions(sessions)
    append_codex_session_index(thread_id, session["codex_thread_name"])


def set_session_model(sessions, key, model):
    session = sessions.setdefault(key, {})
    if model.lower() in ("default", "reset") or model in ("默认", "清空"):
        session.pop("codex_model", None)
    else:
        session["codex_model"] = model
    session["updated_at"] = int(time.time())
    save_sessions(sessions)


def normalize_speed(value):
    value = value.strip()
    lowered = value.lower()
    if lowered in STANDARD_SPEED_VALUES or value in STANDARD_SPEED_VALUES:
        return None
    return SPEED_TO_SERVICE_TIER.get(lowered) or SPEED_TO_SERVICE_TIER.get(value)


def set_session_speed(sessions, key, service_tier):
    session = sessions.setdefault(key, {})
    if service_tier:
        session["codex_service_tier"] = service_tier
    else:
        session.pop("codex_service_tier", None)
    session["updated_at"] = int(time.time())
    save_sessions(sessions)


def normalize_effort(value):
    value = value.strip()
    lowered = value.lower()
    if lowered in DEFAULT_EFFORT_VALUES or value in DEFAULT_EFFORT_VALUES:
        return None
    return EFFORT_TO_REASONING.get(lowered) or EFFORT_TO_REASONING.get(value)


def set_session_effort(sessions, key, reasoning_effort):
    session = sessions.setdefault(key, {})
    if reasoning_effort:
        session["codex_reasoning_effort"] = reasoning_effort
        session["codex_effort_user_set"] = True
    else:
        session.pop("codex_reasoning_effort", None)
        session.pop("codex_effort_user_set", None)
    session["updated_at"] = int(time.time())
    save_sessions(sessions)


def get_session_effort(session):
    if not session.get("codex_effort_user_set"):
        return None
    return session.get("codex_reasoning_effort")


def session_settings_text(session):
    thread_id = session.get("codex_thread_id") or "未绑定"
    thread_name = session.get("codex_thread_name") or "未命名"
    model = session.get("codex_model") or "默认"
    speed = "快速" if session.get("codex_service_tier") == "priority" else "标准"
    effort = get_session_effort(session) or "默认"
    return (
        "当前微信用户设置：\n"
        f"session: {thread_name}\n{thread_id}\n"
        f"model: {model}\n"
        f"speed: {speed}\n"
        f"effort: {effort}"
    )


def handle_control_command(text, sessions, key):
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered in ("/reset", "reset", "清空上下文", "重置上下文"):
        reset_history(sessions, key)
        return "已清空这段微信会话绑定的 Codex session。"

    if lowered in ("/session", "session", "当前会话", "会话"):
        session = sessions.get(key) or {}
        thread_id = session.get("codex_thread_id")
        if not thread_id:
            return "当前微信用户还没有绑定 Codex session。发送一条普通消息会自动创建，或发送 /session <thread_id> 手动绑定。"
        thread_name = session.get("codex_thread_name") or "未命名"
        return f"当前绑定的 Codex session：\n{thread_name}\n{thread_id}"

    if lowered in ("/sessions", "sessions", "会话列表", "最近会话"):
        entries = recent_codex_sessions()
        if not entries:
            return "没有找到 Codex session 索引。"
        lines = ["最近的 Codex sessions："]
        for entry in entries:
            name = entry.get("thread_name") or "未命名"
            lines.append(f"- {name}\n  {entry.get('id')}")
        lines.append("发送 /session <thread_id> 可以切换当前微信用户绑定的会话。")
        return "\n".join(lines)

    if lowered.startswith("/session ") or stripped.startswith("切换会话 "):
        thread_id = stripped.split(maxsplit=1)[1].strip()
        if not SESSION_ID_RE.match(thread_id):
            return "session id 格式不对。请发送类似：/session 019e4447-e663-7011-99b5-5e1f5d7e380a"
        entry = find_codex_session_index_entry(thread_id)
        if not entry and not find_codex_session_file(thread_id):
            return "没有在本机 ~/.codex 里找到这个 Codex session。请确认 id 来自 Codex Desktop/CLI 的真实会话。"
        thread_name = (entry or {}).get("thread_name") or f"Codex Session: {thread_id[:8]}"
        bind_session(sessions, key, thread_id, thread_name=thread_name)
        return f"已切换当前微信用户绑定的 Codex session：\n{thread_name}\n{thread_id}"

    if lowered in ("/settings", "settings", "设置", "当前设置"):
        return session_settings_text(sessions.get(key) or {})

    if lowered in ("/model", "model", "模型", "当前模型"):
        model = (sessions.get(key) or {}).get("codex_model") or "默认"
        return f"当前模型：{model}\n发送 /model <model> 切换，例如：/model gpt-5.5\n发送 /model default 恢复默认。"

    if lowered.startswith("/model ") or stripped.startswith("切换模型 ") or stripped.startswith("模型 "):
        model = stripped.split(maxsplit=1)[1].strip()
        if model.lower() not in ("default", "reset") and model not in ("默认", "清空") and not MODEL_RE.match(model):
            return "模型名格式不对。请发送类似：/model gpt-5.5，或 /model default 恢复默认。"
        set_session_model(sessions, key, model)
        current = (sessions.get(key) or {}).get("codex_model") or "默认"
        return f"已切换当前微信用户的 Codex 模型：{current}"

    if lowered in ("/speed", "speed", "速度", "当前速度"):
        speed = "快速" if (sessions.get(key) or {}).get("codex_service_tier") == "priority" else "标准"
        return (
            f"当前速度：{speed}\n"
            "可选：standard/fast，或 标准/快速。\n"
            "发送 /speed fast 开启 1.5 倍速；发送 /speed standard 恢复标准速度。"
        )

    if lowered.startswith("/speed ") or stripped.startswith("切换速度 ") or stripped.startswith("速度 "):
        value = stripped.split(maxsplit=1)[1].strip()
        if value.lower() in STANDARD_SPEED_VALUES or value in STANDARD_SPEED_VALUES:
            set_session_speed(sessions, key, None)
            return "已切换当前微信用户的速度：标准"
        service_tier = normalize_speed(value)
        if not service_tier:
            return "速度档位不支持。可选：standard/fast，或 标准/快速。"
        set_session_speed(sessions, key, service_tier)
        if service_tier == "priority":
            return "已切换当前微信用户的速度：快速（1.5 倍速，用量增加）"
        else:
            return f"已切换当前微信用户的速度：{service_tier}"

    if lowered in ("/effort", "effort", "推理", "推理强度", "当前推理", "当前推理强度"):
        effort = get_session_effort(sessions.get(key) or {}) or "默认"
        return (
            f"当前推理强度：{effort}\n"
            "可选：low/medium/high/xhigh，或 低/中/高/超高。\n"
            "发送 /effort default 恢复默认。"
        )

    if (
        lowered.startswith("/effort ")
        or stripped.startswith("切换推理 ")
        or stripped.startswith("切换推理强度 ")
        or stripped.startswith("推理 ")
        or stripped.startswith("推理强度 ")
    ):
        value = stripped.split(maxsplit=1)[1].strip()
        if value.lower() in DEFAULT_EFFORT_VALUES or value in DEFAULT_EFFORT_VALUES:
            set_session_effort(sessions, key, None)
            return "已恢复当前微信用户的推理强度：默认"
        reasoning_effort = normalize_effort(value)
        if not reasoning_effort:
            return "推理强度不支持。可选：low/medium/high/xhigh，或 低/中/高/超高。"
        set_session_effort(sessions, key, reasoning_effort)
        return f"已切换当前微信用户的推理强度：{reasoning_effort}"

    return None


def common_headers(body):
    uin = base64.b64encode(str(random.getrandbits(32)).encode("utf-8")).decode("ascii")
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": uin,
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
    }


def api_post(account, endpoint, payload, timeout=45):
    body = json.dumps({**payload, "base_info": {"channel_version": CHANNEL_VERSION}}, ensure_ascii=False)
    headers = common_headers(body)
    headers["Authorization"] = f"Bearer {account['token']}"
    req = request.Request(
        f"{account['base_url']}/{endpoint}",
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{endpoint} HTTP {exc.code}: {detail}") from exc


def get_updates(account, sync_buf):
    return api_post(
        account,
        "ilink/bot/getupdates",
        {"get_updates_buf": sync_buf or ""},
        timeout=45,
    )


def text_from_message(message):
    parts = []
    for item in message.get("item_list") or []:
        if item.get("type") == 1:
            text = (item.get("text_item") or {}).get("text")
            if text:
                parts.append(text)
        elif item.get("type") == 3:
            text = (item.get("voice_item") or {}).get("text")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def build_prompt(text, message, history, use_native_session=False):
    context = {
        "channel": "wechat-clawbot-direct",
        "from_user_id": message.get("from_user_id"),
        "to_user_id": message.get("to_user_id"),
        "session_id": message.get("session_id"),
        "message_id": message.get("message_id"),
        "message": text,
    }
    history_text = "\n".join(
        f"{item.get('role', 'unknown')}: {item.get('content', '')}" for item in history
    )
    return (
        "You are Codex replying to a WeChat ClawBot message. "
        "Reply in the user's language unless they ask otherwise.\n"
        "If the user says /reset, the local WeChat-to-Codex session is cleared.\n"
        "Keep replies concise for WeChat, but do not omit important steps when the user asks for implementation help.\n\n"
        f"Conversation history fallback:\n{history_text or '(native Codex session is handling continuity)'}\n\n"
        f"Current message context JSON:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def extract_reply(stdout):
    text = stdout.strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    for key in ("reply", "message", "output", "text", "content", "last_message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(payload, ensure_ascii=False, indent=2)


def clip_text(text, limit=PROGRESS_PREVIEW_CHARS):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...（已截断）"


def format_command(command, prompt, thread_id=None):
    args = shlex.split(command)
    if any("{prompt}" in arg or "{thread_id}" in arg for arg in args):
        args = [
            arg.replace("{prompt}", prompt).replace("{thread_id}", thread_id or "")
            for arg in args
        ]
        stdin = None
    else:
        stdin = prompt
    return args, stdin


def apply_codex_overrides(args, model=None, service_tier=None, reasoning_effort=None):
    if len(args) < 2 or args[0] != "codex" or args[1] != "exec":
        return args
    insert_at = 2
    if len(args) > 2 and args[0] == "codex" and args[1] == "exec" and args[2] == "resume":
        insert_at = 3
    overrides = []
    if model:
        overrides.extend(["--model", model])
    if service_tier:
        overrides.extend(["-c", f'service_tier="{service_tier}"'])
    if reasoning_effort:
        overrides.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    return args[:insert_at] + overrides + args[insert_at:]


def parse_codex_json_output(stdout):
    reply = ""
    thread_id = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id") or thread_id
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            reply = item.get("text") or reply
    return thread_id, reply


def progress_text_from_event(event):
    event_type = event.get("type")
    if event_type == "turn.started":
        return "Codex 已开始处理。"
    item = event.get("item") or {}
    item_type = item.get("type")
    if item_type == "agent_message":
        text = item.get("text") or ""
        if text.strip():
            return clip_text(text)
    if item_type == "command_execution":
        command = item.get("command") or ""
        status = item.get("status") or ""
        if event_type == "item.started" or status == "in_progress":
            return f"正在执行命令：\n{clip_text(command, 500)}"
        if event_type == "item.completed":
            exit_code = item.get("exit_code")
            output = clip_text(item.get("aggregated_output") or "")
            if output:
                return f"命令完成，exit={exit_code}：\n{clip_text(command, 500)}\n\n输出：\n{output}"
            return f"命令完成，exit={exit_code}：\n{clip_text(command, 500)}"
    if event_type == "item.started" and item_type:
        return f"正在处理：{item_type}"
    if event_type == "item.completed" and item_type and item_type != "agent_message":
        return f"已完成：{item_type}"
    return None


def run_codex_streaming(
    args,
    stdin,
    timeout,
    progress_callback=None,
    stream_updates=True,
):
    started_at = time.time()
    stdout_lines = []
    reply = ""
    thread_id = None
    last_progress = ""
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE if stdin is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(CODEX_CWD),
    )
    if stdin is not None and process.stdin:
        process.stdin.write(stdin)
        process.stdin.close()
    try:
        for line in process.stdout or []:
            stdout_lines.append(line)
            if time.time() - started_at > timeout:
                process.kill()
                raise TimeoutError(f"Codex command timed out after {timeout}s")
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id") or thread_id
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                reply = item.get("text") or reply
            progress = progress_text_from_event(event)
            if (
                stream_updates
                and progress_callback
                and progress
                and progress != last_progress
            ):
                progress_callback(progress)
                last_progress = progress
        return_code = process.wait(timeout=max(1, int(timeout - (time.time() - started_at))))
    except Exception:
        if process.poll() is None:
            process.kill()
        raise
    stdout = "".join(stdout_lines)
    if return_code != 0:
        raise RuntimeError(stdout.strip() or "Codex command failed")
    if not reply:
        parsed_thread_id, parsed_reply = parse_codex_json_output(stdout)
        thread_id = thread_id or parsed_thread_id
        reply = parsed_reply or extract_reply(stdout)
    return thread_id, reply


def run_codex(
    prompt,
    command,
    timeout,
    thread_id=None,
    model=None,
    service_tier=None,
    reasoning_effort=None,
    progress_callback=None,
    stream_updates=True,
):
    args, stdin = format_command(command, prompt, thread_id=thread_id)
    args = apply_codex_overrides(args, model=model, service_tier=service_tier, reasoning_effort=reasoning_effort)
    if progress_callback:
        _, reply = run_codex_streaming(
            args,
            stdin,
            timeout,
            progress_callback=progress_callback,
            stream_updates=stream_updates,
        )
        return reply
    result = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout, cwd=str(CODEX_CWD))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Codex command failed")
    return extract_reply(result.stdout)


def run_codex_thread(
    prompt,
    create_command,
    resume_command,
    timeout,
    thread_id=None,
    model=None,
    service_tier=None,
    reasoning_effort=None,
    progress_callback=None,
    stream_updates=True,
):
    command = resume_command if thread_id else create_command
    args, stdin = format_command(command, prompt, thread_id=thread_id)
    args = apply_codex_overrides(args, model=model, service_tier=service_tier, reasoning_effort=reasoning_effort)
    if progress_callback:
        streamed_thread_id, reply = run_codex_streaming(
            args,
            stdin,
            timeout,
            progress_callback=progress_callback,
            stream_updates=stream_updates,
        )
        return streamed_thread_id or thread_id, reply
    result = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout, cwd=str(CODEX_CWD))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Codex command failed")
    parsed_thread_id, reply = parse_codex_json_output(result.stdout)
    if not reply:
        reply = extract_reply(result.stdout)
    return parsed_thread_id or thread_id, reply


def send_text(account, to_user_id, context_token, text):
    client_id = f"codex-direct-{uuid.uuid4().hex}"
    payload = {
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": 2,
            "message_state": 2,
            "context_token": context_token,
            "item_list": [
                {
                    "type": 1,
                    "text_item": {"text": text},
                }
            ],
        }
    }
    api_post(account, "ilink/bot/sendmessage", payload, timeout=15)
    return client_id


def should_handle(message, seen):
    message_id = str(message.get("message_id") or message.get("client_id") or "")
    if not message_id or message_id in seen:
        return False
    if message.get("message_type") != 1:
        return False
    text = text_from_message(message)
    if not text:
        return False
    seen.add(message_id)
    save_seen(seen)
    return True


def run_loop(args):
    lock_file = acquire_lock()
    account = load_account(args.account_id)
    log(f"Weixin Codex direct bridge using account={account['account_id']} baseUrl={account['base_url']}")
    sync_buf = "" if args.reset_sync else load_sync_buf(account["account_id"])
    sessions = load_sessions()
    seen = set(load_seen())
    log(f"Loaded {len(seen)} processed Weixin message ids")
    while True:
        try:
            resp = get_updates(account, sync_buf)
            if resp.get("get_updates_buf") is not None:
                sync_buf = resp.get("get_updates_buf") or ""
                save_sync_buf(account["account_id"], sync_buf)
            if resp.get("ret", 0) != 0:
                log(f"getupdates ret={resp.get('ret')} err={resp.get('errcode')} {resp.get('errmsg')}")
                time.sleep(args.interval)
                continue
            for message in resp.get("msgs") or []:
                if not should_handle(message, seen):
                    continue
                text = text_from_message(message)
                to_user_id = message.get("from_user_id")
                context_token = message.get("context_token")
                key = session_key(message)
                log(f"inbound from={to_user_id} message_id={message.get('message_id')} text={text[:80]!r}")
                control_reply = handle_control_command(text, sessions, key)
                if control_reply:
                    reply = control_reply
                    send_id = send_text(account, to_user_id, context_token, reply)
                    log(f"sent control ack to={to_user_id} client_id={send_id} reply={reply[:80]!r}")
                    continue
                progress_messages = []
                try:
                    history = get_history(sessions, key)
                    session = sessions.setdefault(key, {})
                    thread_id = session.get("codex_thread_id") if args.native_session else None
                    model = session.get("codex_model")
                    service_tier = session.get("codex_service_tier")
                    reasoning_effort = get_session_effort(session)
                    prompt = build_prompt(text, message, history, use_native_session=args.native_session)

                    def send_progress(progress_text):
                        progress_text = clip_text(progress_text, PROGRESS_PREVIEW_CHARS)
                        progress_messages.append(progress_text)
                        try:
                            progress_send_id = send_text(account, to_user_id, context_token, progress_text)
                            log(
                                f"sent progress to={to_user_id} "
                                f"client_id={progress_send_id} reply={progress_text[:80]!r}"
                            )
                        except Exception as progress_exc:
                            log(f"progress send failed: {progress_exc}")

                    if args.native_session:
                        thread_id, reply = run_codex_thread(
                            prompt,
                            args.codex_command,
                            args.codex_resume_command,
                            args.codex_timeout,
                            thread_id=thread_id,
                            model=model,
                            service_tier=service_tier,
                            reasoning_effort=reasoning_effort,
                            progress_callback=send_progress if args.stream_updates else None,
                            stream_updates=args.stream_updates,
                        )
                        if thread_id:
                            session["codex_thread_id"] = thread_id
                            session.setdefault("codex_thread_name", thread_name_for_message(text))
                            session["updated_at"] = int(time.time())
                            save_sessions(sessions)
                            append_codex_session_index(thread_id, session["codex_thread_name"])
                    else:
                        reply = run_codex(
                            prompt,
                            args.codex_command,
                            args.codex_timeout,
                            model=model,
                            service_tier=service_tier,
                            reasoning_effort=reasoning_effort,
                            progress_callback=send_progress if args.stream_updates else None,
                            stream_updates=args.stream_updates,
                        )
                except Exception as exc:
                    reply = f"Codex 执行失败：{exc}"
                append_history(sessions, key, "user", text, args.history_turns)
                append_history(sessions, key, "assistant", reply, args.history_turns)
                final_reply = f"最终回答：\n{reply}"
                send_id = send_text(account, to_user_id, context_token, final_reply)
                log(f"sent final to={to_user_id} client_id={send_id} reply={reply[:80]!r}")
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log(f"loop error: {exc}")
            time.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser(description="Direct WeChat ClawBot to Codex bridge")
    parser.add_argument("--account-id", default=os.environ.get("WEIXIN_CODEX_ACCOUNT_ID"))
    parser.add_argument("--codex-command", default=os.environ.get("WEIXIN_CODEX_COMMAND", DEFAULT_CODEX_COMMAND))
    parser.add_argument("--codex-resume-command", default=os.environ.get("WEIXIN_CODEX_RESUME_COMMAND", DEFAULT_CODEX_RESUME_COMMAND))
    parser.add_argument("--codex-timeout", type=int, default=int(os.environ.get("WEIXIN_CODEX_TIMEOUT", "180")))
    parser.add_argument("--history-turns", type=int, default=int(os.environ.get("WEIXIN_CODEX_HISTORY_TURNS", "20")))
    parser.add_argument("--native-session", action=argparse.BooleanOptionalAction, default=os.environ.get("WEIXIN_CODEX_NATIVE_SESSION", "true").lower() not in ("0", "false", "no", "off"))
    parser.add_argument("--stream-updates", action=argparse.BooleanOptionalAction, default=os.environ.get("WEIXIN_CODEX_STREAM_UPDATES", "true").lower() not in ("0", "false", "no", "off"))
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--reset-sync", action="store_true")
    args = parser.parse_args()
    run_loop(args)


if __name__ == "__main__":
    main()
