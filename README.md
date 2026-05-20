# WeChat Codex Bridge

把微信消息接入 Codex Desktop / Codex CLI 的本地桥接项目。

这个仓库提供三种集成方式：

- 桌面辅助模式：Codex Desktop 通过本地 MCP 工具读取、OCR、发送微信桌面端消息。
- Bot Channel 模式：你的微信机器人把消息 POST 到本地 bridge，bridge 调用 agent 后把结果返回给微信。
- 直连 ClawBot 模式：复用腾讯官方 WeChat ClawBot 登录和消息通道，绕过 OpenClaw Gateway，直接调用 Codex。

## 架构

Bot Channel 模式：

```text
微信机器人 / 公众号
-> 本地 bridge 服务 scripts/wechat_bridge_service.py
-> WECHAT_AGENT_COMMAND
-> 回复结果
-> 微信机器人发回同一个聊天窗口
```

桌面辅助模式：

```text
Codex Desktop
-> 插件配置
-> 本地 MCP Server scripts/wechat_bridge_mcp.py
-> 本地 bridge 服务 scripts/wechat_bridge_service.py
-> 微信桌面端自动化 + 本地 OCR
```

直连 ClawBot 模式：

```text
微信 ClawBot
-> 腾讯 ilink bot API
-> scripts/weixin_codex_direct.py
-> codex exec / codex exec resume
-> ilink/bot/sendmessage
-> 微信 ClawBot 聊天窗口
```

## 仓库包含什么

- `.codex-plugin/plugin.json`：Codex 插件 manifest。
- `.mcp.json`：本地 MCP Server 配置。
- `scripts/wechat_bridge_mcp.py`：无第三方依赖的 Python MCP server。
- `scripts/wechat_bridge_service.py`：无第三方依赖的本地 HTTP bridge。
- `scripts/weixin_codex_direct.py`：微信 ClawBot 直连 Codex 的轮询和回复循环。
- `scripts/wechat_window_info.swift`：macOS 微信窗口识别辅助脚本。
- `scripts/ocr_image.swift`：基于 Apple Vision 的 OCR 辅助脚本。
- `skills/wechat-chat/SKILL.md`：告诉 Codex 如何使用这些微信 bridge 工具的 skill。
- `start_wechat_bridge.sh`：启动本地 HTTP bridge。
- `start_weixin_codex_direct.sh`：启动直连 ClawBot -> Codex bridge。
- `OPENCLAW_STYLE_BOT.md`：类 OpenClaw 的微信 bot 架构说明。
- `DIRECT_WEIXIN_CODEX.md`：直连微信 ClawBot 到 Codex 的使用说明。

## 当前能力

- 注册需要处理的微信聊天对象。
- 自动截取当前微信窗口。
- 使用 Apple Vision OCR 读取截图文字。
- 在 OCR 不够稳定时，从剪贴板导入手动复制的微信聊天内容。
- 让 Codex 总结聊天、起草回复。
- 通过 macOS 桌面自动化把文本发回微信。
- 接收 `POST /bot/webhook` 的 JSON bot 消息。
- 兼容 `GET|POST /wechat/webhook` 的微信公众号明文回调。
- 通过 `WECHAT_AGENT_COMMAND` 调用可替换的后端 agent。
- 直接轮询官方 WeChat ClawBot 消息，并通过 `codex exec` / `codex exec resume` 回复。

## 推荐模式：直连 ClawBot 到 Codex

如果你想实现：

```text
微信 ClawBot -> Codex
```

而不是：

```text
微信 ClawBot -> OpenClaw Gateway -> Codex
```

请看 [DIRECT_WEIXIN_CODEX.md](DIRECT_WEIXIN_CODEX.md)。

这个模式会为每个微信用户绑定一个 Codex thread id。第一条消息创建 Codex 会话，后续消息使用 `codex exec resume` 继续同一个会话，避免 Codex 很快忘记上下文。

## 桌面辅助模式首次使用

1. 在这台 Mac 上打开微信，并保持登录。
2. 给运行 bridge 或 Codex 的 App 授予 macOS Accessibility 权限。
3. 启动本地 bridge 服务：

```bash
cd /path/to/wechat-codex-bridge
./start_wechat_bridge.sh
```

4. 在 Codex 中调用 `wechat_health` 确认 bridge 正常。
5. 注册一个聊天对象：

```json
{
  "chat_id": "alice",
  "display_name": "Alice",
  "search_term": "Alice"
}
```

6. 在微信中把目标聊天窗口切到最前。
7. 优先使用 `wechat_import_active_window` 截图并 OCR 当前窗口。
8. 如果想先检查 OCR 结果，可以用 `wechat_ocr_active_window`。
9. 如果 OCR 漏字或识别不准，可以复制微信聊天内容后用 `wechat_import_clipboard`。
10. 让 Codex 总结、分析或起草回复。
11. 确认后调用 `wechat_send_message` 发回微信。

