# 类 OpenClaw 的微信 Bot Bridge

这份文档说明如何用微信作为入口，让用户在微信里给 bot 发消息，后端 agent 执行后，再把结果发回同一个微信聊天窗口。

## OpenClaw 的基本思路

OpenClaw 把聊天应用看作 channel。一个 channel 负责接收入站聊天消息，把消息交给 Gateway 或 agent runtime，然后把 agent 的回复通过同一个 channel 发回去。

微信场景通常有两条可行路径：

- 使用已有 OpenClaw 微信 channel 插件，让 OpenClaw 接管完整 channel 生命周期。
- 使用本仓库的本地 bridge，由微信 bot 或 PC 微信自动化把消息 POST 进来，再由 bridge 调用 agent 命令并返回回复。

## 已有 OpenClaw channel 选择

调研中见到的社区插件例子：

- `@thesomewhatyou/openclaw-wechat`：基于微信公众号明文 webhook 的 channel。
- `@canghe/openclaw-wechat`：公开插件目录中出现过的社区微信 channel。

典型 OpenClaw 配置形态：

```bash
openclaw plugins install @thesomewhatyou/openclaw-wechat
openclaw plugins enable wechat
openclaw gateway restart
```

然后在微信公众号后台配置 webhook，例如 `/wechat/webhook`。

## 本仓库的 MVP bridge

当前 bridge 暴露两类 bot 入站接口：

- `POST /bot/webhook`：给个人微信 bot bridge 使用的 JSON 接口。
- `GET|POST /wechat/webhook`：兼容微信公众号明文 webhook。
- `GET /bot/replies/{job_id}`：异步任务查询接口，适合不能长时间等待的 adapter。
- `POST /bot/ack`：adapter 成功发出消息后，用于确认 outbox 消息已发送。

两条路径内部走同一套流程：

```text
微信消息
-> bot adapter 或公众号 webhook
-> wechat_bridge_service.py
-> WECHAT_AGENT_COMMAND
-> JSON 或 XML 回复
-> 微信 bot adapter 发回微信
```

## 个人微信 bot JSON 合约

请求示例：

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

同步响应示例：

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

任何个人微信 bridge 都可以按这个流程接入：

1. 监听微信入站消息。
2. 把消息 POST 到 `http://127.0.0.1:8787/bot/webhook`。
3. 从 JSON 响应里取 `reply`。
4. 把 `reply` 发回同一个微信聊天。
5. 发送成功后 POST `/bot/ack`。

异步请求示例：

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

异步响应示例：

```json
{
  "ok": true,
  "async": true,
  "job_id": "job-uuid"
}
```

轮询结果：

```bash
curl -s http://127.0.0.1:8787/bot/replies/job-uuid
```

当 `job.status` 为 `completed` 时，把 `job.result.reply` 发回微信。

adapter 字段映射可以参考：

```text
examples/personal-wechat-adapter.example.json
```

## Agent 命令

默认情况下，bridge 使用 mock agent，方便你立刻测试 webhook。

通过 `WECHAT_AGENT_COMMAND` 可以接入真实后端 agent。

使用内置 sample agent：

```bash
export WECHAT_AGENT_COMMAND='python3 scripts/sample_agent.py'
./start_wechat_bridge.sh
```

使用 Codex：

```bash
export WECHAT_AGENT_COMMAND='codex exec --cd /path/to/your/workspace --sandbox workspace-write {prompt}'
./start_wechat_bridge.sh
```

使用 OpenClaw local agent：

```bash
export WECHAT_AGENT_COMMAND='openclaw agent --local --message {prompt}'
./start_wechat_bridge.sh
```

使用 OpenClaw Gateway：

```bash
export WECHAT_AGENT_COMMAND='openclaw agent --message {prompt} --json'
./start_wechat_bridge.sh
```

如果命令里没有 `{prompt}`，bridge 会把 prompt 通过 stdin 传给 agent。

## 微信公众号 webhook

设置 token 并启动 bridge：

```bash
export WECHAT_PUBLIC_TOKEN='your-wechat-token'
./start_wechat_bridge.sh
```

在微信公众号后台配置服务器 URL：

```text
https://your-public-host/wechat/webhook
```

当前公众号 route 支持明文文本消息。如果 agent 执行时间超过微信被动回复窗口，需要改成异步客服消息 API，或者使用 JSON personal bot route。

## 安全建议

- 如果 endpoint 不只监听 localhost，请开启 `WECHAT_BRIDGE_TOKEN`。
- 不要在没有 HTTPS 和认证的情况下把 bridge 直接暴露到公网。
- 接入会写代码、执行命令的 agent 前，先用 mock agent 或低风险命令验证消息链路。
- 尽量传入微信原生 `message_id`，bridge 会用它做重复消息抑制。
