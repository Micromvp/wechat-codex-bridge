# WeChat Codex Bridge

在微信里和 Codex 聊天。这个项目把腾讯官方微信 ClawBot 消息直接转给 Codex CLI / Codex Desktop 使用的同一套 session，并把 Codex 的回复发回微信聊天窗口。

如果你还没有部署，请先看 [DEPLOYMENT.md](DEPLOYMENT.md)。本页只讲已经部署后，如何配对和使用。

## 工作方式

推荐模式是直连 ClawBot：

```text
微信 ClawBot -> wechat-codex-bridge -> Codex session -> 微信 ClawBot
```

每个微信用户默认绑定一个长期 Codex session。第一次普通消息会创建 session，后续普通消息会通过 `codex exec resume` 继续同一个会话，所以 Codex 不会每次都从零开始。

## 和微信 ClawBot 配对

1. 在运行 bridge 的电脑上安装并授权官方微信 ClawBot channel：

```bash
npx -y @tencent-weixin/openclaw-weixin-cli install
```

2. 安装命令会展示二维码或打开授权流程。用微信扫码并确认授权。

3. 授权成功后，微信里会出现类似「微信 ClawBot」的聊天入口。之后你就可以直接给这个 bot 发消息。

4. 如果你还没启动 bridge，请在电脑上启动：

```bash
./start_weixin_codex_direct.sh
```

更完整的部署、后台运行、排障方式见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 基本使用

直接在微信 ClawBot 聊天里发普通消息即可：

```text
帮我看一下这个项目现在还有什么要做
```

bridge 会把消息转给 Codex，Codex 执行后把结果回复到同一个微信聊天窗口。

## 微信命令

控制命令由本地 bridge 处理，不会转发给 Codex。

### 会话

查看当前绑定的 Codex session：

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

清空当前微信用户绑定的 Codex session：

```text
/reset
```

中文命令：

```text
当前会话
会话列表
切换会话 019e4447-e663-7011-99b5-5e1f5d7e380a
清空上下文
重置上下文
```

### 模型

查看当前模型：

```text
/model
```

切换模型：

```text
/model gpt-5.5
```

恢复默认模型：

```text
/model default
```

中文命令：

```text
当前模型
切换模型 gpt-5.5
模型 gpt-5.5
```

### 速度

查看当前速度：

```text
/speed
```

切换速度：

```text
/speed fast
/speed standard
```

恢复默认速度：

```text
/speed default
```

速度对应 Codex UI 里的「速度」设置：

- `standard` / `标准`：默认速度，常规用量。
- `fast` / `快速`：1.5 倍速，用量增加。

中文命令：

```text
当前速度
切换速度 快速
速度 标准
```

### 设置

查看当前微信用户的 session、模型、速度：

```text
/settings
```

中文命令：

```text
当前设置
设置
```

## 常见问题

如果 bot 重复回复，通常是启动了多个 bridge 实例。停止旧进程后只保留一个 `weixin-codex-direct` 实例即可。

如果 Desktop 里看不到微信创建的会话，确认 bridge 已经是新版。新版会把微信 session 追加写入 `~/.codex/session_index.jsonl`，方便 Codex Desktop 识别。

如果想了解直连模式的内部细节，请看 [DIRECT_WEIXIN_CODEX.md](DIRECT_WEIXIN_CODEX.md)。
