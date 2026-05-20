# 部署指南

这份文档只讲如何部署和运行。日常使用、微信命令、配对后的聊天方式见 [README.md](README.md)。

## 前置条件

- macOS。
- 已安装并登录 Codex CLI / Codex Desktop。
- 已安装 Node.js / npm，用于运行腾讯官方微信 ClawBot 安装命令。
- 可以在本机运行 `python3`。

## 安装仓库

```bash
git clone https://github.com/Micromvp/wechat-codex-bridge.git
cd wechat-codex-bridge
```

## 安装并授权微信 ClawBot

运行腾讯官方微信 ClawBot channel 安装命令：

```bash
npx -y @tencent-weixin/openclaw-weixin-cli install
```

按命令提示用微信扫码并完成授权。授权成功后，本机会出现账号文件：

```bash
ls ~/.openclaw/openclaw-weixin/accounts/*.json
```

bridge 会从这个账号文件读取 `token` 和 `baseUrl`，直接调用腾讯 ilink bot API。

## 避免 OpenClaw 重复消费消息

如果你同时运行 OpenClaw Gateway，并且里面启用了 `openclaw-weixin` channel，OpenClaw 可能会和本 bridge 同时消费消息，导致重复回复。

如果你希望走纯直连 Codex 路径，建议禁用 OpenClaw 的微信插件：

```bash
openclaw config set plugins.entries.openclaw-weixin.enabled false
openclaw gateway restart
```

需要恢复时：

```bash
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw gateway restart
```

## 启动直连 ClawBot -> Codex

前台启动：

```bash
./start_weixin_codex_direct.sh
```

后台启动：

```bash
screen -dmS weixin-codex-direct /bin/bash -lc './start_weixin_codex_direct.sh > data/weixin-codex-direct.log 2>&1'
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

默认情况下，Codex 在当前仓库目录中执行。建议设置成你真正想让 Codex 操作的项目目录：

```bash
export WEIXIN_CODEX_CWD='/path/to/your/workspace'
./start_weixin_codex_direct.sh
```

也可以完全覆盖创建新会话和继续会话的命令：

```bash
export WEIXIN_CODEX_COMMAND='codex exec --json --cd /path/to/your/workspace --sandbox workspace-write {prompt}'
export WEIXIN_CODEX_RESUME_COMMAND='codex exec resume --json {thread_id} {prompt}'
./start_weixin_codex_direct.sh
```

## 环境变量

- `WEIXIN_CODEX_ACCOUNT_ID`：指定使用哪个微信 ClawBot account json。
- `WEIXIN_CODEX_CWD`：Codex 执行命令的工作目录。
- `WEIXIN_CODEX_COMMAND`：创建新 Codex session 时执行的命令模板。
- `WEIXIN_CODEX_RESUME_COMMAND`：继续已有 Codex session 时执行的命令模板。
- `WEIXIN_CODEX_NATIVE_SESSION`：是否使用 Codex 原生 session，默认 `true`。
- `WEIXIN_CODEX_HISTORY_TURNS`：本地 fallback 历史保留轮数，默认 `20`。
- `WEIXIN_CODEX_TIMEOUT`：Codex 单次执行超时时间，默认 `180` 秒。
- `WEIXIN_CODEX_DATA_DIR`：运行数据目录，默认 `data/`。

## 运行数据

bridge 会在 `data/` 里保存运行状态：

- `weixin-codex-sessions.json`：微信用户到 Codex session 的绑定。
- `weixin-codex-seen.json`：已处理的微信消息 id，用于去重。
- `weixin-codex-direct.lock`：单实例锁，防止多开导致重复回复。
- `weixin-codex-direct.log`：运行日志。

`data/` 不应该提交到 git。

## 桌面辅助模式

本仓库也保留了桌面辅助模式，可以通过 MCP + macOS 自动化读取当前微信窗口、OCR、发送消息：

```bash
./start_wechat_bridge.sh
```

这个模式主要用于本机桌面辅助，不是推荐的 ClawBot 直连路径。

## 本地 Bridge API

桌面辅助 / Bot Channel 模式默认监听：

```text
http://127.0.0.1:8787
```

常用接口：

- `GET /health`
- `GET /chats?limit=20`
- `GET /messages?chat_id=<id>&limit=20&since=<cursor>`
- `POST /bot/webhook`
- `POST /bot/ack`
- `POST /contacts/upsert`
- `POST /messages/import_clipboard`
- `POST /messages/import_active_window`
- `POST /messages/send`

类 OpenClaw webhook 架构见 [OPENCLAW_STYLE_BOT.md](OPENCLAW_STYLE_BOT.md)。

## 安全与隐私

- 不要提交 `data/`。
- 不要提交微信账号 token、OpenClaw account json、Codex 凭据。
- 如果 bridge 不只监听 localhost，请启用认证并放在 HTTPS 后面。
- 让代码写入型 agent 接入微信前，建议先用低风险项目测试。

## 排障

检查是否多开：

```bash
ps aux | rg 'weixin_codex_direct|start_weixin_codex_direct'
```

确认授权账号文件是否存在：

```bash
ls ~/.openclaw/openclaw-weixin/accounts/*.json
```

确认 bridge 日志：

```bash
tail -n 100 data/weixin-codex-direct.log
```