## 本地 Bridge API

本地 bridge 默认监听：

```text
http://127.0.0.1:8787
```

支持的接口：

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

个人微信 bot adapter 调用 `POST /bot/webhook` 的示例：

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

发送消息示例：

```json
{
  "chat_id": "filehelper",
  "content": "你好，这是从 Codex 发来的消息。"
}
```

导入剪贴板示例：

```json
{
  "chat_id": "alice"
}
```

导入当前微信窗口示例：

```json
{
  "chat_id": "alice"
}
```

## 环境变量

- `WECHAT_BRIDGE_BASE_URL`：本地 bridge 服务地址。
- `WECHAT_BRIDGE_HOST`：本地 bridge 监听 host。
- `WECHAT_BRIDGE_PORT`：本地 bridge 监听端口。
- `WECHAT_BRIDGE_TOKEN`：可选的 bridge bearer token。
- `WECHAT_PUBLIC_TOKEN`：微信公众号回调用于校验签名的 token。
- `WECHAT_AGENT_COMMAND`：Bot Channel 模式调用的 agent 命令。
- `WECHAT_AGENT_TIMEOUT`：agent 超时时间，默认 `120` 秒。
- `WEIXIN_CODEX_CWD`：直连 ClawBot 模式下 Codex 执行命令的工作目录。
- `WEIXIN_CODEX_COMMAND`：创建新 Codex 会话时执行的命令模板。
- `WEIXIN_CODEX_RESUME_COMMAND`：继续已有 Codex 会话时执行的命令模板。
- `WEIXIN_CODEX_NATIVE_SESSION`：是否使用 Codex 原生 session，默认开启。
- `WEIXIN_CODEX_HISTORY_TURNS`：本地 fallback 历史保留轮数，默认 `20`。

## 类 OpenClaw Bot 模式

当用户在微信里和一个 bot 聊天，希望结果回到同一个聊天窗口时，用这个模式。

先用 mock agent 启动：

```bash
cd /path/to/wechat-codex-bridge
./start_wechat_bridge.sh
```

测试入站 webhook：

```bash
curl -s -X POST http://127.0.0.1:8787/bot/webhook \
  -H 'Content-Type: application/json' \
  -d '{"from":"wxid_alice","chat_id":"wxid_alice","message_id":"demo-1","text":"ping"}'
```

如果 agent 执行时间较长，可以使用异步模式：

```bash
curl -s -X POST http://127.0.0.1:8787/bot/webhook \
  -H 'Content-Type: application/json' \
  -d '{"from":"wxid_alice","chat_id":"wxid_alice","message_id":"demo-2","text":"ping","async":true}'
```

然后轮询：

```text
GET /bot/replies/<job_id>
```

使用 Codex 作为后端 agent：

```bash
export WECHAT_AGENT_COMMAND='codex exec --cd /path/to/your/workspace --sandbox workspace-write {prompt}'
./start_wechat_bridge.sh
```

使用内置 sample agent：

```bash
export WECHAT_AGENT_COMMAND='python3 scripts/sample_agent.py'
./start_wechat_bridge.sh
```

使用 OpenClaw 作为后端 agent：

```bash
export WECHAT_AGENT_COMMAND='openclaw agent --local --message {prompt}'
./start_wechat_bridge.sh
```

更多细节见 [OPENCLAW_STYLE_BOT.md](OPENCLAW_STYLE_BOT.md)。

adapter 合约也可以参考 [examples/personal-wechat-adapter.example.json](examples/personal-wechat-adapter.example.json)。

## 消息读取方式

桌面辅助模式支持两条读取路径。

优先路径：

- bridge 截取最前方微信窗口，保存到 `data/captures/`。
- Apple Vision OCR 提取截图文字。
- OCR 结果写入 `data/store.json`。

备用路径：

- 你从微信复制相关聊天内容。
- bridge 从剪贴板读取内容并写入 `data/store.json`。

之后 Codex 读取导入的聊天快照，进行总结或起草回复；最终由 bridge 通过桌面自动化发送回微信。

## 安全与隐私

- `data/` 目录不会提交到 git，里面可能包含本地消息、日志、锁文件和会话状态。
- 不要把微信账号 token、OpenClaw account json、Codex 凭据提交到仓库。
- 如果 bridge 不只监听 localhost，请启用 `WECHAT_BRIDGE_TOKEN`，并放在 HTTPS 后面。
- 让代码写入型 agent 接入微信前，建议先用 mock agent 或只读命令验证流程。

## 后续可以升级的方向

- 自动监听指定微信聊天，而不是只读取当前窗口。
- 对 OCR 结果做聊天气泡分割，减少头像、时间戳和噪声干扰。
- 为多个微信用户提供更细粒度的 Codex session 映射和管理命令。
- 增加 launchd / systemd 的长期运行配置。

## 免责声明

你需要自行确保微信自动化、bot 接入、消息读取和发送行为符合微信平台规则以及所在地法律法规。
