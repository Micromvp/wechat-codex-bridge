# Direct WeChat ClawBot to Codex

This mode bypasses OpenClaw Gateway for agent execution.

It still uses Tencent's official WeChat ClawBot login and message transport, but the local loop is:

```text
WeChat ClawBot
-> Tencent ilink bot API
-> scripts/weixin_codex_direct.py
-> codex exec
-> ilink/bot/sendmessage
-> WeChat ClawBot conversation
```

## Prerequisites

1. Install and authorize the official WeChat channel once:

```bash
npx -y @tencent-weixin/openclaw-weixin-cli install
openclaw channels login --channel openclaw-weixin
```

2. Confirm an account file exists:

```bash
ls ~/.openclaw/openclaw-weixin/accounts/*.json
```

## Avoid duplicate consumers

If OpenClaw Gateway is also running the `openclaw-weixin` channel, it may consume the same messages.

For a pure direct Codex path, disable the OpenClaw plugin before starting this bridge:

```bash
openclaw config set plugins.entries.openclaw-weixin.enabled false
openclaw gateway restart
```

You can re-enable it later:

```bash
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw gateway restart
```

## Start

```bash
cd /path/to/wechat-codex-bridge
./start_weixin_codex_direct.sh
```

Or run it in a detached screen session:

```bash
screen -dmS weixin-codex-direct /bin/bash -lc 'cd /path/to/wechat-codex-bridge && ./start_weixin_codex_direct.sh'
```

## Custom Codex command

```bash
export WEIXIN_CODEX_COMMAND='codex exec --cd /path/to/your/workspace --sandbox workspace-write {prompt}'
./start_weixin_codex_direct.sh
```

## Conversation memory

The direct bridge keeps a per-WeChat-user Codex thread id and fallback conversation history in:

```text
/path/to/wechat-codex-bridge/data/weixin-codex-sessions.json
```

By default, each WeChat user is bound to one native Codex session id. The first message creates a Codex thread, and later messages use:

```bash
codex exec resume --json <thread_id> <prompt>
```

It also keeps the last 20 turns as a fallback/debug history:

```bash
export WEIXIN_CODEX_NATIVE_SESSION=true
export WEIXIN_CODEX_HISTORY_TURNS=20
./start_weixin_codex_direct.sh
```

From WeChat, send `/reset` or `清空上下文` to clear your current ClawBot user's Codex session binding.

## Stop

```bash
screen -S weixin-codex-direct -X quit
```

## Notes

- The script handles text messages and voice messages that include text transcription.
- It sends text replies only.
- It reuses `context_token` from inbound messages when replying, which is required by the WeChat backend.
- It stores the long-poll cursor in `~/.openclaw/openclaw-weixin/accounts/<account>.sync.json`.
