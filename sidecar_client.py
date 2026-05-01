"""XYZW Node sidecar 客户端。"""

from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(slots=True)
class SidecarConfig:
    base_url: str
    timeout_seconds: int = 15


class XyzwSidecarClient:
    """负责与 Node sidecar 通信。

    插件层统一通过该客户端访问 sidecar，避免在 AstrBot 侧直接耦合协议细节。
    """

    def __init__(self, config: SidecarConfig):
        self.config = config

    def describe(self) -> str:
        return (
            "当前采用 AstrBot Python 插件 + Node sidecar 方案。\n"
            f"sidecar_base_url: {self.config.base_url}\n"
            f"request_timeout: {self.config.timeout_seconds}s\n"
            "当前最小接口: /health, /v1/token/server-list, /v1/token/authuser, "
            "/v1/token/verify, /v1/account/describe, /v1/command/run, "
            "/v1/car/overview, /v1/car/helpers, /v1/car/send, /v1/car/claim-ready, /v1/task/run-daily, "
            "/v1/dungeon/run, "
            "/v1/resource/run"
        )

    def _resolve_http_timeout_seconds(self, timeout_ms: int | None = None) -> int:
        timeout_seconds = int(self.config.timeout_seconds)
        if timeout_ms is None:
            return timeout_seconds

        numeric = int(timeout_ms)
        if numeric <= 0:
            return timeout_seconds

        derived_seconds = (numeric + 999) // 1000 + 10
        return max(timeout_seconds, derived_seconds)

    async def healthcheck(self) -> dict[str, str]:
        return await asyncio.to_thread(self._request_json_sync, "GET", "/health")

    async def get_server_list_from_bin_base64(self, bin_base64: str) -> dict:
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/token/server-list",
            {"bin_base64": bin_base64},
        )

    async def register_bin_source(
        self,
        bin_base64: str,
        source_type: str = "bin",
        metadata: dict | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "bin_base64": bin_base64,
            "source_type": source_type,
        }
        if metadata:
            payload["metadata"] = metadata
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/token/source/register-bin",
            payload,
        )

    async def start_wechat_qrcode(self) -> dict:
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/token/wechat-qrcode/start",
            {},
        )

    async def get_wechat_qrcode_status(self, uuid: str) -> dict:
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/token/wechat-qrcode/status",
            {"uuid": uuid},
        )

    async def consume_wechat_qrcode(
        self,
        uuid: str | None = None,
        code: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if uuid:
            payload["uuid"] = uuid
        if code:
            payload["code"] = code
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/token/wechat-qrcode/consume",
            payload,
        )

    async def authuser_from_bin_base64(
        self,
        bin_base64: str,
        server_id: str | int | None = None,
    ) -> dict:
        payload: dict[str, object] = {"bin_base64": bin_base64}
        if server_id is not None:
            payload["server_id"] = server_id
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/token/authuser",
            payload,
        )

    async def verify_token(self, token: str) -> dict:
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/token/verify",
            {"token": token},
        )

    async def describe_account(
        self,
        token: str,
        timeout_ms: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {"token": token}
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/account/describe",
            payload,
            self._resolve_http_timeout_seconds(timeout_ms),
        )

    async def run_command(
        self,
        token: str,
        command: str,
        params: dict | None = None,
        timeout_ms: int | None = None,
        response_command: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "token": token,
            "command": command,
            "params": params or {},
        }
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        if response_command:
            payload["response_command"] = response_command
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/command/run",
            payload,
            self._resolve_http_timeout_seconds(timeout_ms),
        )

    async def get_car_overview(
        self,
        token: str,
        timeout_ms: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {"token": token}
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/car/overview",
            payload,
            self._resolve_http_timeout_seconds(timeout_ms),
        )

    async def claim_ready_cars(
        self,
        token: str,
        timeout_ms: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {"token": token}
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/car/claim-ready",
            payload,
            self._resolve_http_timeout_seconds(timeout_ms),
        )

    async def get_car_helpers(
        self,
        token: str,
        member_ids: list[str] | None = None,
        keyword: str | None = None,
        include_self: bool = False,
        timeout_ms: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {"token": token}
        if member_ids:
            payload["member_ids"] = [str(item).strip() for item in member_ids if str(item).strip()]
        if keyword:
            payload["keyword"] = str(keyword).strip()
        if include_self:
            payload["include_self"] = True
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/car/helpers",
            payload,
            self._resolve_http_timeout_seconds(timeout_ms),
        )

    async def send_car(
        self,
        token: str,
        car_id: str | int,
        helper_id: str | int | None = None,
        text: str | None = None,
        is_upgrade: bool | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "token": token,
            "car_id": str(car_id),
        }
        if helper_id is not None:
            payload["helper_id"] = helper_id
        if text is not None:
            payload["text"] = text
        if is_upgrade is not None:
            payload["is_upgrade"] = bool(is_upgrade)
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/car/send",
            payload,
            self._resolve_http_timeout_seconds(timeout_ms),
        )

    async def run_daily_task(
        self,
        token: str,
        options: dict | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {"token": token}
        if options:
            payload["options"] = options
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/task/run-daily",
            payload,
            self._resolve_http_timeout_seconds(timeout_ms),
        )

    async def run_resource_action(
        self,
        token: str,
        action: str,
        options: dict | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "token": token,
            "action": action,
        }
        if options:
            payload["options"] = options
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/resource/run",
            payload,
            self._resolve_http_timeout_seconds(timeout_ms),
        )

    async def run_dungeon_action(
        self,
        token: str,
        action: str,
        options: dict | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "token": token,
            "action": action,
        }
        if options:
            payload["options"] = options
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        return await asyncio.to_thread(
            self._request_json_sync,
            "POST",
            "/v1/dungeon/run",
            payload,
            self._resolve_http_timeout_seconds(timeout_ms),
        )

    def _request_json_sync(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        timeout_seconds: int | None = None,
    ) -> dict:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(
                request,
                timeout=timeout_seconds or self.config.timeout_seconds,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            response_payload = None
            try:
                response_payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                response_payload = None

            if isinstance(response_payload, dict):
                return response_payload
            return {
                "ok": False,
                "code": "HTTP_ERROR",
                "message": str(exc),
            }
        except URLError as exc:
            return {
                "ok": False,
                "code": "NETWORK_ERROR",
                "message": str(exc),
            }
        except (TimeoutError, socket.timeout):
            return {
                "ok": False,
                "code": "TIMEOUT",
                "message": "请求 sidecar 超时",
            }
