# 直连微信 ClawBot 到 Codex

这个模式会绕过 OpenClaw Gateway 的 agent 执行层。

它仍然复用腾讯官方 WeChat ClawBot 的登录和消息通道，但本地执行链路变成：

```text
微信 ClawBot
-> 腾讯 ilink bot API
-> scripts/weixin_codex_direct.py
-> codex exec / codex exec resume
-> ilink/bot/sendmessage
-> 微信 ClawBot 聊天窗口
```

## 前置条件

1. 安装并授权官方微信 channel：

```bash
npx -y @tencent-weixin/openclaw-weixin-cli install
openclaw channels login --channel openclaw-weixin
```

2. 确认本地存在账号文件：

```bash
ls ~/.openclaw/openclaw-weixin/accounts/*.json
```

`scripts/weixin_codex_direct.py` 会从这个账号文件读取 `token` 和 `baseUrl`，直接调用腾讯 ilink bot API。

## 避免重复消费消息

如果 OpenClaw Gateway 同时启用了 `openclaw-weixin` channel，它可能会和本 bridge 同时消费同一批微信消息，导致重复回复。

如果你希望走纯直连 Codex 路径，启动本 bridge 前建议禁用 OpenClaw 里的微信插件：

```bash
openclaw config set plugins.entries.openclaw-weixin.enabled false
openclaw gateway restart
```

需要恢复 OpenClaw 微信 channel 时再打开：

```bash
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw gateway restart
```

## 启动

```bash
cd /path/to/wechat-codex-bridge
./start_weixin_codex_direct.sh
```

后台启动可以用 `screen`：

```bash
screen -dmS weixin-codex-direct /bin/bash -lc 'cd /path/to/wechat-codex-bridge && ./start_weixin_codex_direct.sh'
```

查看日志：

```bash
tail -f data/weixin-codex-direct.log
```

停止：

```bash
screen -S weixin-codex-direct -X quit
```

## 自定义 Codex 工作目录

默认情况下，Codex 在当前仓库目录中执行。你可以通过 `WEIXIN_CODEX_CWD` 指定实际项目目录：

```bash
export WEIXIN_CODEX_CWD='/path/to/your/workspace'
./start_weixin_codex_direct.sh
```

也可以完全覆盖创建新会话的命令：

```bash
export WEIXIN_CODEX_COMMAND='codex exec --json --cd /path/to/your/workspace --sandbox workspace-write {prompt}'
./start_weixin_codex_direct.sh
```

## 会话记忆

直连 bridge 会为每个微信用户保存一个 Codex thread id，以及一份 fallback 聊天历史：

```text
data/weixin-codex-sessions.json
```

默认行为：

- 第一条微信消息创建新的 Codex thread。
- 后续同一个微信用户的消息使用 `codex exec resume --json <thread_id> <prompt>` 继续同一个会话。
- 本地额外保留最近 20 轮对话，作为调试和 fallback 使用。
- 每次拿到 Codex thread id 后，会把它追加写入 `~/.codex/session_index.jsonl`，方便 Codex Desktop 的会话列表识别。

## 在微信里切换指定 Codex session

你可以直接在微信 ClawBot 聊天里发送控制命令。控制命令由 bridge 本地处理，不会转发给 Codex。

查看当前绑定：

```text
/session
```

列出最近的 Codex sessions：

```text
/sessions
```

切换当前微信用户绑定的 Codex session：

```text
/session 019e4447-e663-7011-99b5-5e1f5d7e380a
```

也可以用中文命令：

```text
当前会话
会话列表
切换会话 019e4447-e663-7011-99b5-5e1f5d7e380a
```

切换后，这个微信用户的后续普通消息都会通过：

```bash
codex exec resume --json <thread_id> <prompt>
```

继续你指定的 Codex session。

相关环境变量：

```bash
export WEIXIN_CODEX_NATIVE_SESSION=true
export WEIXIN_CODEX_HISTORY_TURNS=20
export WEIXIN_CODEX_RESUME_COMMAND='codex exec resume --json {thread_id} {prompt}'
./start_weixin_codex_direct.sh
```

在微信里发送下面任意一种命令，可以清空当前微信用户绑定的 Codex session：

```text
/reset
reset
清空上下文
重置上下文
```

## 去重和单实例

bridge 会做两层保护，避免重复回复：

- 进程锁：`data/weixin-codex-direct.lock` 防止多个 bridge 实例同时运行。
- 消息去重：`data/weixin-codex-seen.json` 记录已处理的微信消息 id。

如果你看到微信里重复回复，优先检查是否有多个进程：

```bash
ps aux | rg 'weixin_codex_direct|start_weixin_codex_direct'
```

## 注意事项

- 当前处理文本消息，以及带文本转写的语音消息。
- 当前只发送文本回复。
- 回复时会复用入站消息的 `context_token`，这是微信后端发回同一聊天所需的信息。
- 长轮询 cursor 会写入 `~/.openclaw/openclaw-weixin/accounts/<account>.sync.json`。
- `data/` 目录包含本地运行状态和聊天记录，不要提交到 git。
