# OpenClaw-style WeChat Bot Bridge

This file describes the corrected architecture for using WeChat as the entry point.

## What OpenClaw does

OpenClaw treats chat apps as channels. A channel receives an inbound chat message, passes it to the Gateway or agent runtime, then sends the agent reply back through the same chat channel.

For WeChat, there are two practical routes:

- Existing OpenClaw channel plugin: install a community WeChat plugin and let OpenClaw own the full channel lifecycle.
- Local bridge: use a WeChat bot or PC-WeChat bridge to POST messages into this service, then this service runs an agent command and returns the reply.

## Existing OpenClaw channel options

Community plugin examples found during research:

- `@thesomewhatyou/openclaw-wechat`: WeChat Official Account channel using plaintext webhooks.
- `@canghe/openclaw-wechat`: Community WeChat channel plugin listed in public plugin directories.

Typical OpenClaw setup shape:

```bash
openclaw plugins install @thesomewhatyou/openclaw-wechat
openclaw plugins enable wechat
openclaw gateway restart
```

Then configure a WeChat Official Account webhook path such as `/wechat/webhook`.

## This repo's MVP bridge

The current bridge exposes two bot-style inbound endpoints:

- `POST /bot/webhook`: JSON endpoint for a personal WeChat bot bridge.
- `GET|POST /wechat/webhook`: WeChat Official Account plaintext webhook compatibility.
- `GET /bot/replies/{job_id}`: Async job lookup for adapters that cannot wait.
- `POST /bot/ack`: Mark an outbox reply as sent by the adapter.

Both paths call the same internal flow:

```text
WeChat message
-> bot adapter or Official Account webhook
-> wechat_bridge_service.py
-> WECHAT_AGENT_COMMAND
-> JSON or XML reply
-> WeChat bot adapter sends reply back
```

## JSON personal bot contract

Request:

```json
{
  "from": "wxid_alice",
  "chat_id": "wxid_alice",
  "message_id": "wechat-message-id",
  "text": "帮我总结一下当前项目状态",
  "source": "personal_wechat_bridge",
  "async": false
}
```

Response:

```json
{
  "ok": true,
  "chat_id": "wxid_alice",
  "reply": "这里是 agent 返回的结果。",
  "outbound": {
    "direction": "outbound",
    "source": "agent",
    "content": "这里是 agent 返回的结果。"
  }
}
```

Any personal WeChat bridge can use this by:

1. Listening for incoming WeChat messages.
2. POSTing the message to `http://127.0.0.1:8787/bot/webhook`.
3. Taking `reply` from the JSON response.
4. Sending that text back to the same WeChat chat.
5. POSTing `/bot/ack` after the send succeeds.

Async request:

```json
{
  "from": "wxid_alice",
  "chat_id": "wxid_alice",
  "message_id": "wechat-message-id",
  "text": "帮我跑一下测试",
  "source": "personal_wechat_bridge",
  "async": true
}
```

Async response:

```json
{
  "ok": true,
  "async": true,
  "job_id": "job-uuid"
}
```

Poll:

```bash
curl -s http://127.0.0.1:8787/bot/replies/job-uuid
```

When `job.status` is `completed`, send `job.result.reply` back to WeChat.

Adapter field mapping is captured in `examples/personal-wechat-adapter.example.json`.

## Agent command

By default, the bridge uses a mock agent so the webhook can be tested immediately.

Set `WECHAT_AGENT_COMMAND` to run a real backend agent.

Sample local agent:

```bash
export WECHAT_AGENT_COMMAND='python3 scripts/sample_agent.py'
./start_wechat_bridge.sh
```

Codex example:

```bash
export WECHAT_AGENT_COMMAND='codex exec --cd /path/to/your/workspace --sandbox workspace-write {prompt}'
./start_wechat_bridge.sh
```

OpenClaw local agent example:

```bash
export WECHAT_AGENT_COMMAND='openclaw agent --local --message {prompt}'
./start_wechat_bridge.sh
```

OpenClaw Gateway example:

```bash
export WECHAT_AGENT_COMMAND='openclaw agent --message {prompt} --json'
./start_wechat_bridge.sh
```

If `{prompt}` is omitted, the bridge sends the prompt on stdin.

## Official Account webhook

Set a token and expose the service to WeChat:

```bash
export WECHAT_PUBLIC_TOKEN='your-wechat-token'
./start_wechat_bridge.sh
```

Configure WeChat server URL:

```text
https://your-public-host/wechat/webhook
```

The official account route supports plaintext text messages. If the agent takes longer than WeChat's reply window, move to async custom message API or use the JSON personal bot route.

## Safety notes

- Keep `WECHAT_BRIDGE_TOKEN` enabled if the endpoint is reachable beyond localhost.
- Do not expose this bridge directly to the internet without HTTPS and authentication.
- Start with a mock agent or a low-risk command before wiring it to a code-writing agent.
- Pass WeChat's native message id as `message_id`; the bridge uses it for duplicate suppression.
