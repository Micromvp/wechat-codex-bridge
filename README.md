# WeChat Codex Bridge

This project now supports two WeChat integration modes:

- Desktop assist mode: Codex Desktop reads or sends through local WeChat desktop automation.
- Bot channel mode: WeChat messages enter through a bot webhook, trigger an agent, and return a reply to the WeChat chat.
- Direct ClawBot mode: Tencent's official WeChat ClawBot transport talks directly to Codex, bypassing OpenClaw Gateway for agent execution.

## Architecture

Bot channel mode:

WeChat bot or Official Account
-> local bridge service (`scripts/wechat_bridge_service.py`)
-> `WECHAT_AGENT_COMMAND`
-> reply payload
-> WeChat bot sends reply back

Desktop assist mode:

Codex Desktop
-> plugin manifest
-> local MCP server (`scripts/wechat_bridge_mcp.py`)
-> local bridge service (`scripts/wechat_bridge_service.py`)
-> WeChat desktop automation plus local OCR imports

This design keeps the Codex plugin small and lets you swap the WeChat implementation later.

## What is included

- A Codex plugin manifest in `.codex-plugin/plugin.json`
- A local MCP server config in `.mcp.json`
- A dependency-free Python MCP server in `scripts/wechat_bridge_mcp.py`
- A dependency-free local HTTP bridge in `scripts/wechat_bridge_service.py`
- A macOS window-inspection helper in `scripts/wechat_window_info.swift`
- A macOS Vision OCR helper in `scripts/ocr_image.swift`
- A skill that teaches Codex how to use the bridge tools
- Local data files in `data/`
- A one-command launcher in `start_wechat_bridge.sh`
- An OpenClaw-style bot architecture note in `OPENCLAW_STYLE_BOT.md`
- A direct ClawBot-to-Codex loop in `DIRECT_WEIXIN_CODEX.md`

## What this personal bridge can do today

- Register chats you want to work with
- Capture the current WeChat window automatically
- OCR the captured screenshot with Apple's Vision framework
- Import copied WeChat conversation text from the clipboard when needed
- Let Codex summarize or draft replies from those imported snapshots
- Send text messages back to WeChat through macOS desktop automation
- Receive inbound JSON bot webhooks at `POST /bot/webhook`
- Receive plaintext WeChat Official Account callbacks at `GET|POST /wechat/webhook`
- Run a configurable backend agent with `WECHAT_AGENT_COMMAND`
- Poll official WeChat ClawBot messages directly and answer with `codex exec`

## Direct ClawBot to Codex mode

Use this when you want:

```text
微信 ClawBot -> Codex
```

instead of:

```text
微信 ClawBot -> OpenClaw Gateway -> Codex
```

See `DIRECT_WEIXIN_CODEX.md`.

## Before first use

1. Open WeChat on this Mac and keep it logged in.
2. Grant Accessibility permission to the app that will run the bridge or Codex.
3. Start the local bridge service:

```bash
cd /path/to/wechat-codex-bridge
./start_wechat_bridge.sh
```

4. In Codex, use `wechat_health` to confirm the bridge is up.
5. Register a chat:

```json
{
  "chat_id": "alice",
  "display_name": "Alice",
  "search_term": "Alice"
}
```

6. In WeChat, bring the target conversation to the front.
7. Prefer `wechat_import_active_window` to capture and OCR the live window.
8. Use `wechat_ocr_active_window` if you want to inspect the extracted text first.
9. Fall back to `wechat_import_clipboard` when OCR misses content.
10. Ask Codex to summarize or draft a reply.
11. When ready, call `wechat_send_message`.

## Expected bridge API

The local bridge should listen at `WECHAT_BRIDGE_BASE_URL`, which defaults to:

```text
http://127.0.0.1:8787
```

Supported endpoints:

- `GET /health`
- `GET /chats?limit=20`
- `GET /messages?chat_id=<id>&limit=20&since=<cursor>`
- `GET /bot/outbox?chat_id=<id>&limit=20`
- `GET /bot/replies/<job_id>`
- `GET /capture/active_window`
- `GET /ocr/active_window`
- `GET|POST /wechat/webhook`
- `POST /bot/webhook`
- `POST /bot/ack`
- `POST /contacts/upsert`
- `POST /messages/import_clipboard`
- `POST /messages/import_active_window`
- `POST /messages/send`

