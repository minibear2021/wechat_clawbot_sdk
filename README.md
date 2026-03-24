# wechat-clawbot-sdk

`wechat-clawbot-sdk` 是一个面向微信 ClawBot 的 Python SDK，提供登录、轮询、消息发送、媒体处理和状态持久化等能力。

用于完成以下能力：

- 二维码登录与会话复用
- `getupdates` 长轮询收消息
- 文本与媒体消息发送
- `context_token` 持久化与自动复用
- typing 状态发送与 keepalive
- 文件状态持久化与基础日志输出

## 特性

- 全异步 API，适合 `asyncio` 场景
- 默认文件持久化，重启后仍可复用账号会话与上下文 token
- 内置二维码登录与轮询服务
- 支持文本、图片、视频、文件发送
- 支持 SDK 日志接入用户自己的 `logging.Logger`
- 覆盖登录、轮询、发送、媒体处理和状态管理等常见能力

## 环境要求

- Python `>= 3.11`

## 安装

```bash
pip install wechat-clawbot-sdk
```

## 快速开始

下面的示例演示了最基本的登录、收消息和文本回复流程。

```python
from __future__ import annotations

import asyncio

from wechat_clawbot_sdk import AsyncWeChatBotClient, PollEventType


async def main() -> None:
	client = AsyncWeChatBotClient.create()

	qrcode = await client.start_login()
	print(qrcode.qrcode_image_content)
	session = await client.wait_for_login(qrcode.qrcode)

	async for event in client.poll_events(session.account_id):
		if event.event_type is not PollEventType.MESSAGE or event.message is None:
			continue
		if not event.message.text:
			continue

		await client.send_text(
			account_id=session.account_id,
			user_id=event.message.user_id,
			text=f"echo: {event.message.text}",
		)

	await client.close()


asyncio.run(main())
```

## 核心能力

### 1. 默认客户端工厂

`AsyncWeChatBotClient.create(...)` 会自动组装以下默认组件：

- HTTP transport
- API client
- 二维码登录服务
- polling 服务
- typing 服务
- 消息发送服务
- 配置缓存
- 文件状态存储

默认情况下，它会使用内置的登录网关和 CDN 基础地址。

### 2. 收消息

SDK 提供两种主要消费方式。

直接使用异步迭代：

```python
async for event in client.poll_events(account_id):
	...
```

使用回调驱动循环：

```python
await client.consume_events(account_id, on_event, message_only=True)
```

### 3. 发消息

已公开的稳定发送接口包括：

- `send_text(...)`
- `send_image(...)`
- `send_video(...)`
- `send_file(...)`

这些接口默认会从持久化状态中自动加载对应 `(account_id, user_id)` 的 `context_token`。如果上下文不存在，会抛出 `ValidationError`。

### 4. typing 状态

SDK 内部已经实现 typing keepalive：

- 发送 `TypingStatus.TYPING` 后，会自动维持 typing 状态
- 发送 `TypingStatus.CANCEL` 后，会停止 keepalive

调用示例：

```python
from wechat_clawbot_sdk.api import TypingStatus

await client.send_typing(
	account_id=account_id,
	user_id=user_id,
	status=int(TypingStatus.TYPING),
)
```

## 登录与会话复用

### 首次登录

```python
qrcode = await client.start_login()
print(qrcode.qrcode_image_content)

session = await client.wait_for_login(qrcode.qrcode)
print(session.account_id)
```

### 复用已持久化账号

你可以通过 `get_account_status(...)` 和 `is_account_session_alive(...)` 检查账号是否已保存且仍可用：

```python
status = await client.get_account_status(account_id)
if status.logged_in and status.session is not None:
	alive = await client.is_account_session_alive(account_id)
```

## 状态持久化

默认情况下，SDK 使用 `FileStateStore` 持久化以下内容：

- 账号会话
- `get_updates_buf`
- 每个账号下按用户维度保存的 `context_token`

这样做的直接好处是：

- 进程重启后仍可继续回复已有会话
- 不需要每次重新扫码登录

默认状态目录：

- Windows: `%APPDATA%/wechat-clawbot-sdk`
- macOS: `~/Library/Application Support/wechat-clawbot-sdk`
- Linux: `$XDG_STATE_HOME/wechat-clawbot-sdk` 或 `~/.local/state/wechat-clawbot-sdk`

你可以通过环境变量或参数覆盖：

- 环境变量：`WECHAT_CLAWBOT_SDK_STATE_DIR`
- 工厂参数：`state_dir=`

如果你只想使用内存态，不写磁盘：

```python
from wechat_clawbot_sdk import AsyncWeChatBotClient, InMemoryStateStore


client = AsyncWeChatBotClient.create(state_store=InMemoryStateStore())
```

## 日志

SDK 支持接入标准库 `logging`。

```python
import logging

from wechat_clawbot_sdk import AsyncWeChatBotClient


logger = logging.getLogger("wechat_clawbot_sdk_demo")
client = AsyncWeChatBotClient.create(logger=logger, debug=True)
```

## 示例

仓库内提供了一个可直接运行的示例：

- `examples/echo_bot.py`

当前示例具备这些行为：

- 启动时配置标准库日志
- 优先复用本地持久化账号
- 必要时自动进入二维码登录流程
- 自动检查会话是否仍然有效
- 处理文本与媒体入站消息
- 收到消息后发送 typing，等待一段时间再回复 echo 文本

## 公开导出

常用导出包括：

- `AsyncWeChatBotClient`
- `PollEvent` / `PollEventType`
- `AccountSession` / `AccountStatus`
- `QRCodeSession`
- `InboundMessage` / `OutboundMessage`
- `MediaPayload`
- `FileStateStore` / `InMemoryStateStore`
- `ProtocolError` / `TransportError` / `SessionExpiredError` / `ValidationError`

## 适用范围

这个包专注于提供微信 ClowBot 的 Python SDK 能力，包括：

- 登录与会话管理
- 长轮询收消息
- 文本与媒体发送
- typing 状态控制
- 本地状态持久化

它适合作为独立 SDK 使用，也适合作为上层应用、服务或机器人程序的底层接入模块。

## 开发说明

如果你在仓库内开发本 SDK，常用命令如下：

```bash
pip install -e .
```

示例运行：

```bash
python examples/echo_bot.py
```

## License

本项目遵循仓库中已有的许可证约定。
