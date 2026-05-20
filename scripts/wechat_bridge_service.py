#!/usr/bin/env python3
import json
import os
import hashlib
import re
import shlex
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONTACTS_PATH = DATA_DIR / "contacts.json"
STORE_PATH = DATA_DIR / "store.json"
CAPTURE_DIR = DATA_DIR / "captures"
HOST = os.environ.get("WECHAT_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("WECHAT_BRIDGE_PORT", "8787"))
TOKEN = os.environ.get("WECHAT_BRIDGE_TOKEN", "")
PUBLIC_WECHAT_TOKEN = os.environ.get("WECHAT_PUBLIC_TOKEN", "")
AGENT_COMMAND = os.environ.get("WECHAT_AGENT_COMMAND", "")
AGENT_TIMEOUT = int(os.environ.get("WECHAT_AGENT_TIMEOUT", "120"))
BOT_ASYNC_DEFAULT = os.environ.get("WECHAT_BOT_ASYNC", "false").lower() in ("1", "true", "yes", "on")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure_data_files():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    if not CONTACTS_PATH.exists():
        CONTACTS_PATH.write_text(
            json.dumps(
                [
                    {
                        "chat_id": "filehelper",
                        "display_name": "文件传输助手",
                        "search_term": "文件传输助手",
                    }
                ],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if not STORE_PATH.exists():
        STORE_PATH.write_text(
            json.dumps(
                {"messages": {}, "recent_chat_ids": [], "outbox": [], "jobs": {}, "seen_message_ids": []},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_contacts():
    contacts = load_json(CONTACTS_PATH, [])
    by_id = {}
    for item in contacts:
        if isinstance(item, dict) and item.get("chat_id"):
            by_id[item["chat_id"]] = item
    return by_id


def save_contacts(contacts_by_id):
    save_json(CONTACTS_PATH, list(contacts_by_id.values()))


def load_store():
    return load_json(STORE_PATH, {"messages": {}, "recent_chat_ids": [], "outbox": [], "jobs": {}, "seen_message_ids": []})


def save_store(store):
    save_json(STORE_PATH, store)


def require_token(headers):
    if not TOKEN:
        return True
    auth = headers.get("Authorization", "")
    return auth == f"Bearer {TOKEN}"


def append_message(chat_id, message):
    store = load_store()
    store.setdefault("messages", {})
    store.setdefault("recent_chat_ids", [])
    store["messages"].setdefault(chat_id, [])
    store["messages"][chat_id].append(message)
    store["recent_chat_ids"] = [x for x in store["recent_chat_ids"] if x != chat_id]
    store["recent_chat_ids"].insert(0, chat_id)
    save_store(store)
    return message


def append_outbox(chat_id, message):
    store = load_store()
    store.setdefault("outbox", [])
    outbox_item = {"chat_id": chat_id, "sent": False, "sent_at": None, **message}
    store["outbox"].append(outbox_item)
    save_store(store)
    return outbox_item


def is_duplicate_message(external_id):
    if not external_id:
        return False
    store = load_store()
    seen = store.setdefault("seen_message_ids", [])
    if external_id in seen:
        return True
    seen.append(external_id)
    store["seen_message_ids"] = seen[-1000:]
    save_store(store)
    return False


def upsert_job(job_id, payload):
    store = load_store()
    store.setdefault("jobs", {})
    current = store["jobs"].get(job_id, {})
    current.update(payload)
    store["jobs"][job_id] = current
    save_store(store)
    return current


def fetch_job(job_id):
    store = load_store()
    return store.get("jobs", {}).get(job_id)


def list_chats(limit=20):
    contacts = load_contacts()
    store = load_store()
    recent_ids = store.get("recent_chat_ids", [])
    ordered_ids = recent_ids + [chat_id for chat_id in contacts if chat_id not in recent_ids]
    chats = []
    for chat_id in ordered_ids[:limit]:
        contact = contacts.get(chat_id, {"chat_id": chat_id, "display_name": chat_id, "search_term": chat_id})
        message_count = len(store.get("messages", {}).get(chat_id, []))
        chats.append(
            {
                "chat_id": chat_id,
                "display_name": contact.get("display_name", chat_id),
                "search_term": contact.get("search_term", contact.get("display_name", chat_id)),
                "message_count": message_count,
            }
        )
    return chats


def fetch_messages(chat_id=None, limit=20, since=None):
    store = load_store()
    messages = []
    if chat_id:
        messages = list(store.get("messages", {}).get(chat_id, []))
    else:
        for cid in store.get("recent_chat_ids", []):
            messages.extend(store.get("messages", {}).get(cid, []))
    if since:
        messages = [m for m in messages if m.get("created_at", "") > since]
    messages = sorted(messages, key=lambda item: item.get("created_at", ""), reverse=True)
    return messages[:limit]


def fetch_outbox(chat_id=None, limit=20):
    store = load_store()
    messages = list(store.get("outbox", []))
    if chat_id:
        messages = [m for m in messages if m.get("chat_id") == chat_id]
    messages = sorted(messages, key=lambda item: item.get("created_at", ""), reverse=True)
    return messages[:limit]


def ack_outbox(message_id, sent_by=None):
    store = load_store()
    updated = None
    for message in store.get("outbox", []):
        if message.get("id") == message_id:
            message["sent"] = True
            message["sent_at"] = now_iso()
            if sent_by:
                message["sent_by"] = sent_by
            updated = message
            break
    if not updated:
        raise RuntimeError(f"Unknown outbox message id: {message_id}")
    save_store(store)
    return updated


def pbcopy_text(text):
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def pbpaste_text():
    result = subprocess.run(["pbpaste"], capture_output=True, text=True, check=True)
    return result.stdout


def run_cmd(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Command failed")
    return result.stdout.strip()


def extract_agent_reply(stdout):
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


def build_agent_prompt(text, sender=None, chat_id=None):
    context = {
        "channel": "wechat",
        "from": sender or chat_id or "unknown",
        "chat_id": chat_id or sender or "unknown",
        "message": text,
    }
    return (
        "You are replying to a WeChat message. Keep the reply concise and useful.\n"
        f"Context JSON:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def run_agent_turn(text, sender=None, chat_id=None):
    prompt = build_agent_prompt(text, sender=sender, chat_id=chat_id)
    if not AGENT_COMMAND:
        return f"[mock agent] 收到：{text}"

    args = shlex.split(AGENT_COMMAND)
    if any("{prompt}" in arg or "{message}" in arg or "{from}" in arg or "{chat_id}" in arg for arg in args):
        args = [
            arg.replace("{prompt}", prompt)
            .replace("{message}", text)
            .replace("{from}", sender or "")
            .replace("{chat_id}", chat_id or "")
            for arg in args
        ]
        stdin = None
    else:
        stdin = prompt

    result = subprocess.run(
        args,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=AGENT_TIMEOUT,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Agent command failed")
    return extract_agent_reply(result.stdout)


def handle_inbound_text(text, sender=None, chat_id=None, source="json_webhook", external_id=None):
    resolved_chat_id = chat_id or sender or "wechat"
    inbound = {
        "id": external_id or str(uuid.uuid4()),
        "chat_id": resolved_chat_id,
        "direction": "inbound",
        "source": source,
        "from": sender,
        "content": text,
        "created_at": now_iso(),
    }
    append_message(resolved_chat_id, inbound)

    reply = run_agent_turn(text, sender=sender, chat_id=resolved_chat_id)
    outbound = {
        "id": str(uuid.uuid4()),
        "direction": "outbound",
        "source": "agent",
        "to": sender,
        "content": reply,
        "created_at": now_iso(),
    }
    append_message(resolved_chat_id, {**outbound, "chat_id": resolved_chat_id})
    append_outbox(resolved_chat_id, outbound)
    return {"ok": True, "chat_id": resolved_chat_id, "inbound": inbound, "reply": reply, "outbound": outbound}


def process_inbound_job(job_id, text, sender=None, chat_id=None, source="json_webhook", external_id=None):
    upsert_job(job_id, {"status": "running", "started_at": now_iso()})
    try:
        result = handle_inbound_text(text, sender=sender, chat_id=chat_id, source=source, external_id=external_id)
        upsert_job(job_id, {"status": "completed", "completed_at": now_iso(), "result": result})
    except Exception as exc:
        upsert_job(job_id, {"status": "failed", "completed_at": now_iso(), "error": str(exc)})


def enqueue_inbound_text(text, sender=None, chat_id=None, source="json_webhook", external_id=None):
    job_id = str(uuid.uuid4())
    resolved_chat_id = chat_id or sender or "wechat"
    job = upsert_job(
        job_id,
        {
            "id": job_id,
            "status": "queued",
            "chat_id": resolved_chat_id,
            "from": sender,
            "source": source,
            "text": text,
            "created_at": now_iso(),
        },
    )
    worker = threading.Thread(
        target=process_inbound_job,
        args=(job_id, text),
        kwargs={"sender": sender, "chat_id": chat_id, "source": source, "external_id": external_id},
        daemon=True,
    )
    worker.start()
    return job


def verify_public_wechat_signature(query):
    if not PUBLIC_WECHAT_TOKEN:
        return True
    signature = query.get("signature", [""])[0]
    timestamp = query.get("timestamp", [""])[0]
    nonce = query.get("nonce", [""])[0]
    raw = "".join(sorted([PUBLIC_WECHAT_TOKEN, timestamp, nonce]))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return digest == signature


def parse_wechat_xml(raw):
    payload = {}
    for key in ("ToUserName", "FromUserName", "CreateTime", "MsgType", "Content", "MsgId"):
        match = re.search(rf"<{key}>(.*?)</{key}>", raw, re.DOTALL)
        if not match:
            continue
        value = match.group(1).strip()
        if value.startswith("<![CDATA[") and value.endswith("]]>"):
            value = value[9:-3]
        payload[key] = value
    return payload


def wechat_xml_reply(to_user, from_user, content):
    created_at = str(int(datetime.now(timezone.utc).timestamp()))
    escaped = content.replace("]]>", "]]]]><![CDATA[>")
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{created_at}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{escaped}]]></Content>"
        "</xml>"
    )


def run_osascript(lines):
    args = []
    for line in lines:
        args.extend(["-e", line])
    return run_cmd(["osascript", *args])


def active_wechat_window_info():
    raw = run_cmd(["swift", str(ROOT / "scripts" / "wechat_window_info.swift")])
    return json.loads(raw)


def capture_wechat_window():
    info = active_wechat_window_info()
    capture_path = CAPTURE_DIR / f"wechat-window-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    bounds = info["bounds"]
    rect = f'{bounds["x"]},{bounds["y"]},{bounds["width"]},{bounds["height"]}'
    try:
        run_cmd(["screencapture", "-x", "-l", str(info["window_id"]), str(capture_path)])
    except RuntimeError:
        run_cmd(["screencapture", "-x", "-R", rect, str(capture_path)])
    info["image_path"] = str(capture_path)
    return info


def ocr_image(image_path):
    raw = run_cmd(["swift", str(ROOT / "scripts" / "ocr_image.swift"), image_path])
    return json.loads(raw)


def import_capture(chat_id, display_name=None):
    capture = capture_wechat_window()
    ocr = ocr_image(capture["image_path"])
    if display_name:
        upsert_contact(chat_id, display_name, display_name)
    message = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "direction": "snapshot",
        "source": "window_capture_ocr",
        "content": ocr.get("text", "").strip(),
        "image_path": capture["image_path"],
        "window": capture,
        "ocr_lines": ocr.get("lines", []),
        "created_at": now_iso(),
    }
    append_message(chat_id, message)
    return {
        "ok": True,
        "chat_id": chat_id,
        "message_id": message["id"],
        "image_path": capture["image_path"],
        "text": message["content"],
        "ocr_line_count": len(message["ocr_lines"]),
    }


def image_ocr_only():
    capture = capture_wechat_window()
    ocr = ocr_image(capture["image_path"])
    return {
        "ok": True,
        "image_path": capture["image_path"],
        "window": capture,
        "text": ocr.get("text", "").strip(),
        "ocr_lines": ocr.get("lines", []),
    }


def send_message_via_wechat(search_term, content):
    pbcopy_text(content)
    run_osascript(
        [
            'tell application "WeChat" to activate',
            "delay 0.4",
            'tell application "System Events"',
            '  tell process "WeChat"',
            '    keystroke "f" using {command down}',
            "    delay 0.2",
            '    keystroke "a" using {command down}',
            "    key code 51",
            f'    keystroke {json.dumps(search_term, ensure_ascii=False)}',
            "    delay 0.6",
            "    key code 36",
            "    delay 0.5",
            '    keystroke "v" using {command down}',
            "    delay 0.2",
            "    key code 36",
            "  end tell",
            "end tell",
        ]
    )


def upsert_contact(chat_id, display_name, search_term):
    contacts = load_contacts()
    contacts[chat_id] = {
        "chat_id": chat_id,
        "display_name": display_name,
        "search_term": search_term or display_name,
    }
    save_contacts(contacts)
    return contacts[chat_id]


def import_clipboard(chat_id, display_name=None, text=None):
    if display_name:
        upsert_contact(chat_id, display_name, display_name)
    raw = text if text is not None else pbpaste_text()
    if not raw.strip():
        raise RuntimeError("Clipboard is empty.")
    message = {
        "id": str(uuid.uuid4()),
        "chat_id": chat_id,
        "direction": "snapshot",
        "source": "clipboard_import",
        "content": raw.strip(),
        "created_at": now_iso(),
    }
    append_message(chat_id, message)
    return {
        "ok": True,
        "chat_id": chat_id,
        "imported_chars": len(raw),
        "message_id": message["id"],
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, status, text, content_type="text/plain; charset=utf-8"):
        raw = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body_text(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8")

    def _json_body(self):
        body = self._body_text()
        return json.loads(body) if body else {}

    def _unauthorized(self):
        self._send(401, {"error": "Unauthorized"})

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/wechat/webhook":
            if not verify_public_wechat_signature(query):
                return self._send_text(403, "invalid signature")
            return self._send_text(200, query.get("echostr", [""])[0])
        if not require_token(self.headers):
            return self._unauthorized()
        try:
            if parsed.path == "/health":
                return self._send(
                    200,
                    {
                        "ok": True,
                        "service": "wechat-bridge",
                        "mode": "personal-wechat-desktop",
                        "time": now_iso(),
                    },
                )
            if parsed.path == "/chats":
                limit = int(query.get("limit", ["20"])[0])
                return self._send(200, {"chats": list_chats(limit=limit)})
            if parsed.path == "/messages":
                chat_id = query.get("chat_id", [None])[0]
                limit = int(query.get("limit", ["20"])[0])
                since = query.get("since", [None])[0]
                return self._send(200, {"messages": fetch_messages(chat_id=chat_id, limit=limit, since=since)})
            if parsed.path == "/bot/outbox":
                chat_id = query.get("chat_id", [None])[0]
                limit = int(query.get("limit", ["20"])[0])
                return self._send(200, {"messages": fetch_outbox(chat_id=chat_id, limit=limit)})
            if parsed.path.startswith("/bot/replies/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                job = fetch_job(job_id)
                if not job:
                    return self._send(404, {"error": "Job not found", "job_id": job_id})
                return self._send(200, {"ok": True, "job": job})
            if parsed.path == "/capture/active_window":
                return self._send(200, capture_wechat_window())
            if parsed.path == "/ocr/active_window":
                return self._send(200, image_ocr_only())
            return self._send(404, {"error": "Not found"})
        except Exception as exc:
            return self._send(500, {"error": str(exc)})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/wechat/webhook":
            query = parse_qs(parsed.query)
            if not verify_public_wechat_signature(query):
                return self._send_text(403, "invalid signature")
            try:
                payload = parse_wechat_xml(self._body_text())
                if payload.get("MsgType") != "text":
                    reply = "目前只支持文本消息。"
                elif is_duplicate_message(payload.get("MsgId")):
                    reply = "收到，正在处理或已处理过这条消息。"
                else:
                    result = handle_inbound_text(
                        payload.get("Content", ""),
                        sender=payload.get("FromUserName"),
                        chat_id=payload.get("FromUserName"),
                        source="wechat_public_webhook",
                        external_id=payload.get("MsgId"),
                    )
                    reply = result["reply"]
                xml = wechat_xml_reply(payload.get("FromUserName", ""), payload.get("ToUserName", ""), reply)
                return self._send_text(200, xml, content_type="application/xml; charset=utf-8")
            except Exception as exc:
                return self._send_text(500, str(exc))
        if not require_token(self.headers):
            return self._unauthorized()
        try:
            body = self._json_body()
            if parsed.path == "/bot/webhook":
                text = body.get("text") or body.get("content") or body.get("message") or ""
                sender = body.get("from") or body.get("sender")
                chat_id = body.get("chat_id") or body.get("room_id") or body.get("conversation_id")
                source = body.get("source") or "personal_wechat_bridge"
                external_id = body.get("message_id") or body.get("msg_id") or body.get("id")
                async_mode = bool(body.get("async", BOT_ASYNC_DEFAULT))
                if is_duplicate_message(external_id):
                    return self._send(200, {"ok": True, "duplicate": True, "message_id": external_id})
                if async_mode:
                    job = enqueue_inbound_text(
                        text=text,
                        sender=sender,
                        chat_id=chat_id,
                        source=source,
                        external_id=external_id,
                    )
                    return self._send(202, {"ok": True, "async": True, "job_id": job["id"], "job": job})
                result = handle_inbound_text(
                    text=text,
                    sender=sender,
                    chat_id=chat_id,
                    source=source,
                    external_id=external_id,
                )
                return self._send(200, result)
            if parsed.path == "/bot/ack":
                message = ack_outbox(body["message_id"], sent_by=body.get("sent_by"))
                return self._send(200, {"ok": True, "message": message})
            if parsed.path == "/contacts/upsert":
                contact = upsert_contact(
                    body["chat_id"],
                    body["display_name"],
                    body.get("search_term") or body["display_name"],
                )
                return self._send(200, {"ok": True, "contact": contact})
            if parsed.path == "/messages/import_clipboard":
                result = import_clipboard(
                    chat_id=body["chat_id"],
                    display_name=body.get("display_name"),
                    text=body.get("text"),
                )
                return self._send(200, result)
            if parsed.path == "/messages/import_active_window":
                result = import_capture(
                    chat_id=body["chat_id"],
                    display_name=body.get("display_name"),
                )
                return self._send(200, result)
            if parsed.path == "/messages/send":
                contacts = load_contacts()
                chat_id = body["chat_id"]
                content = body["content"]
                contact = contacts.get(chat_id)
                if not contact:
                    raise RuntimeError(
                        f"Unknown chat_id '{chat_id}'. Register it first with /contacts/upsert."
                    )
                send_message_via_wechat(contact["search_term"], content)
                message = {
                    "id": str(uuid.uuid4()),
                    "chat_id": chat_id,
                    "direction": "outbound",
                    "source": "desktop_automation",
                    "content": content,
                    "created_at": now_iso(),
                }
                append_message(chat_id, message)
                return self._send(200, {"ok": True, "message": message})
            return self._send(404, {"error": "Not found"})
        except Exception as exc:
            return self._send(500, {"error": str(exc)})

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


def main():
    ensure_data_files()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"WeChat bridge listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
