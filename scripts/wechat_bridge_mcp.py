#!/usr/bin/env python3
import json
import os
import sys
import traceback
from urllib import error, parse, request


BASE_URL = os.environ.get("WECHAT_BRIDGE_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.environ.get("WECHAT_BRIDGE_TOKEN", "")
SERVER_NAME = "wechat-codex-bridge"
SERVER_VERSION = "0.1.0"


def send_jsonrpc(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read_jsonrpc():
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if line:
            return json.loads(line)


def auth_headers():
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return headers


def http_json(method, path, payload=None, query=None):
    url = BASE_URL + path
    if query:
        url += "?" + parse.urlencode(query)
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=auth_headers(), method=method)
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Bridge HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(
            f"Unable to reach WeChat bridge at {BASE_URL}. Start your bridge service first."
        ) from exc


def text_result(payload):
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ]
    }


def list_tools():
    return [
        {
            "name": "wechat_health",
            "description": "Check whether the local WeChat bridge service is reachable.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "wechat_list_chats",
            "description": "List recent WeChat chats available through the bridge.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100}
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "wechat_fetch_messages",
            "description": "Fetch recent messages for a chat or inbox.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "since": {
                        "type": "string",
                        "description": "Opaque cursor or timestamp understood by your bridge."
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "wechat_send_message",
            "description": "Send a text reply to a WeChat chat through the bridge.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["chat_id", "content"],
                "additionalProperties": False,
            },
        },
        {
            "name": "wechat_register_chat",
            "description": "Register or update a WeChat chat mapping used by desktop automation.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "display_name": {"type": "string"},
                    "search_term": {"type": "string"}
                },
                "required": ["chat_id", "display_name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "wechat_import_clipboard",
            "description": "Import the current clipboard text as a chat snapshot for later summarization.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "display_name": {"type": "string"},
                    "text": {"type": "string"}
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "wechat_capture_active_window",
            "description": "Capture the current WeChat window to an image file.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        },
        {
            "name": "wechat_ocr_active_window",
            "description": "Capture the current WeChat window and run OCR on it.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        },
        {
            "name": "wechat_import_active_window",
            "description": "Capture the current WeChat window, OCR it, and save the result as a chat snapshot.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "display_name": {"type": "string"}
                },
                "required": ["chat_id"],
                "additionalProperties": False
            }
        },
    ]


def call_tool(name, arguments):
    arguments = arguments or {}
    if name == "wechat_health":
        return text_result(http_json("GET", "/health"))
    if name == "wechat_list_chats":
        limit = arguments.get("limit", 20)
        return text_result(http_json("GET", "/chats", query={"limit": limit}))
    if name == "wechat_fetch_messages":
        query = {"limit": arguments.get("limit", 20)}
        if arguments.get("chat_id"):
            query["chat_id"] = arguments["chat_id"]
        if arguments.get("since"):
            query["since"] = arguments["since"]
        return text_result(http_json("GET", "/messages", query=query))
    if name == "wechat_send_message":
        payload = {
            "chat_id": arguments["chat_id"],
            "content": arguments["content"],
        }
        return text_result(http_json("POST", "/messages/send", payload=payload))
    if name == "wechat_register_chat":
        payload = {
            "chat_id": arguments["chat_id"],
            "display_name": arguments["display_name"],
            "search_term": arguments.get("search_term") or arguments["display_name"],
        }
        return text_result(http_json("POST", "/contacts/upsert", payload=payload))
    if name == "wechat_import_clipboard":
        payload = {
            "chat_id": arguments["chat_id"],
            "display_name": arguments.get("display_name"),
        }
        if arguments.get("text") is not None:
            payload["text"] = arguments["text"]
        return text_result(http_json("POST", "/messages/import_clipboard", payload=payload))
    if name == "wechat_capture_active_window":
        return text_result(http_json("GET", "/capture/active_window"))
    if name == "wechat_ocr_active_window":
        return text_result(http_json("GET", "/ocr/active_window"))
    if name == "wechat_import_active_window":
        payload = {
            "chat_id": arguments["chat_id"],
            "display_name": arguments.get("display_name"),
        }
        return text_result(http_json("POST", "/messages/import_active_window", payload=payload))
    raise RuntimeError(f"Unknown tool: {name}")


def handle_request(req):
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        send_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                    "capabilities": {
                        "tools": {}
                    },
                },
            }
        )
        return

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        send_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": list_tools()
                },
            }
        )
        return

    if method == "tools/call":
        params = req.get("params", {})
        result = call_tool(params.get("name"), params.get("arguments"))
        send_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result,
            }
        )
        return

    send_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        }
    )


def main():
    while True:
        try:
            req = read_jsonrpc()
            if req is None:
                break
            handle_request(req)
        except Exception as exc:
            req_id = None
            if "req" in locals() and isinstance(req, dict):
                req_id = req.get("id")
            send_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32000,
                        "message": str(exc),
                        "data": traceback.format_exc(limit=3),
                    },
                }
            )


if __name__ == "__main__":
    main()
