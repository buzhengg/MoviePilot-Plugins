"""
轻量 APNs HTTP/2 客户端（Token-based auth，.p8 密钥）。
依赖 MoviePilot 内置的 httpx[http2] 与 PyJWT。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx
import jwt

from app.log import logger


APNS_PRODUCTION_HOST = "https://api.push.apple.com"
APNS_SANDBOX_HOST = "https://api.sandbox.push.apple.com"


@dataclass
class APNsSendResult:
    device_token: str
    success: bool
    status_code: int
    reason: Optional[str] = None
    apns_id: Optional[str] = None


class APNsClient:
    """Apple Push Notification service 客户端。"""

    def __init__(
            self,
            team_id: str,
            key_id: str,
            auth_key: str,
            bundle_id: str,
            use_sandbox: bool = True,
    ):
        self._team_id = (team_id or "").strip()
        self._key_id = (key_id or "").strip()
        self._auth_key = (auth_key or "").strip()
        self._bundle_id = (bundle_id or "").strip()
        self._use_sandbox = bool(use_sandbox)
        self._jwt_token: Optional[str] = None
        self._jwt_expires_at: float = 0.0
        self._client: Optional[httpx.Client] = None

    @property
    def is_configured(self) -> bool:
        return all([self._team_id, self._key_id, self._auth_key, self._bundle_id])

    def _host(self, use_sandbox: Optional[bool] = None) -> str:
        sandbox = self._use_sandbox if use_sandbox is None else bool(use_sandbox)
        return APNS_SANDBOX_HOST if sandbox else APNS_PRODUCTION_HOST

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(http2=True, timeout=httpx.Timeout(15.0, connect=10.0))
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def _build_jwt(self) -> str:
        now = time.time()
        if self._jwt_token and now < self._jwt_expires_at - 60:
            return self._jwt_token

        token = jwt.encode(
            {"iss": self._team_id, "iat": int(now)},
            self._auth_key,
            algorithm="ES256",
            headers={"alg": "ES256", "kid": self._key_id},
        )
        # PyJWT>=2 可能返回 str 或 bytes
        if isinstance(token, bytes):
            token = token.decode("utf-8")

        self._jwt_token = token
        self._jwt_expires_at = now + 50 * 60
        return token

    def send(
            self,
            device_token: str,
            *,
            title: str,
            body: str,
            link: Optional[str] = None,
            badge: Optional[int] = None,
            sound: str = "default",
            custom: Optional[Dict[str, Any]] = None,
            use_sandbox: Optional[bool] = None,
    ) -> APNsSendResult:
        if not self.is_configured:
            return APNsSendResult(
                device_token=device_token,
                success=False,
                status_code=0,
                reason="APNs 未配置",
            )

        token_hex = (device_token or "").strip().replace(" ", "").replace("<", "").replace(">", "")
        if not token_hex:
            return APNsSendResult(
                device_token=device_token,
                success=False,
                status_code=0,
                reason="device token 为空",
            )

        aps: Dict[str, Any] = {
            "alert": {"title": title or "MoviePilot", "body": body or ""},
            "sound": sound,
        }
        if badge is not None:
            aps["badge"] = badge

        payload: Dict[str, Any] = {"aps": aps}
        if link:
            payload["link"] = link
        if custom:
            payload.update(custom)

        url = f"{self._host(use_sandbox)}/3/device/{token_hex}"
        headers = {
            "authorization": f"bearer {self._build_jwt()}",
            "apns-topic": self._bundle_id,
            "apns-push-type": "alert",
            "apns-priority": "10",
            "content-type": "application/json",
        }

        try:
            response = self._get_client().post(url, headers=headers, json=payload)
            apns_id = response.headers.get("apns-id")
            if response.status_code == 200:
                return APNsSendResult(
                    device_token=token_hex,
                    success=True,
                    status_code=200,
                    apns_id=apns_id,
                )

            reason = response.text.strip() or response.reason_phrase
            try:
                reason_json = response.json()
                reason = reason_json.get("reason") or reason
            except Exception:
                pass

            logger.warning(
                "APNs 推送失败: token=%s status=%s reason=%s",
                token_hex[:12] + "...",
                response.status_code,
                reason,
            )
            return APNsSendResult(
                device_token=token_hex,
                success=False,
                status_code=response.status_code,
                reason=str(reason),
                apns_id=apns_id,
            )
        except Exception as err:
            logger.error("APNs 请求异常: %s", err)
            return APNsSendResult(
                device_token=token_hex,
                success=False,
                status_code=0,
                reason=str(err),
            )

    @staticmethod
    def should_remove_token(result: APNsSendResult) -> bool:
        """410 Gone / Unregistered 时 token 已失效。"""
        if result.status_code == 410:
            return True
        if result.reason and str(result.reason).lower() in {"unregistered", "baddevicetoken"}:
            return True
        return False
