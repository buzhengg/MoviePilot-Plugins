# MoviePilot-Plugins

MoviePilot 第三方插件库。

## 插件列表

| 插件 ID | 名称 | 说明 |
|---------|------|------|
| `MoviePilotAppPush` | MoviePilot App 推送 | 为 MoviePilot iOS / macOS App 提供 APNs 远程推送 |

## 安装

### 1. 添加市场源

MoviePilot Web → **插件** → **插件市场设置**，追加（不要带 `.git`）：

```
https://github.com/buzhengg/MoviePilot-Plugins
```

### 2. 安装插件

**插件 → 市场** → 搜索插件名称 → **安装** → 配置并 **启用**。

例如 App 推送：搜索「MoviePilot App 推送」→ 配置 APNs 密钥 → 启用。

### 3. API 安装（可选）

```bash
curl -G "https://<MoviePilot地址>/api/v1/plugin/install/MoviePilotAppPush" \
  --data-urlencode "repo_url=https://github.com/buzhengg/MoviePilot-Plugins" \
  -H "Authorization: Bearer <管理员Token>"
```
