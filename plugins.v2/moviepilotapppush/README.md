# MoviePilotAppPush

为 MoviePilot iOS / macOS App 提供 **APNs 远程推送**。

| 项目 | 值 |
|------|-----|
| 插件 ID / 类名 | `MoviePilotAppPush` |
| 目录 | `moviepilotapppush` |
| 市场名称 | MoviePilot App 推送 |

## 配置（推荐两步）

### 1. 插件页（APNs 技术参数）

启用插件，填写 Team ID、Key ID、.p8、Bundle ID 等。

### 2. 系统通知（场景开关）

**设定 → 通知 → ＋ → 自定义**

| 字段 | 填写 |
|------|------|
| **类型** | `applepush` |
| **名称** | 任意，如「App 推送」 |
| **场景** | 勾选需要推送到 App 的消息类型 |
| **启用** | 打开 |

插件会读取 `Notifications` 里 `type=applepush` 且已启用的渠道，按场景的 `switchs` 决定是否转发（与 Telegram 等内置渠道逻辑一致）。

未配置系统渠道时，若插件中开启「未配置通知渠道时仍按插件规则转发」，则与旧版行为相同。

## API

| 方法 | 路径 |
|------|------|
| POST | `/api/v1/plugin/MoviePilotAppPush/register` |
| DELETE | `/api/v1/plugin/MoviePilotAppPush/unregister` |
| GET | `/api/v1/plugin/MoviePilotAppPush/devices` |
| GET/POST | `/api/v1/plugin/MoviePilotAppPush/test_push` |
