---
name: wechat-chat
description: Use this plugin when the user wants Codex to read, summarize, draft, or send WeChat messages through the configured local bridge.
---

# WeChat Chat

Use this skill when a user wants to work with WeChat conversations inside Codex Desktop.

## Workflow

1. Call `wechat_health` first to verify the bridge is online.
2. If the chat is not configured yet, call `wechat_register_chat` with a stable `chat_id` and the WeChat search name.
3. Call `wechat_list_chats` to discover the target conversation when the chat id is unknown.
4. Prefer `wechat_import_active_window` to capture the current WeChat window automatically.
5. Use `wechat_ocr_active_window` when the user wants to inspect the OCR text before importing.
6. Fall back to `wechat_import_clipboard` only when automatic capture is unreliable.
7. Call `wechat_fetch_messages` to inspect recent context.
8. Summarize, draft, or translate the reply in the user's requested style.
9. Only call `wechat_send_message` after the user clearly asks to send.

## Notes

- The plugin does not talk to WeChat directly; it talks to your local bridge.
- The bridge is expected to expose `/health`, `/chats`, `/messages`, `/capture/active_window`, `/ocr/active_window`, `/contacts/upsert`, `/messages/import_clipboard`, `/messages/import_active_window`, and `/messages/send`.
- If the bridge is down, explain that setup is incomplete and point the user to the plugin README.
