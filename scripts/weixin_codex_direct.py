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
CHANNEL_VERSION = "2.1.8"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = str((2 << 16) | (1 << 8) | 8)


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


def run_codex(prompt, command, timeout, thread_id=None):
    args, stdin = format_command(command, prompt, thread_id=thread_id)
    result = subprocess.run(
        args,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(CODEX_CWD),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Codex command failed")
    return extract_reply(result.stdout)


def run_codex_thread(prompt, create_command, resume_command, timeout, thread_id=None):
    command = resume_command if thread_id else create_command
    args, stdin = format_command(command, prompt, thread_id=thread_id)
    result = subprocess.run(
        args,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(CODEX_CWD),
    )
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
                try:
                    history = get_history(sessions, key)
                    session = sessions.setdefault(key, {})
                    thread_id = session.get("codex_thread_id") if args.native_session else None
                    prompt = build_prompt(text, message, history, use_native_session=args.native_session)
                    if args.native_session:
                        thread_id, reply = run_codex_thread(
                            prompt,
                            args.codex_command,
                            args.codex_resume_command,
                            args.codex_timeout,
                            thread_id=thread_id,
                        )
                        if thread_id:
                            session["codex_thread_id"] = thread_id
                            session.setdefault("codex_thread_name", thread_name_for_message(text))
                            session["updated_at"] = int(time.time())
                            save_sessions(sessions)
                            append_codex_session_index(thread_id, session["codex_thread_name"])
                    else:
                        reply = run_codex(prompt, args.codex_command, args.codex_timeout)
                except Exception as exc:
                    reply = f"Codex 执行失败：{exc}"
                append_history(sessions, key, "user", text, args.history_turns)
                append_history(sessions, key, "assistant", reply, args.history_turns)
                send_id = send_text(account, to_user_id, context_token, reply)
                log(f"sent to={to_user_id} client_id={send_id} reply={reply[:80]!r}")
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
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--reset-sync", action="store_true")
    args = parser.parse_args()
    run_loop(args)


if __name__ == "__main__":
    main()