Example `POST /bot/webhook` body for a personal WeChat bot adapter:

```json
{
  "from": "wxid_alice",
  "chat_id": "wxid_alice",
  "message_id": "wechat-message-id",
  "text": "帮我看一下今天有什么要处理的",
  "source": "personal_wechat_bridge",
  "async": false
}
```

Example `POST /messages/send` body:

```json
{
  "chat_id": "filehelper",
  "content": "你好，这是从 Codex 发来的消息。"
}
```

Example `POST /messages/import_clipboard` body:

```json
{
  "chat_id": "alice"
}
```

Example `POST /messages/import_active_window` body:

```json
{
  "chat_id": "alice"
}
```

## Environment variables

- `WECHAT_BRIDGE_BASE_URL`: Base URL for your local bridge service
- `WECHAT_BRIDGE_HOST`: Host for the local bridge service
- `WECHAT_BRIDGE_PORT`: Port for the local bridge service
- `WECHAT_BRIDGE_TOKEN`: Optional bearer token for bridge auth
- `WECHAT_PUBLIC_TOKEN`: Token used to verify WeChat Official Account callbacks
- `WECHAT_AGENT_COMMAND`: Agent command used by bot channel mode
- `WECHAT_AGENT_TIMEOUT`: Agent timeout in seconds, default `120`

## OpenClaw-style bot mode

This is the mode you want when the user chats with a bot inside WeChat and gets the result back in the same chat.

Start with the mock agent:

```bash
cd /path/to/wechat-codex-bridge
./start_wechat_bridge.sh
```

Test the inbound webhook:

```bash
curl -s -X POST http://127.0.0.1:8787/bot/webhook \
  -H 'Content-Type: application/json' \
  -d '{"from":"wxid_alice","chat_id":"wxid_alice","message_id":"demo-1","text":"ping"}'
```

Use async mode when your agent may take longer:

```bash
curl -s -X POST http://127.0.0.1:8787/bot/webhook \
  -H 'Content-Type: application/json' \
  -d '{"from":"wxid_alice","chat_id":"wxid_alice","message_id":"demo-2","text":"ping","async":true}'
```

Then poll `GET /bot/replies/<job_id>`.

Use Codex as the backend agent:

```bash
export WECHAT_AGENT_COMMAND='codex exec --cd /path/to/your/workspace --sandbox workspace-write {prompt}'
./start_wechat_bridge.sh
```

Use the included sample agent:

```bash
export WECHAT_AGENT_COMMAND='python3 scripts/sample_agent.py'
./start_wechat_bridge.sh
```

Use OpenClaw as the backend agent:

```bash
export WECHAT_AGENT_COMMAND='openclaw agent --local --message {prompt}'
./start_wechat_bridge.sh
```

More detail is in `OPENCLAW_STYLE_BOT.md`.

The adapter contract is also available as JSON in `examples/personal-wechat-adapter.example.json`.

## How message reading works

This personal version now supports two reading paths:

- Preferred path:
- The bridge captures the frontmost WeChat window into `data/captures/`
- Apple's Vision OCR extracts text from the screenshot
- The OCR result is stored in `data/store.json`

- Fallback path:
- You copy relevant chat content from WeChat
- The bridge stores that clipboard snapshot in `data/store.json`

- Codex reads the imported snapshot and drafts the reply
- The bridge sends the final text back through desktop automation

This keeps the setup lightweight and avoids a brittle full UI tree scraper.

## Suggested next step

If you want, the next upgrade can be one of these:

- Add automatic conversation capture from the active WeChat window
- Add chat-bubble segmentation so OCR reads only the message pane
- Add a polling watcher that syncs a small set of chats automatically

## Privacy

Message data stays within Codex Desktop and whatever bridge service you configure.

## Terms

You are responsible for complying with WeChat platform rules and local law when automating message access or sending.
