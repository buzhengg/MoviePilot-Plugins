"""
MoviePilot App 远程推送插件。

在线安装：将 https://github.com/buzhengg/MoviePilot-Plugins 加入 PLUGIN_MARKET 后，安装 MoviePilotAppPush。
本地安装：PLUGIN_LOCAL_REPO_PATHS 指向仓库根目录。

App API：
  POST   /api/v1/plugin/MoviePilotAppPush/register
  DELETE /api/v1/plugin/MoviePilotAppPush/unregister
  GET    /api/v1/plugin/MoviePilotAppPush/devices
  GET/POST /api/v1/plugin/MoviePilotAppPush/test_push  （插件详情页测试推送）
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.db import get_async_db
from app.db.models import User
from app.db.user_oper import (
    UserOper,
    get_current_active_superuser_async,
    get_current_active_user_async,
)
from app.core.config import settings
from app.helper.service import ServiceConfigHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import Notification, NotificationConf

from .apns_client import APNsClient, APNsSendResult

PLUGIN_ID = "MoviePilotAppPush"
DEVICE_REGISTRY_KEY = "device_registry"
LAST_TEST_PUSH_KEY = "last_test_push"
# 与「设定 → 通知 → 自定义」里填写的渠道 type 一致（小写）
NOTIFICATION_CHANNEL_TYPE = "applepush"
DEFAULT_BUNDLE_ID = "com.buzheng.MoviePilotApp"
DEFAULT_TEST_TITLE = "MoviePilot 测试推送"
DEFAULT_TEST_BODY = "这是一条来自 MoviePilotAppPush 插件的测试通知"


class DeviceRegisterRequest(BaseModel):
    device_token: str = Field(..., min_length=1, description="APNs device token（hex）")
    platform: str = Field(default="ios", description="ios / macos")
    bundle_id: Optional[str] = Field(default=None, description="App Bundle ID，默认取插件配置")
    apns_environment: Optional[str] = Field(
        default=None,
        description="APNs 环境：sandbox（Debug/开发包）或 production（TestFlight/App Store）",
    )


class DeviceUnregisterRequest(BaseModel):
    device_token: str = Field(..., min_length=1)


class MoviePilotAppPush(_PluginBase):
    plugin_name = "MoviePilot App 推送"
    plugin_desc = "为 MoviePilot iOS / macOS App 提供 APNs 远程推送"
    plugin_version = "1.0.3"
    plugin_author = "buzhengg"
    # 与 package.v2.json 的 icon 一致；独立仓库须用 raw.githubusercontent.com 完整 URL
    plugin_icon = "https://raw.githubusercontent.com/buzhengg/MoviePilot-Plugins/main/icons/moviepilotapppush.png"
    plugin_order = 120

    def __init__(self):
        super().__init__()
        self._config: dict = {}
        self._enabled = False
        self._apns: Optional[APNsClient] = None
        self._lock = threading.Lock()

    def init_plugin(self, config: dict = None):
        self._config = config or {}
        self._enabled = bool(self._config.get("enabled"))
        self._rebuild_apns_client()

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        with self._lock:
            if self._apns:
                self._apns.close()
            self._apns = None

    def _rebuild_apns_client(self) -> None:
        with self._lock:
            if self._apns:
                self._apns.close()
            self._apns = APNsClient(
                team_id=str(self._config.get("team_id") or ""),
                key_id=str(self._config.get("key_id") or ""),
                auth_key=str(self._config.get("auth_key") or ""),
                bundle_id=str(self._config.get("bundle_id") or DEFAULT_BUNDLE_ID),
                use_sandbox=bool(self._config.get("use_sandbox", True)),
            )

    def _apns_ready(self) -> bool:
        client = self._apns
        return bool(self._enabled and client and client.is_configured)

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "enabled",
                                        "label": "启用 App 推送",
                                        "color": "primary",
                                    },
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "use_sandbox",
                                        "label": "使用 APNs 沙盒环境",
                                        "color": "warning",
                                    },
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "push_broadcast",
                                        "label": "未指定用户时推送给所有已注册设备",
                                        "color": "info",
                                    },
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "use_system_notification_channel",
                                        "label": "遵循「设定 → 通知」中的自定义渠道",
                                        "color": "primary",
                                    },
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "fallback_without_channel",
                                        "label": "未配置通知渠道时仍按插件规则转发",
                                        "color": "warning",
                                    },
                                }],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {
                                        "model": "team_id",
                                        "label": "Apple Team ID",
                                        "placeholder": "10 位 Team ID",
                                    },
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {
                                        "model": "key_id",
                                        "label": "APNs Key ID",
                                        "placeholder": "AuthKey 的 Key ID",
                                    },
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {
                                        "model": "bundle_id",
                                        "label": "Bundle ID",
                                        "placeholder": DEFAULT_BUNDLE_ID,
                                    },
                                }],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [{
                                    "component": "VTextarea",
                                    "props": {
                                        "model": "auth_key",
                                        "label": "APNs Auth Key (.p8 文件内容)",
                                        "placeholder": "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----",
                                        "rows": 6,
                                        "auto-grow": True,
                                    },
                                }],
                            },
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": (
                                "建议在「设定 → 通知 → ＋ → 自定义」新增渠道："
                                f"类型填 {NOTIFICATION_CHANNEL_TYPE}，勾选要接收的消息场景并启用。"
                                "App 登录后调用 POST /api/v1/plugin/MoviePilotAppPush/register 上报 device token。"
                                "Debug 包请开启「沙盒环境」；TestFlight / App Store 请关闭。"
                            ),
                        },
                    },
                ],
            }
        ], {
            "enabled": False,
            "use_sandbox": True,
            "push_broadcast": False,
            "use_system_notification_channel": True,
            "fallback_without_channel": True,
            "notification_channel_type": NOTIFICATION_CHANNEL_TYPE,
            "team_id": "",
            "key_id": "",
            "auth_key": "",
            "bundle_id": DEFAULT_BUNDLE_ID,
        }

    def get_page(self) -> Optional[List[dict]]:
        return self._build_detail_page()

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/register",
                "endpoint": self.register_device,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "注册 App 设备 token",
                "description": "MoviePilot App 登录后上报 APNs device token，绑定当前用户。",
            },
            {
                "path": "/unregister",
                "endpoint": self.unregister_device,
                "methods": ["DELETE"],
                "auth": "bear",
                "summary": "注销 App 设备 token",
                "description": "App 登出或关闭推送时移除 device token。",
            },
            {
                "path": "/devices",
                "endpoint": self.list_my_devices,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "列出当前用户已注册设备",
            },
            {
                "path": "/test_push",
                "endpoint": self.test_push,
                "methods": ["GET", "POST"],
                "auth": "bear",
                "summary": "发送测试推送（插件详情页）",
                "description": "管理员登录态（Bearer）调用；与订阅通知走同一 APNs 通道。",
            },
        ]

    async def register_device(
            self,
            body: DeviceRegisterRequest,
            current_user: User = Depends(get_current_active_user_async),
            db: AsyncSession = Depends(get_async_db),
    ) -> schemas.Response:
        _ = db
        username = current_user.name
        token = self._normalize_token(body.device_token)
        if not token:
            return schemas.Response(success=False, message="device token 无效")

        registry = self._load_registry()
        devices = registry.get(username, [])
        now = datetime.now().isoformat(timespec="seconds")
        bundle_id = (body.bundle_id or self._config.get("bundle_id") or DEFAULT_BUNDLE_ID).strip()
        platform = (body.platform or "ios").strip().lower()
        apns_environment = self._normalize_apns_environment(body.apns_environment)

        updated = False
        for item in devices:
            if item.get("device_token") == token:
                item.update({
                    "platform": platform,
                    "bundle_id": bundle_id,
                    "updated_at": now,
                })
                if apns_environment:
                    item["apns_environment"] = apns_environment
                updated = True
                break

        if not updated:
            entry = {
                "device_token": token,
                "platform": platform,
                "bundle_id": bundle_id,
                "updated_at": now,
            }
            if apns_environment:
                entry["apns_environment"] = apns_environment
            devices.append(entry)

        registry[username] = devices
        self._save_registry(registry)
        logger.info("App 推送：用户 %s 注册 device token（%s）", username, token[:12] + "...")
        return schemas.Response(
            success=True,
            message="device token 已注册",
            data={"username": username, "device_count": len(devices)},
        )

    async def unregister_device(
            self,
            body: DeviceUnregisterRequest,
            current_user: User = Depends(get_current_active_user_async),
            db: AsyncSession = Depends(get_async_db),
    ) -> schemas.Response:
        _ = db
        username = current_user.name
        token = self._normalize_token(body.device_token)
        registry = self._load_registry()
        devices = registry.get(username, [])
        registry[username] = [d for d in devices if d.get("device_token") != token]
        self._save_registry(registry)
        return schemas.Response(success=True, message="device token 已移除")

    async def list_my_devices(
            self,
            current_user: User = Depends(get_current_active_user_async),
            db: AsyncSession = Depends(get_async_db),
    ) -> schemas.Response:
        _ = db
        username = current_user.name
        devices = self._load_registry().get(username, [])
        masked = [
            {
                "platform": d.get("platform"),
                "bundle_id": d.get("bundle_id"),
                "apns_environment": self._apns_env_display(d),
                "updated_at": d.get("updated_at"),
                "device_token": (d.get("device_token") or "")[:12] + "...",
            }
            for d in devices
        ]
        return schemas.Response(success=True, data={"devices": masked})

    async def test_push(
            self,
            username: Optional[str] = None,
            device_token: Optional[str] = None,
            title: str = DEFAULT_TEST_TITLE,
            message: str = DEFAULT_TEST_BODY,
            link: Optional[str] = None,
            current_user: User = Depends(get_current_active_superuser_async),
            db: AsyncSession = Depends(get_async_db),
    ) -> schemas.Response:
        """插件详情页：发送测试 APNs（管理员 Bearer 鉴权）。"""
        _ = db
        logger.info(
            "App 推送：管理员 %s 触发测试推送 user=%s token=%s",
            current_user.name,
            username or "*",
            (device_token[:12] + "...") if device_token else "*",
        )
        return self._execute_test_push(
            username=username,
            device_token=device_token,
            title=title,
            message=message,
            link=link,
        )

    def _execute_test_push(
            self,
            *,
            username: Optional[str],
            device_token: Optional[str],
            title: str,
            message: str,
            link: Optional[str],
    ) -> schemas.Response:
        if not self._apns_ready():
            return schemas.Response(
                success=False,
                message="插件未启用或 APNs 未配置完整，请先在插件配置中填写 Team ID / Key ID / .p8 / Bundle ID",
            )

        targets = self._resolve_test_push_targets(username, device_token)
        if not targets:
            hint = "暂无已注册设备"
            if username:
                hint = f"用户 {username} 暂无已注册设备"
            elif device_token:
                hint = "未找到匹配的 device token"
            return schemas.Response(success=False, message=hint)

        push_title = (title or DEFAULT_TEST_TITLE).strip() or DEFAULT_TEST_TITLE
        push_body = (message or DEFAULT_TEST_BODY).strip() or DEFAULT_TEST_BODY

        devices_by_user: Dict[str, List[dict]] = {}
        for target in targets:
            devices_by_user.setdefault(target["username"], []).append(target["device"])

        sent, failed = self._deliver_push(
            usernames=[t["username"] for t in targets],
            registry=self._load_registry(),
            title=push_title,
            body=push_body,
            link=link,
            mtype=None,
            devices_by_user=devices_by_user,
        )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary = f"已向 {sent}/{len(targets)} 个设备发送测试推送"
        self.save_data(LAST_TEST_PUSH_KEY, {
            "time": now,
            "title": push_title,
            "message": push_body,
            "username": username,
            "device_token": device_token,
            "sent": sent,
            "total": len(targets),
            "failed": failed,
        })

        if failed and sent:
            return schemas.Response(
                success=True,
                message=f"{summary}，{len(failed)} 个失败",
                data={"sent": sent, "failed": failed},
            )
        if failed:
            return schemas.Response(
                success=False,
                message=f"推送失败：{failed[0].get('reason', '未知错误')}",
                data={"sent": 0, "failed": failed},
            )
        return schemas.Response(success=True, message=summary, data={"sent": sent})

    def get_module(self) -> Dict[str, Any]:
        return {"post_message": self.send_push}

    def send_push(self, message: Notification, **kwargs) -> None:
        if not self._apns_ready() or not self._passes_channel_filters(message):
            return None

        title, body = self._extract_notification_content(message)
        if not title:
            return None

        registry = self._load_registry()
        usernames = self._resolve_recipient_usernames(message, registry)
        if not usernames:
            logger.debug(
                "App 推送：跳过「%s」，mtype=%s，无匹配接收用户",
                title,
                self._message_mtype_value(message),
            )
            return None

        sent, _ = self._deliver_push(
            usernames=usernames,
            registry=registry,
            title=title,
            body=body,
            link=message.link,
            mtype=self._message_mtype_value(message),
        )
        if sent:
            logger.info("App 推送：已向 %d 个设备发送「%s」", sent, title)
        return None

    def _config_flag(self, key: str, default: bool = False) -> bool:
        return bool(self._config.get(key, default))

    @staticmethod
    def _extract_notification_content(message: Notification) -> Tuple[Optional[str], str]:
        title = (message.title or "").strip() or "MoviePilot"
        body = (message.text or "").strip()
        if not body and not message.title:
            return None, body
        return title, body

    def _deliver_push(
            self,
            *,
            usernames: List[str],
            registry: Dict[str, List[dict]],
            title: str,
            body: str,
            link: Optional[str],
            mtype: Optional[str],
            devices_by_user: Optional[Dict[str, List[dict]]] = None,
    ) -> Tuple[int, List[dict]]:
        sent = 0
        failed: List[dict] = []
        invalid_tokens: List[Tuple[str, str]] = []
        seen_tokens: set[str] = set()

        for username in usernames:
            devices = devices_by_user.get(username) if devices_by_user else registry.get(username, [])
            for device in devices or []:
                token = device.get("device_token")
                if not token or token in seen_tokens:
                    continue
                seen_tokens.add(token)

                result = self._send_to_device(
                    token=token,
                    title=title,
                    body=body,
                    link=link,
                    mtype=mtype,
                    use_sandbox=self._device_use_sandbox(device),
                )
                if result.success:
                    sent += 1
                else:
                    failed.append({
                        "username": username,
                        "device_token": token,
                        "reason": result.reason or f"HTTP {result.status_code}",
                    })
                    if APNsClient.should_remove_token(result):
                        invalid_tokens.append((username, token))

        if invalid_tokens:
            self._remove_invalid_tokens(invalid_tokens)
        return sent, failed

    @staticmethod
    def _normalize_token(raw: str) -> str:
        return (raw or "").strip().replace(" ", "").replace("<", "").replace(">", "")

    def _load_registry(self) -> Dict[str, List[dict]]:
        data = self.get_data(DEVICE_REGISTRY_KEY)
        if isinstance(data, dict):
            return copy.deepcopy(data)
        return {}

    def _save_registry(self, registry: Dict[str, List[dict]]) -> None:
        self.save_data(DEVICE_REGISTRY_KEY, registry)

    def _notification_channel_type(self) -> str:
        raw = self._config.get("notification_channel_type") or NOTIFICATION_CHANNEL_TYPE
        return str(raw).strip().lower() or NOTIFICATION_CHANNEL_TYPE

    def _load_system_channels(self) -> List[NotificationConf]:
        channel_type = self._notification_channel_type()
        return [
            conf for conf in ServiceConfigHelper.get_notification_configs()
            if (conf.type or "").strip().lower() == channel_type and conf.enabled and conf.name
        ]

    @staticmethod
    def _message_mtype_value(message: Notification) -> Optional[str]:
        if not message.mtype:
            return None
        return message.mtype.value if hasattr(message.mtype, "value") else str(message.mtype)

    @staticmethod
    def _matches_channel_conf(conf: NotificationConf, message: Notification) -> bool:
        """与内置 _MessageBase.check_message 一致。"""
        if message.source and message.source != conf.name:
            return False
        if message.userid or not message.mtype:
            return True
        switchs = conf.switchs or []
        mtype_value = MoviePilotAppPush._message_mtype_value(message)
        return bool(mtype_value and mtype_value in switchs)

    def _passes_channel_filters(self, message: Notification) -> bool:
        if not self._config_flag("use_system_notification_channel", True):
            return True
        channels = self._load_system_channels()
        if not channels:
            return self._config_flag("fallback_without_channel", True)
        return any(self._matches_channel_conf(conf, message) for conf in channels)

    def _get_scope_action(self, message: Notification) -> Optional[str]:
        if not message.mtype:
            return None
        action = ServiceConfigHelper.get_notification_switch(message.mtype)
        if action:
            return action
        mtype_value = self._message_mtype_value(message)
        if not mtype_value:
            return None
        for switch in ServiceConfigHelper.get_notification_switches():
            if switch.type == mtype_value:
                return switch.action
        return None

    @staticmethod
    def _usernames_for_scope(
            message: Notification,
            registered: set[str],
            action: str,
    ) -> List[str]:
        parts = [p.strip() for p in action.split(",") if p.strip()]
        if "all" in parts:
            return sorted(registered)

        resolved: List[str] = []
        for part in parts:
            if part == "admin" and settings.SUPERUSER in registered:
                resolved.append(settings.SUPERUSER)
            elif part == "user" and message.username:
                un = str(message.username)
                if un in registered:
                    resolved.append(un)
        return list(dict.fromkeys(resolved))

    def _username_from_userid(self, userid: Any) -> Optional[str]:
        try:
            uid = int(userid)
        except (TypeError, ValueError):
            return None
        try:
            user = User.get_by_id(UserOper()._db, uid)
        except Exception as err:
            logger.debug("App 推送：按 userid 查用户失败：%s", err)
            return None
        return user.name if user else None

    def _resolve_recipient_usernames(
            self,
            message: Notification,
            registry: Dict[str, List[dict]],
    ) -> List[str]:
        if not registry:
            return []

        registered = set(registry.keys())

        if message.username:
            un = str(message.username)
            return [un] if un in registered else []

        if message.userid and (name := self._username_from_userid(message.userid)):
            return [name] if name in registered else []

        if message.targets is not None:
            admin = settings.SUPERUSER
            return [admin] if admin and admin in registered else []

        if action := self._get_scope_action(message):
            scoped = self._usernames_for_scope(message, registered, action)
            if scoped:
                return scoped

        if self._config_flag("push_broadcast"):
            return list(registered)

        return []

    def _system_channel_status_text(self) -> str:
        if not self._config_flag("use_system_notification_channel", True):
            return "已关闭系统通知渠道联动，仅按插件内规则转发。"
        channels = self._load_system_channels()
        channel_type = self._notification_channel_type()
        if not channels:
            if self._config_flag("fallback_without_channel", True):
                return (
                    f"未在「设定 → 通知」中配置 type={channel_type} 的自定义渠道；"
                    "当前使用插件兜底规则（未配置渠道时仍可能推送）。"
                )
            return (
                f"未配置 type={channel_type} 的通知渠道，且已关闭兜底，系统通知不会转发到 App。"
            )
        names = "、".join(conf.name for conf in channels)
        return f"已联动系统通知渠道（type={channel_type}）：{names}"

    def _send_to_device(
            self,
            *,
            token: str,
            title: str,
            body: str,
            link: Optional[str],
            mtype: Optional[str],
            use_sandbox: Optional[bool] = None,
    ) -> APNsSendResult:
        with self._lock:
            client = self._apns
            if not client:
                return APNsSendResult(
                    device_token=token, success=False, status_code=0, reason="client unavailable"
                )

        custom = {}
        if mtype:
            custom["mtype"] = mtype

        return client.send(
            token,
            title=title,
            body=body,
            link=link,
            custom=custom or None,
            use_sandbox=use_sandbox,
        )

    def _remove_invalid_tokens(self, invalid: List[Tuple[str, str]]) -> None:
        registry = self._load_registry()
        changed = False
        for username, bad_token in invalid:
            devices = registry.get(username, [])
            filtered = [d for d in devices if d.get("device_token") != bad_token]
            if len(filtered) != len(devices):
                registry[username] = filtered
                changed = True
                logger.info("App 推送：移除失效 token 用户=%s token=%s...", username, bad_token[:12])

        if changed:
            self._save_registry(registry)

    def _resolve_test_push_targets(
            self,
            username: Optional[str],
            device_token: Optional[str],
    ) -> List[dict]:
        registry = self._load_registry()
        token_filter = self._normalize_token(device_token or "") if device_token else ""

        def _target(uname: str, device: dict) -> dict:
            return {
                "username": uname,
                "device_token": device.get("device_token") or "",
                "device": device,
            }

        if token_filter:
            for uname, devices in registry.items():
                for device in devices:
                    token = device.get("device_token") or ""
                    if token == token_filter:
                        return [_target(uname, device)]
            return []

        if username:
            return [
                _target(username, d)
                for d in registry.get(username, [])
                if d.get("device_token")
            ]

        targets: List[dict] = []
        for uname, devices in registry.items():
            for device in devices:
                if device.get("device_token"):
                    targets.append(_target(uname, device))
        return targets

    def _page_test_push_event(
            self,
            *,
            username: str = "",
            device_token: str = "",
            title: str = "",
            message: str = "",
    ) -> dict:
        """详情页按钮：使用 POST + 管理员 Bearer（勿用 apikey，Web 登录态不传 apikey）。"""
        params: Dict[str, Any] = {}
        if username:
            params["username"] = username
        if device_token:
            params["device_token"] = device_token
        if title:
            params["title"] = title
        if message:
            params["message"] = message
        return {
            "api": f"plugin/{PLUGIN_ID}/test_push",
            "method": "post",
            "params": params,
        }

    @staticmethod
    def _wrap_detail_page(sections: List[dict]) -> List[dict]:
        return [{
            "component": "div",
            "props": {"class": "d-flex flex-column gap-4 pa-2"},
            "content": sections,
        }]

    @staticmethod
    def _detail_stat_card(value: str, label: str) -> dict:
        return {
            "component": "VCard",
            "props": {"variant": "tonal", "class": "h-100"},
            "content": [{
                "component": "VCardText",
                "props": {"class": "py-5 px-4"},
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "text-h5 font-weight-bold"},
                        "text": value,
                    },
                    {
                        "component": "div",
                        "props": {"class": "text-caption text-medium-emphasis mt-2"},
                        "text": label,
                    },
                ],
            }],
        }

    def _build_detail_page(self) -> List[dict]:
        registry = self._load_registry()
        last_test = self.get_data(LAST_TEST_PUSH_KEY) or {}

        status_alerts: List[dict] = [{
            "component": "VAlert",
            "props": {
                "type": "success" if self._apns_ready() else "warning",
                "variant": "tonal",
                "density": "comfortable",
                "text": self._detail_apns_status_text(),
            },
        }, {
            "component": "VAlert",
            "props": {
                "type": "info",
                "variant": "tonal",
                "density": "comfortable",
                "text": self._system_channel_status_text(),
            },
        }]

        if last_test:
            failed_list = last_test.get("failed") or []
            failed_count = len(failed_list)
            sent = int(last_test.get("sent") or 0)
            total = int(last_test.get("total") or 0)
            last_msg = (
                f"最近测试：{last_test.get('time', '—')}，"
                f"成功 {sent}/{total}"
            )
            if failed_count:
                last_msg += f"，失败 {failed_count} 个"
                first_reason = (failed_list[0] or {}).get("reason")
                if first_reason:
                    last_msg += f"。原因：{first_reason}"
            alert_type = "error" if total > 0 and sent == 0 else "info"
            status_alerts.append({
                "component": "VAlert",
                "props": {
                    "type": alert_type,
                    "variant": "tonal",
                    "density": "comfortable",
                    "text": last_msg,
                },
            })

        sections: List[dict] = [{
            "component": "div",
            "props": {"class": "d-flex flex-column gap-3"},
            "content": status_alerts,
        }]

        if not registry:
            sections.append({
                "component": "VCard",
                "props": {"variant": "outlined"},
                "content": [{
                    "component": "VCardText",
                    "props": {"class": "text-center text-medium-emphasis py-10 px-6"},
                    "text": "暂无已注册设备。请使用 MoviePilot App 登录并允许通知权限。",
                }],
            })
            return self._wrap_detail_page(sections)

        total_users = len(registry)
        total_devices = sum(len(devices) for devices in registry.values())

        sections.append({
            "component": "VRow",
            "props": {"dense": True},
            "content": [
                {
                    "component": "VCol",
                    "props": {"cols": 12, "sm": 6, "class": "pb-2"},
                    "content": [self._detail_stat_card(str(total_users), "已注册用户")],
                },
                {
                    "component": "VCol",
                    "props": {"cols": 12, "sm": 6, "class": "pb-2"},
                    "content": [self._detail_stat_card(str(total_devices), "已注册设备")],
                },
            ],
        })
        sections.append({
            "component": "div",
            "props": {"class": "d-flex justify-end mb-1"},
            "content": [{
                "component": "VBtn",
                "props": {
                    "color": "primary",
                    "prependIcon": "mdi-bell-ring",
                },
                "text": "向全部设备发送测试推送",
                "events": {
                    "click": self._page_test_push_event(),
                },
            }],
        })

        table_headers = [
            {"text": "用户名", "class": "text-start ps-4"},
            {"text": "平台", "class": "text-start"},
            {"text": "APNs 环境", "class": "text-start"},
            {"text": "Bundle ID", "class": "text-start"},
            {"text": "Device Token", "class": "text-start"},
            {"text": "更新时间", "class": "text-start"},
            {"text": "操作", "class": "text-start pe-4"},
        ]
        header_row = {
            "component": "thead",
            "content": [
                {
                    "component": "th",
                    "props": {"class": h["class"]},
                    "text": h["text"],
                }
                for h in table_headers
            ],
        }

        table_rows: List[dict] = []
        for username in sorted(registry.keys()):
            for device in registry.get(username, []):
                token = device.get("device_token") or ""
                if not token:
                    continue
                table_rows.append({
                    "component": "tr",
                    "props": {"class": "text-sm"},
                    "content": [
                        {"component": "td", "props": {"class": "ps-4"}, "text": username},
                        {"component": "td", "text": device.get("platform") or "—"},
                        {"component": "td", "text": self._detail_device_env_text(device)},
                        {"component": "td", "text": device.get("bundle_id") or "—"},
                        {
                            "component": "td",
                            "props": {
                                "class": "font-mono text-caption py-3",
                                "style": "word-break: break-all; max-width: 420px;",
                            },
                            "text": token,
                        },
                        {"component": "td", "text": device.get("updated_at") or "—"},
                        {
                            "component": "td",
                            "props": {"class": "pe-4"},
                            "content": [{
                                "component": "VBtn",
                                "props": {
                                    "color": "primary",
                                    "size": "small",
                                    "variant": "tonal",
                                },
                                "text": "测试推送",
                                "events": {
                                    "click": self._page_test_push_event(
                                        username=username,
                                        device_token=token,
                                    ),
                                },
                            }],
                        },
                    ],
                })

        sections.append({
            "component": "VCard",
            "props": {"variant": "flat", "class": "border-thin"},
            "content": [
                {
                    "component": "VCardTitle",
                    "props": {"class": "text-subtitle-1 font-weight-medium pt-4 pb-2 px-4"},
                    "text": "已注册设备",
                },
                {
                    "component": "VCardText",
                    "props": {"class": "pt-0 pb-4 px-2"},
                    "content": [{
                        "component": "VTable",
                        "props": {"hover": True, "density": "comfortable"},
                        "content": [
                            header_row,
                            {"component": "tbody", "content": table_rows},
                        ],
                    }],
                },
            ],
        })

        sections.append({
            "component": "VAlert",
            "props": {
                "type": "info",
                "variant": "tonal",
                "density": "comfortable",
                "class": "mb-1",
                "text": (
                    "点击「测试推送」将向对应设备发送默认测试通知。"
                    "APNs 环境由 App 注册时上报：Debug 包为沙盒，TestFlight / App Store 为生产。"
                    "插件配置中的沙盒开关仅作未上报环境时的兜底。"
                ),
            },
        })

        return self._wrap_detail_page(sections)

    @staticmethod
    def _normalize_apns_environment(raw: Optional[str]) -> Optional[str]:
        value = (raw or "").strip().lower()
        if value in {"sandbox", "development", "dev"}:
            return "sandbox"
        if value in {"production", "prod", "release"}:
            return "production"
        return None

    @classmethod
    def _apns_env_display(cls, device: dict) -> str:
        env = cls._normalize_apns_environment(device.get("apns_environment"))
        if env == "sandbox":
            return "沙盒"
        if env == "production":
            return "生产"
        return "未知"

    def _device_use_sandbox(self, device: dict) -> bool:
        env = self._normalize_apns_environment(device.get("apns_environment"))
        if env == "sandbox":
            return True
        if env == "production":
            return False
        return bool(self._config.get("use_sandbox", True))

    def _detail_device_env_text(self, device: dict) -> str:
        label = self._apns_env_display(device)
        env = self._normalize_apns_environment(device.get("apns_environment"))
        if not env:
            return label
        plugin_sandbox = bool(self._config.get("use_sandbox", True))
        device_sandbox = env == "sandbox"
        if device_sandbox != plugin_sandbox:
            return f"{label}（与插件配置不一致）"
        return label

    def _detail_apns_status_text(self) -> str:
        if not self._enabled:
            return "插件未启用：请在插件配置中打开「启用 App 推送」"
        client = self._apns
        if not client or not client.is_configured:
            return "APNs 未配置完整：请填写 Team ID、Key ID、.p8 内容与 Bundle ID"
        env = "沙盒" if self._config.get("use_sandbox", True) else "生产"
        bundle = self._config.get("bundle_id") or DEFAULT_BUNDLE_ID
        return f"APNs 已就绪（{env}），Bundle ID：{bundle}"
