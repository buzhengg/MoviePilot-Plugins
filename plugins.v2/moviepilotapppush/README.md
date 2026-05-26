# MoviePilotAppPush

为 MoviePilot iOS / macOS App 提供 **APNs 远程推送**。

| 项目 | 值 |
|------|-----|
| 插件 ID / 类名 | `MoviePilotAppPush` |
| 目录 | `moviepilotapppush` |
| 市场名称 | MoviePilot App 推送 |

## API

| 方法 | 路径 |
|------|------|
| POST | `/api/v1/plugin/MoviePilotAppPush/register` |
| DELETE | `/api/v1/plugin/MoviePilotAppPush/unregister` |
| GET | `/api/v1/plugin/MoviePilotAppPush/devices` |
| GET/POST | `/api/v1/plugin/MoviePilotAppPush/test_push` |

详细说明见主项目 [MoviePilotAppPush.md](../../../MoviePilotAppPush.md)。
