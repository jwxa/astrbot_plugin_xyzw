"""XYZW AstrBot 插件。"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from astrbot.api import logger
import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.utils.session_waiter import (
    SessionController,
    SessionFilter,
    session_waiter,
)

from .notifier import NotificationRouter
from .sidecar_client import SidecarConfig, XyzwSidecarClient
from .storage import XyzwStorage, _normalize_activity_key


class UserBindSessionFilter(SessionFilter):
    """按平台和发送人维度隔离绑定会话。"""

    def filter(self, event: AstrMessageEvent) -> str:
        return f"xyzw-bind:{event.get_platform_id()}:{event.get_sender_id()}"


@register(
    "astrbot_plugin_xyzw",
    "codex",
    "XYZW AstrBot 插件，采用 AstrBot + Node sidecar 架构。",
    "0.21.1",
    "https://github.com/your-org/astrbot_plugin_xyzw",
)
class XyzwPlugin(Star):
    """XYZW AstrBot 插件。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.logger = logger
        self.config = config or {}
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_xyzw")
        self.storage = XyzwStorage(self.data_dir)
        self.sidecar = XyzwSidecarClient(
            SidecarConfig(
                base_url=self.config.get("sidecar_base_url", "http://127.0.0.1:8099"),
                timeout_seconds=int(self.config.get("request_timeout", 15)),
            )
        )
        self._last_bot_self_id = ""
        self.notifier = NotificationRouter(
            storage=self.storage,
            allow_private_fallback=bool(
                self.config.get("allow_private_fallback", True)
            ),
            forward_sender_uin_resolver=self._get_forward_sender_uin,
        )
        self.binding_private_only = bool(self.config.get("binding_private_only", True))
        self.scheduler_poll_interval_seconds = self._read_int_config(
            "scheduler_poll_interval_seconds",
            default=60,
            min_value=30,
        )
        self.car_reminder_default_interval_minutes = self._read_int_config(
            "car_reminder_default_interval_minutes",
            default=15,
            min_value=5,
            max_value=720,
        )
        self.car_reminder_check_timeout_ms = self._read_int_config(
            "car_reminder_check_timeout_ms",
            default=15000,
            min_value=5000,
            max_value=120000,
        )
        self.hangup_reminder_default_interval_minutes = self._read_int_config(
            "hangup_reminder_default_interval_minutes",
            default=15,
            min_value=5,
            max_value=720,
        )
        self.helper_member_reminder_default_interval_minutes = self._read_int_config(
            "helper_member_reminder_default_interval_minutes",
            default=30,
            min_value=5,
            max_value=720,
        )
        self.hangup_reminder_check_timeout_ms = self._read_int_config(
            "hangup_reminder_check_timeout_ms",
            default=15000,
            min_value=5000,
            max_value=120000,
        )
        self.daily_task_timeout_ms = self._read_int_config(
            "daily_task_timeout_ms",
            default=90000,
            min_value=10000,
            max_value=180000,
        )
        self.car_helper_query_timeout_ms = self._read_int_config(
            "car_helper_query_timeout_ms",
            default=30000,
            min_value=5000,
            max_value=120000,
        )
        self.wechat_qrcode_poll_interval_ms = self._read_int_config(
            "wechat_qrcode_poll_interval_ms",
            default=1000,
            min_value=500,
            max_value=5000,
        )
        self._scheduler_stop_event = asyncio.Event()
        self._running_daily_task_account_ids: set[str] = set()
        self._pending_daily_task_account_ids: set[str] = set()
        self._running_action_task_job_ids: set[str] = set()
        self._background_tasks: set[asyncio.Task] = set()
        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(),
            name="xyzw_scheduler_loop",
        )
        self._scheduler_task.add_done_callback(self._on_scheduler_task_done)
        self.logger.info("[XYZW] 插件已加载")

    def _read_int_config(
        self,
        key: str,
        default: int,
        min_value: int = 1,
        max_value: int | None = None,
    ) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        value = max(min_value, value)
        if max_value is not None:
            value = min(max_value, value)
        return value

    async def terminate(self) -> None:
        self._scheduler_stop_event.set()
        if not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        background_tasks = list(self._background_tasks)
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

    def _on_scheduler_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            self.logger.error("[XYZW] 后台巡检任务异常退出: %s", exc)

    def _track_background_task(self, task: asyncio.Task, label: str) -> None:
        self._background_tasks.add(task)
        task.set_name(label)

        def _cleanup(completed: asyncio.Task) -> None:
            self._background_tasks.discard(completed)
            if completed.cancelled():
                return
            try:
                completed.result()
            except Exception as exc:
                self.logger.error("[XYZW] 后台任务异常退出(%s): %s", label, exc)

        task.add_done_callback(_cleanup)

    def _get_user_id(self, event: AstrMessageEvent) -> str:
        return str(event.get_sender_id())

    def _get_forward_sender_uin(self) -> str | None:
        value = str(self._last_bot_self_id or "").strip()
        return value or None

    def _remember_bot_self_id(self, event: AstrMessageEvent) -> None:
        bot_self_id = str(event.get_self_id() or "").strip()
        if bot_self_id:
            self._last_bot_self_id = bot_self_id

    def _build_text_result(
        self,
        event: AstrMessageEvent,
        text: str,
    ):
        chain = self.notifier.build_message_chain(
            text=text,
            sender_uin=event.get_self_id(),
        )
        return event.chain_result(chain.chain)

    async def _push_text_to_session(
        self,
        session: str,
        text: str,
        sender_uin: str | None = None,
        fallback_group_id: str | None = None,
        fallback_user_id: str | None = None,
    ) -> bool:
        chain = self.notifier.build_message_chain(
            text=text,
            sender_uin=sender_uin,
        )
        session_text = str(session or "").strip()
        group_id = str(fallback_group_id or "").strip()
        user_id = str(fallback_user_id or "").strip()

        if session_text:
            try:
                sent = await StarTools.send_message(
                    session=session_text,
                    message_chain=chain,
                )
                if sent:
                    return True
            except Exception as exc:
                self.logger.error("[XYZW] 会话主动回发失败: %s", exc)

        if group_id:
            try:
                await StarTools.send_message_by_id(
                    type="GroupMessage",
                    id=group_id,
                    message_chain=chain,
                )
                return True
            except Exception as exc:
                self.logger.error("[XYZW] 群消息回发失败: %s", exc)

        if user_id:
            try:
                await StarTools.send_message_by_id(
                    type="PrivateMessage",
                    id=user_id,
                    message_chain=chain,
                )
                return True
            except Exception as exc:
                self.logger.error("[XYZW] 私聊回发失败: %s", exc)

        return False

    async def _push_private_chain(
        self,
        user_id: str,
        chain: list[Any],
    ) -> bool:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id or not chain:
            return False
        try:
            await StarTools.send_message_by_id(
                type="PrivateMessage",
                id=normalized_user_id,
                message_chain=chain,
            )
            return True
        except Exception as exc:
            self.logger.error("[XYZW] 私聊链式消息发送失败: %s", exc)
            return False

    def _is_daily_task_busy(self, account_id: str) -> bool:
        normalized = str(account_id or "").strip()
        if not normalized:
            return False
        return (
            normalized in self._pending_daily_task_account_ids
            or normalized in self._running_daily_task_account_ids
        )

    def _manual_daily_busy_message(self, alias: str) -> str:
        return (
            "当前账号的日常正在执行，请稍后等待统一结果回复。\n"
            f"- 别名: {alias or '-'}"
        )

    def _manual_daily_accepted_message(self, alias: str) -> str:
        return (
            "已收到日常请求，开始后台执行。\n"
            f"- 别名: {alias or '-'}\n"
            f"- 执行超时: {self.daily_task_timeout_ms} ms\n"
            "完成后会在当前会话统一回复结果。"
        )

    async def _run_manual_daily_task_background(
        self,
        account: dict[str, Any],
        session: str,
        fallback_group_id: str,
        fallback_user_id: str,
        sender_uin: str,
    ) -> None:
        account_id = str(account.get("account_id") or "")
        self._pending_daily_task_account_ids.discard(account_id)
        self._running_daily_task_account_ids.add(account_id)
        try:
            daily_options = self._build_daily_task_options(account)
            response = await self._call_with_account_token_ready(
                fallback_user_id,
                account,
                lambda ready_account: self.sidecar.run_daily_task(
                    ready_account.get("token", ""),
                    options=daily_options,
                    timeout_ms=self.daily_task_timeout_ms,
                ),
                reason="manual_daily",
            )
            if not response.get("ok"):
                text = (
                    "简版日常执行失败。\n"
                    f"- 别名: {account.get('alias')}\n"
                    f"- 错误: {response.get('message', '未知错误')}"
                )
            else:
                data = response.get("data", {}) or {}
                if not data.get("totalCount"):
                    text = (
                        "当前没有可执行的简版日常。\n"
                        f"账号: {account.get('alias')}"
                    )
                else:
                    text = self._format_daily_result_text(account, data)

            await self._push_text_to_session(
                session=session,
                text=text,
                sender_uin=sender_uin,
                fallback_group_id=fallback_group_id,
                fallback_user_id=fallback_user_id,
            )
        except Exception as exc:
            self.logger.error("[XYZW] 手动日常后台执行失败: %s", exc)
            await self._push_text_to_session(
                session=session,
                text=(
                    "简版日常执行失败。\n"
                    f"- 别名: {account.get('alias')}\n"
                    f"- 错误: {exc}"
                ),
                sender_uin=sender_uin,
                fallback_group_id=fallback_group_id,
                fallback_user_id=fallback_user_id,
            )
        finally:
            self._pending_daily_task_account_ids.discard(account_id)
            self._running_daily_task_account_ids.discard(account_id)

    async def _run_manual_scheduled_daily_background(
        self,
        user_id: str,
        account: dict[str, Any],
        schedule: dict[str, Any],
        session: str,
        fallback_group_id: str,
        fallback_user_id: str,
        sender_uin: str,
    ) -> None:
        account_id = str(account.get("account_id") or "")
        self._pending_daily_task_account_ids.discard(account_id)
        try:
            result = await self._run_daily_task_once(
                user_id=user_id,
                account=account,
                schedule=schedule,
                allow_notify=False,
                force_run=True,
            )
            daily_result = result.get("daily_result")
            if daily_result:
                text = self._format_daily_result_text(account, daily_result)
            else:
                text = self._format_daily_task_run_result(account, result)
            await self._push_text_to_session(
                session=session,
                text=text,
                sender_uin=sender_uin,
                fallback_group_id=fallback_group_id,
                fallback_user_id=fallback_user_id,
            )
        except Exception as exc:
            self.logger.error("[XYZW] 定时日常手动触发后台执行失败: %s", exc)
            await self._push_text_to_session(
                session=session,
                text=(
                    "定时日常手动执行失败。\n"
                    f"- 别名: {account.get('alias')}\n"
                    f"- 错误: {exc}"
                ),
                sender_uin=sender_uin,
                fallback_group_id=fallback_group_id,
                fallback_user_id=fallback_user_id,
            )
        finally:
            self._pending_daily_task_account_ids.discard(account_id)

    def _mask_token(self, token: str) -> str:
        token = (token or "").strip()
        if len(token) <= 16:
            return f"{token[:4]}***{token[-4:]}" if token else "***"
        return f"{token[:12]}...{token[-8:]}"

    def _build_alias_suggestion(self, summary: dict[str, Any]) -> str:
        role_name = (summary.get("roleName") or "").strip()
        server_name = (summary.get("serverName") or "").strip()
        role_id = summary.get("roleId")
        if role_name and server_name:
            return f"{server_name}-{role_name}"
        if role_name:
            return role_name
        if role_id:
            return f"role-{role_id}"
        return "我的账号"

    def _format_summary(self, summary: dict[str, Any]) -> str:
        if not summary:
            return "角色摘要: 无"

        lines = ["角色摘要"]
        lines.append(
            f"- 角色: {summary.get('roleName') or '-'}"
            f" (ID: {summary.get('roleId') or '-'})"
        )
        lines.append(f"- 服务器: {summary.get('serverName') or '-'}")
        lines.append(f"- 等级: {summary.get('level') or '-'}")
        lines.append(f"- VIP: {summary.get('vipLevel') or '-'}")
        if summary.get("legionName"):
            lines.append(f"- 俱乐部: {summary.get('legionName')}")
        if summary.get("hangUpMinutes") is not None:
            lines.append(f"- 挂机分钟: {summary.get('hangUpMinutes')}")
        return "\n".join(lines)

    def _format_local_account(self, account: dict[str, Any], is_default: bool) -> str:
        default_mark = " [默认]" if is_default else ""
        import_method = account.get("import_method") or "manual"
        import_method_label = {
            "manual": "token",
            "bin": "bin",
            "url": "url",
            "wx_qrcode": "wx",
        }.get(import_method, import_method)
        lines = [
            f"{account.get('alias', '-')}{default_mark}",
            f"  id: {account.get('account_id', '')[:8]}",
            f"  导入: {import_method_label}",
            f"  自动刷新: {'已启用' if str(account.get('source_url') or '').strip() else '未启用'}",
            f"  角色: {account.get('role_name') or '-'}",
            f"  服务器: {account.get('server_name') or '-'}",
            f"  等级: {account.get('level') or '-'}",
            "  token: "
            + (
                account.get("token_preview")
                or self._mask_token(account.get("token", ""))
            ),
        ]
        return "\n".join(lines)

    def _format_account_list(self, user_id: str) -> str:
        accounts = self.storage.list_accounts(user_id)
        if not accounts:
            return (
                "当前还没有绑定任何 XYZW 账号。\n\n"
                "使用 /xyzw 绑定 开始会话式绑定。"
            )

        default_account_id = self.storage.get_user_state(user_id).get(
            "default_account_id", ""
        )
        lines = [f"已绑定账号: {len(accounts)} 个"]
        for index, account in enumerate(accounts, start=1):
            lines.append(
                f"{index}. {self._format_local_account(account, account.get('account_id') == default_account_id)}"
            )
        lines.append("")
        lines.append("可用命令:")
        lines.append("/xyzw 账号 默认 <别名或ID前缀>")
        lines.append("/xyzw 账号 重命名 <别名或ID前缀> <新别名>")
        lines.append("/xyzw 账号 删除 <别名或ID前缀>")
        return "\n".join(lines)

    def _format_bind_result(
        self,
        account: dict[str, Any],
        summary: dict[str, Any],
        created: bool,
        is_default: bool,
    ) -> str:
        action = "已新增账号" if created else "已更新已有账号"
        default_text = "是" if is_default else "否"
        import_method = account.get("import_method") or "manual"
        import_method_label = {
            "manual": "token",
            "bin": "bin",
            "url": "url",
            "wx_qrcode": "wx",
        }.get(import_method, import_method)
        lines = [
            action,
            f"- 别名: {account.get('alias')}",
            f"- 账号ID: {account.get('account_id', '')[:8]}",
            f"- 导入方式: {import_method_label}",
            f"- 默认账号: {default_text}",
            f"- token: {account.get('token_preview')}",
        ]
        if str(account.get("source_url") or "").strip():
            lines.append("- 自动刷新: 已启用")
        lines.extend(["", self._format_summary(summary)])
        return "\n".join(lines)

    def _binding_usage(self) -> str:
        return (
            "账号绑定\n\n"
            "/xyzw 绑定\n"
            "/xyzw 绑定 token\n"
            "/xyzw 绑定 url\n"
            "/xyzw 绑定 bin\n"
            "/xyzw 绑定 wx\n\n"
            "token 绑定流程:\n"
            "1. 发送 WebSocket-ready token\n"
            "2. 发送账号别名\n"
            "3. 插件校验后写入当前 QQ 用户账号列表\n\n"
            "url 绑定流程:\n"
            "1. 发送返回 JSON 的 HTTP/HTTPS 地址\n"
            "2. 插件读取 token 字段并校验\n"
            "3. 发送账号别名\n"
            "4. 写入当前 QQ 用户账号列表\n\n"
            "bin 绑定流程:\n"
            "1. 发送 BIN 文件或 BIN 的 base64 文本\n"
            "2. 选择角色序号或 serverId\n"
            "3. 发送账号别名\n"
            "4. 插件转成 WebSocket-ready token 后写入账号列表\n\n"
            "wx 绑定流程:\n"
            "1. 私聊触发 /xyzw 绑定 wx\n"
            "2. 插件发送微信登录二维码\n"
            "3. 后台轮询扫码状态并拉取角色列表\n"
            "4. 选择角色后发送账号别名\n\n"
            "输入 取消 可随时结束绑定。"
        )

    def _binding_url_usage(self) -> str:
        return (
            "URL 绑定\n\n"
            "/xyzw 绑定 url\n\n"
            "流程:\n"
            "1. 发送返回 JSON 的 HTTP/HTTPS 地址\n"
            "2. 插件读取 token 字段并校验\n"
            "3. 发送账号别名\n\n"
            "要求:\n"
            "- 返回内容必须是 JSON\n"
            "- JSON 顶层可直接是原始 token 对象\n"
            "- 或包含 token 字段；也兼容 data.token\n\n"
            "输入 取消 可随时结束绑定。"
        )

    def _binding_bin_usage(self) -> str:
        return (
            "BIN 绑定\n\n"
            "/xyzw 绑定 bin\n\n"
            "流程:\n"
            "1. 发送 BIN 文件，或直接发送 BIN 的 base64 文本\n"
            "2. 插件解析角色列表\n"
            "3. 发送序号或 serverId 选择要绑定的角色\n"
            "4. 发送账号别名\n\n"
            "输入 取消 可随时结束绑定。"
        )

    def _binding_wx_usage(self) -> str:
        return (
            "微信扫码绑定\n\n"
            "/xyzw 绑定 wx\n\n"
            "流程:\n"
            "1. 仅支持私聊触发\n"
            "2. 插件发送微信登录二维码图片\n"
            "3. 后台每 1 秒轮询扫码状态\n"
            "4. 扫码成功后选择角色并发送账号别名\n\n"
            "说明:\n"
            "- 绑定成功后会为账号保存本地 refresh_url\n"
            "- 后续执行命令时会先校验 token，失效则自动从 refresh_url 刷新\n\n"
            "输入 取消 可随时结束绑定。"
        )

    def _account_manage_usage(self) -> str:
        return (
            "账号管理\n\n"
            "/xyzw 账号\n"
            "/xyzw 账号 默认 <别名或ID前缀>\n"
            "/xyzw 账号 重命名 <别名或ID前缀> <新别名>\n"
            "/xyzw 账号 删除 <别名或ID前缀>"
        )

    def _status_usage(self) -> str:
        return "用法: /xyzw 状态 [别名或ID前缀]"

    def _car_usage(self) -> str:
        return (
            "车辆命令\n\n"
            "/xyzw 车\n"
            "/xyzw 车 查看 [别名或ID前缀]\n"
            "/xyzw 车 护卫成员 [成员ID或名称关键字] [别名或ID前缀]\n"
            "/xyzw 车 发车 <车辆ID> [护卫 <护卫ID>] [别名或ID前缀]\n"
            "/xyzw 车 收车 [别名或ID前缀]\n\n"
            "说明: 品阶 >= 5 的车辆发车时必须提供护卫ID，低品级车辆可直接发车。\n"
            "发车时间限制: 仅周一至周三 06:00-20:00 可发车。"
        )

    def _daily_usage(self) -> str:
        return (
            "日常命令\n\n"
            "/xyzw 日常\n"
            "/xyzw 日常 [别名或ID前缀]\n\n"
            "/xyzw 日常 配置 查看 [别名或ID前缀]\n"
            "/xyzw 日常 配置 设置 招募次数 <次数> [别名或ID前缀]\n"
            "/xyzw 日常 配置 设置 挂机领取次数 <次数> [别名或ID前缀]\n"
            "/xyzw 日常 配置 设置 黑市购买次数 <次数> [别名或ID前缀]\n"
            "/xyzw 日常 配置 设置 竞技场次数 <1-3> [别名或ID前缀]\n"
            "/xyzw 日常 配置 重置 [别名或ID前缀]\n\n"
            "当前为简版基础日常：分享、好友金币、招募、点金、挂机、免费钓鱼、盐罐、签到、黑市、竞技场、任务奖励等。\n"
            "说明：招募次数 > 1 时，除首次免费招募外，其余次数会执行付费招募；竞技场次数支持 1-3 次；配置按账号隔离，手动日常与定时日常共用。"
        )

    def _normalize_daily_settings_field(self, raw_field: str) -> str:
        normalized = str(raw_field or "").strip().lower()
        mapping = {
            "招募次数": "recruit_count",
            "招募": "recruit_count",
            "recruit": "recruit_count",
            "recruit_count": "recruit_count",
            "recruitcount": "recruit_count",
            "挂机领取次数": "hangup_claim_count",
            "挂机奖励次数": "hangup_claim_count",
            "挂机次数": "hangup_claim_count",
            "挂机": "hangup_claim_count",
            "hangup": "hangup_claim_count",
            "hangupclaim": "hangup_claim_count",
            "hangup_claim_count": "hangup_claim_count",
            "黑市购买次数": "blackmarket_purchase_count",
            "黑市次数": "blackmarket_purchase_count",
            "黑市": "blackmarket_purchase_count",
            "blackmarket": "blackmarket_purchase_count",
            "blackmarket_purchase_count": "blackmarket_purchase_count",
            "blackmarketpurchasecount": "blackmarket_purchase_count",
            "竞技场次数": "arena_battle_count",
            "竞技场免费次数": "arena_battle_count",
            "竞技场": "arena_battle_count",
            "arena": "arena_battle_count",
            "arenabattle": "arena_battle_count",
            "arena_battle_count": "arena_battle_count",
            "arenabattlecount": "arena_battle_count",
        }
        return mapping.get(normalized, "")

    def _daily_settings_field_label(self, field: str) -> str:
        return {
            "recruit_count": "招募次数",
            "hangup_claim_count": "挂机领取次数",
            "blackmarket_purchase_count": "黑市购买次数",
            "arena_battle_count": "竞技场次数",
        }.get(str(field or "").strip(), str(field or "-"))

    def _format_daily_settings_text(
        self,
        account: dict[str, Any],
        settings: dict[str, Any],
    ) -> str:
        return (
            "日常配置\n"
            f"- 别名: {account.get('alias')}\n"
            f"- 招募次数: {int(settings.get('recruit_count') or 0)}\n"
            f"- 挂机领取次数: {int(settings.get('hangup_claim_count') or 0)}\n"
            f"- 黑市购买次数: {int(settings.get('blackmarket_purchase_count') or 0)}\n"
            f"- 竞技场次数: {int(settings.get('arena_battle_count') or 0)}\n\n"
            "说明:\n"
            "- 招募次数 > 1 时，首次为免费招募，剩余次数为付费招募。\n"
            "- 挂机领取次数 > 1 时，sidecar 会在可用范围内穿插挂机加钟后重复领取。\n"
            "- 免费钓鱼会在检测到当日可领取时自动尝试 3 次。\n"
            "- 竞技场次数支持 1-3 次，默认 1 次。\n"
            "- 配置按账号隔离，手动 `/xyzw 日常` 与 `定时日常` 共用同一份配置。"
        )

    def _build_daily_task_options(self, account: dict[str, Any]) -> dict[str, int]:
        settings = account.get("daily_settings") or {}
        return {
            "recruitCount": int(settings.get("recruit_count") or 0),
            "hangUpClaimCount": int(settings.get("hangup_claim_count") or 0),
            "blackMarketPurchaseCount": int(
                settings.get("blackmarket_purchase_count") or 0
            ),
            "arenaBattleCount": int(settings.get("arena_battle_count") or 0),
        }

    def _build_daily_settings_hints(
        self,
        account: dict[str, Any],
        task_items: list[dict[str, Any]],
    ) -> list[str]:
        incomplete_names = {
            str(item.get("name") or "").strip()
            for item in task_items
            if not item.get("completed")
        }
        settings = account.get("daily_settings") or {}
        hints: list[str] = []

        recruit_count = int(settings.get("recruit_count") or 0)
        if "进行2次招募" in incomplete_names and recruit_count < 2:
            hints.append(
                f"当前招募次数配置为 {recruit_count}，若希望完成“进行2次招募”，请执行 "
                f"`/xyzw 日常 配置 设置 招募次数 2 {account.get('alias')}`。"
            )

        hangup_claim_count = int(settings.get("hangup_claim_count") or 0)
        if "领取5次挂机奖励" in incomplete_names and hangup_claim_count < 5:
            hints.append(
                f"当前挂机领取次数配置为 {hangup_claim_count}，若希望完成“领取5次挂机奖励”，请执行 "
                f"`/xyzw 日常 配置 设置 挂机领取次数 5 {account.get('alias')}`。"
            )

        blackmarket_purchase_count = int(
            settings.get("blackmarket_purchase_count") or 0
        )
        if (
            "黑市购买1次物品（请设置采购清单）" in incomplete_names
            and blackmarket_purchase_count < 1
        ):
            hints.append(
                "当前黑市购买次数配置为 0，若希望完成“黑市购买1次物品”，请执行 "
                f"`/xyzw 日常 配置 设置 黑市购买次数 1 {account.get('alias')}`。"
            )

        return hints

    def _resource_usage(self) -> str:
        return (
            "资源命令\n\n"
            "/xyzw 资源 招募 [免费|付费] [别名或ID前缀]\n"
            "/xyzw 资源 钓鱼 [别名或ID前缀]\n"
            "/xyzw 资源 开箱 [数量] [别名或ID前缀]\n"
            "/xyzw 资源 珍宝阁 [别名或ID前缀]\n"
            "/xyzw 资源 黑市 [别名或ID前缀]\n"
            "/xyzw 资源 军团 四圣碎片 [别名或ID前缀]\n"
            "/xyzw 资源 每日礼包 [别名或ID前缀]\n\n"
            "当前基础版支持：免费招募、付费招募、免费钓鱼、木箱、珍宝阁免费、黑市采购、军团四圣碎片、每日礼包。\n"
            "注意：付费招募和开箱会消耗账号内对应资源。"
        )

    def _dungeon_usage(self) -> str:
        return (
            "副本命令\n\n"
            "/xyzw 副本 宝库 前3 [别名或ID前缀]\n"
            "/xyzw 副本 宝库 后2 [别名或ID前缀]\n"
            "/xyzw 副本 梦境 [阵容编号] [别名或ID前缀]\n"
            "/xyzw 副本 梦境 购买 [金币] [别名或ID前缀]\n"
            "/xyzw 副本 怪异塔 [查看] [别名或ID前缀]\n"
            "/xyzw 副本 怪异塔 免费道具 [别名或ID前缀]\n\n"
            "/xyzw 副本 怪异塔 用道具 [数量 <正整数>] [别名或ID前缀]\n"
            "/xyzw 副本 怪异塔 爬塔 [次数 <正整数>] [阵容 <编号>] [别名或ID前缀]\n\n"
            "/xyzw 副本 换皮 [查看] [别名或ID前缀]\n"
            "/xyzw 副本 换皮 补打 [别名或ID前缀]\n"
            "/xyzw 副本 换皮 挑战 [Boss编号] [别名或ID前缀]\n\n"
            "当前基础版支持：宝库前3、宝库后2、梦境阵容切换、梦境金币商品购买、怪异塔状态、怪异塔免费道具、怪异塔用道具、怪异塔限次爬塔、换皮闯关状态、换皮补打、换皮单 Boss 挑战。\n"
            "梦境阵容默认使用 107；梦境购买默认处理金币商品；怪异塔爬塔默认阵容 1、默认 5 次，当前上限 8 次。"
        )

    def _binding_permission_text(self) -> str:
        return "当前配置要求在私聊中完成账号绑定/删除等敏感操作。"

    def _extract_token_from_url_response(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None

        required_token_fields = {"roleToken", "sessId", "connId"}
        if required_token_fields.issubset(payload.keys()):
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        for candidate in (
            payload.get("token"),
            (payload.get("data") or {}).get("token")
            if isinstance(payload.get("data"), dict)
            else None,
        ):
            token = str(candidate or "").strip()
            if token:
                return token
        return None

    def _fetch_token_from_url_sync(
        self,
        source_url: str,
    ) -> tuple[str | None, str | None]:
        normalized_url = str(source_url or "").strip()
        if not normalized_url:
            return None, "URL 不能为空，请重新发送。"

        parsed = urlparse(normalized_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None, "仅支持有效的 HTTP/HTTPS 地址。"

        request = Request(
            normalized_url,
            method="GET",
            headers={
                "Accept": "application/json, text/plain;q=0.9, */*;q=0.1",
                "User-Agent": "astrbot-plugin-xyzw/0.21.0",
            },
        )

        try:
            with urlopen(request, timeout=self.sidecar.config.timeout_seconds) as response:
                raw_content = response.read(1024 * 1024 + 1)
                if len(raw_content) > 1024 * 1024:
                    return None, "URL 返回内容超过 1MB，已拒绝处理。"
                charset = response.headers.get_content_charset() or "utf-8"
        except HTTPError as exc:
            return None, f"URL 请求失败: HTTP {exc.code}"
        except URLError as exc:
            return None, f"URL 请求失败: {exc.reason}"
        except Exception as exc:
            return None, f"URL 请求失败: {exc}"

        try:
            payload = json.loads(raw_content.decode(charset, errors="replace"))
        except (json.JSONDecodeError, LookupError, UnicodeDecodeError):
            return None, "URL 返回内容不是有效 JSON。"

        token = self._extract_token_from_url_response(payload)
        if not token:
            return None, "URL 返回 JSON 中未找到 token 字段。"
        return token, None

    async def _fetch_token_from_url(
        self,
        source_url: str,
    ) -> tuple[str | None, str | None]:
        return await asyncio.to_thread(self._fetch_token_from_url_sync, source_url)

    def _build_source_url_from_payload(
        self,
        source_payload: dict[str, Any],
        selected_role: dict[str, Any] | None = None,
    ) -> str:
        refresh_url = str(source_payload.get("refreshUrl") or "").strip()
        refresh_url_template = str(source_payload.get("refreshUrlTemplate") or "").strip()
        requires_server_id = bool(source_payload.get("requiresServerId"))
        if not requires_server_id:
            return refresh_url or refresh_url_template

        server_id = ""
        if selected_role:
            server_id = str(selected_role.get("serverId") or "").strip()
        if not server_id:
            return refresh_url_template
        if "{serverId}" in refresh_url_template:
            return refresh_url_template.replace("{serverId}", server_id)
        separator = "&" if "?" in refresh_url else "?"
        return f"{refresh_url}{separator}server_id={server_id}"

    def _finalize_source_url_for_role(
        self,
        source_url: str | None,
        selected_role: dict[str, Any] | None = None,
    ) -> str | None:
        normalized = str(source_url or "").strip()
        if not normalized:
            return None
        if "{serverId}" not in normalized:
            return normalized
        server_id = ""
        if selected_role:
            server_id = str(selected_role.get("serverId") or "").strip()
        if not server_id:
            return normalized
        return normalized.replace("{serverId}", server_id)

    async def _register_bin_source_url(
        self,
        bin_base64: str,
        selected_role: dict[str, Any] | None = None,
        source_type: str = "bin",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str | None, str | None]:
        response = await self.sidecar.register_bin_source(
            bin_base64,
            source_type=source_type,
            metadata=metadata,
        )
        if not response.get("ok"):
            return None, response.get("message", "未知错误")
        source_payload = (response.get("data", {}) or {}).get("source", {}) or {}
        source_url = self._build_source_url_from_payload(source_payload, selected_role)
        if not source_url:
            return None, "sidecar 未返回有效的 refresh_url"
        return source_url, None

    def _can_auto_refresh_account(self, account: dict[str, Any]) -> bool:
        return bool(str(account.get("source_url") or "").strip())

    def _is_token_issue_message(self, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        keywords = (
            "token",
            "expired",
            "websocket",
            "1006",
            "handshake",
            "403",
            "forbidden",
            "verify failed",
        )
        return any(keyword in normalized for keyword in keywords)

    def _response_indicates_token_issue(self, response: dict[str, Any] | None) -> bool:
        if not isinstance(response, dict):
            return False
        if response.get("ok", True):
            return False
        candidates = [
            response.get("message"),
            response.get("error"),
            (response.get("data", {}) or {}).get("message")
            if isinstance(response.get("data"), dict)
            else None,
            (response.get("data", {}) or {}).get("error")
            if isinstance(response.get("data"), dict)
            else None,
        ]
        return any(self._is_token_issue_message(candidate or "") for candidate in candidates)

    async def _refresh_account_token_from_source(
        self,
        user_id: str,
        account: dict[str, Any],
        reason: str = "",
    ) -> tuple[dict[str, Any] | None, str | None]:
        account_id = str(account.get("account_id") or "").strip()
        source_url = str(account.get("source_url") or "").strip()
        if not source_url:
            return None, "当前账号未配置自动刷新来源。"

        now_iso = self._now_iso()
        token, fetch_error = await self._fetch_token_from_url(source_url)
        if not token:
            self.storage.update_account_runtime(
                user_id,
                account_id,
                last_token_refresh_error=fetch_error or "未知错误",
                last_token_refresh_error_at=now_iso,
            )
            return None, fetch_error or "URL 刷新失败"

        verify_response = await self.sidecar.verify_token(token)
        if not verify_response.get("ok"):
            error_text = verify_response.get("message", "未知错误")
            self.storage.update_account_runtime(
                user_id,
                account_id,
                last_token_refresh_error=error_text,
                last_token_refresh_error_at=now_iso,
            )
            return None, f"刷新后的 token 校验失败: {error_text}"

        verify_data = verify_response.get("data", {}) or {}
        if not verify_data.get("verified"):
            error_text = "刷新后的 token 校验未通过"
            self.storage.update_account_runtime(
                user_id,
                account_id,
                last_token_refresh_error=error_text,
                last_token_refresh_error_at=now_iso,
            )
            return None, error_text

        summary = verify_data.get("summary", {}) or {}
        refreshed_account = self.storage.update_account_credentials(
            user_id=user_id,
            account_id=account_id,
            token=token,
            summary=summary,
            refreshed_at=now_iso,
        )
        self.storage.update_account_runtime(
            user_id,
            account_id,
            last_token_checked_at=now_iso,
        )
        refreshed_account["refresh_reason"] = reason
        return refreshed_account, None

    async def _ensure_account_token_ready(
        self,
        user_id: str,
        account: dict[str, Any],
        reason: str = "",
    ) -> tuple[dict[str, Any] | None, str | None]:
        account_id = str(account.get("account_id") or "").strip()
        token = str(account.get("token") or "").strip()
        if not token:
            return None, "当前账号缺少 token，请重新绑定。"

        now_iso = self._now_iso()
        verify_response = await self.sidecar.verify_token(token)
        if verify_response.get("ok") and (verify_response.get("data", {}) or {}).get(
            "verified"
        ):
            verify_data = verify_response.get("data", {}) or {}
            summary = verify_data.get("summary", {}) or {}
            self.storage.update_account_runtime(
                user_id,
                account_id,
                last_token_checked_at=now_iso,
                role_id=summary.get("roleId") or account.get("role_id"),
                role_name=summary.get("roleName") or account.get("role_name"),
                server_name=summary.get("serverName") or account.get("server_name"),
                level=summary.get("level") or account.get("level"),
                vip_level=summary.get("vipLevel") or account.get("vip_level"),
            )
            return account, None

        failure_message = verify_response.get("message", "token 校验失败")
        if not self._can_auto_refresh_account(account):
            self.storage.update_account_runtime(
                user_id,
                account_id,
                last_token_checked_at=now_iso,
                last_token_refresh_error=failure_message,
                last_token_refresh_error_at=now_iso,
            )
            return None, f"token 校验失败，且当前账号没有可用刷新来源: {failure_message}"

        refreshed_account, refresh_error = await self._refresh_account_token_from_source(
            user_id,
            account,
            reason=reason or failure_message,
        )
        if not refreshed_account:
            return None, refresh_error or failure_message
        return refreshed_account, None

    async def _call_with_account_token_ready(
        self,
        user_id: str,
        account: dict[str, Any],
        operation,
        reason: str = "",
        retry_on_token_issue: bool = True,
    ) -> dict[str, Any]:
        ready_account, error_text = await self._ensure_account_token_ready(
            user_id,
            account,
            reason=reason,
        )
        if not ready_account:
            return {
                "ok": False,
                "code": "TOKEN_NOT_READY",
                "message": error_text or "token 不可用",
            }

        response = await operation(ready_account)
        if retry_on_token_issue and self._response_indicates_token_issue(response):
            refreshed_account, refresh_error = await self._refresh_account_token_from_source(
                user_id,
                ready_account,
                reason=f"retry:{reason or 'operation'}",
            )
            if refreshed_account:
                response = await operation(refreshed_account)
            elif refresh_error:
                return {
                    "ok": False,
                    "code": "TOKEN_REFRESH_FAILED",
                    "message": refresh_error,
                }
        return response

    def _format_bind_role_choices(self, roles: list[dict[str, Any]]) -> str:
        if not roles:
            return "未解析到可绑定角色。"

        lines = [f"从 BIN 解析到 {len(roles)} 个角色："]
        preview_roles = roles[:12]
        for index, role in enumerate(preview_roles, start=1):
            lines.append(
                f"{index}. serverId={role.get('serverId') or '-'}"
                f"  角色={role.get('name') or '-'}"
                f"  roleId={role.get('roleId') or '-'}"
                f"  等级={role.get('level') or '-'}"
                f"  战力={role.get('power') or '-'}"
            )
        if len(roles) > len(preview_roles):
            lines.append("")
            lines.append(
                f"仅展开前 {len(preview_roles)} 个角色；也可以发送 s<serverId> 进行选择。"
            )
        lines.append("")
        lines.append("请发送序号。若按 serverId 选择，请发送 s<serverId>，例如 s101。")
        return "\n".join(lines)

    def _select_bind_role(
        self,
        roles: list[dict[str, Any]],
        selector: str,
    ) -> dict[str, Any] | None:
        normalized = (selector or "").strip()
        if not normalized:
            return None

        lowered = normalized.lower()
        if lowered.startswith("serverid="):
            normalized = normalized.split("=", 1)[1].strip()
            lowered = normalized.lower()
        elif lowered.startswith("server:"):
            normalized = normalized.split(":", 1)[1].strip()
            lowered = normalized.lower()
        elif lowered.startswith("s") and lowered[1:].isdigit():
            normalized = lowered[1:]

        matched_server_roles = [
            role
            for role in roles
            if str(role.get("serverId") or "").strip() == normalized
        ]
        if len(matched_server_roles) == 1:
            return matched_server_roles[0]

        if normalized.isdigit():
            index = int(normalized)
            if 1 <= index <= len(roles):
                return roles[index - 1]

        matched_role_ids = [
            role for role in roles if str(role.get("roleId") or "").strip() == normalized
        ]
        if len(matched_role_ids) == 1:
            return matched_role_ids[0]

        return None

    async def _extract_bin_base64_from_event(
        self,
        event: AstrMessageEvent,
    ) -> tuple[str | None, str | None]:
        for component in event.message_obj.message:
            if not isinstance(component, Comp.File):
                continue
            try:
                file_path = await component.get_file()
                if not file_path:
                    continue
                with open(file_path, "rb") as file:
                    content = file.read()
            except Exception as exc:
                return None, f"读取 BIN 文件失败: {exc}"

            if not content:
                return None, "BIN 文件为空，请重新发送。"
            return base64.b64encode(content).decode("ascii"), None

        text = (event.message_str or "").strip()
        if not text:
            return None, "请发送 BIN 文件，或直接发送 BIN 的 base64 文本。"

        normalized_text = text.replace("```", "").replace("`", "").strip()
        if normalized_text.lower().startswith("base64:"):
            normalized_text = normalized_text.split(":", 1)[1].strip()
        return normalized_text, None

    async def _resolve_bin_binding_token(
        self,
        bin_base64: str,
        selected_role: dict[str, Any],
    ) -> tuple[str | None, dict[str, Any] | None, str | None]:
        server_id = selected_role.get("serverId")
        if server_id in {None, ""}:
            return None, None, "目标角色缺少 serverId，无法继续绑定。"

        auth_response = await self.sidecar.authuser_from_bin_base64(bin_base64, server_id)
        if not auth_response.get("ok"):
            return (
                None,
                None,
                f"BIN 转 token 失败: {auth_response.get('message', '未知错误')}",
            )

        token_data = auth_response.get("data", {}).get("token", {}) or {}
        ws_ready_token = str(token_data.get("wsReadyToken") or "").strip()
        if not ws_ready_token:
            return None, None, "sidecar 未返回有效的 WebSocket-ready token。"

        verify_response = await self.sidecar.verify_token(ws_ready_token)
        if not verify_response.get("ok"):
            return (
                None,
                None,
                f"token 校验失败: {verify_response.get('message', '未知错误')}",
            )

        verify_data = verify_response.get("data", {})
        if not verify_data.get("verified"):
            return None, None, "token 校验未通过，请重新导入 BIN。"

        return ws_ready_token, verify_data.get("summary", {}) or {}, None

    def _resolve_account_or_text(
        self,
        user_id: str,
        selector: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        account = self.storage.resolve_account(user_id, selector or None)
        if account:
            return account, None

        if self.storage.list_accounts(user_id):
            return (
                None,
                "未找到目标账号。\n请使用 /xyzw 账号 查看当前账号列表，并使用别名或 ID 前缀选择账号。",
            )
        return None, "当前没有已绑定账号。\n请先使用 /xyzw 绑定 开始绑定。"

    async def _handle_daily_config(
        self,
        event: AstrMessageEvent,
        tokens,
    ):
        user_id = self._get_user_id(event)
        args = [
            str(token or "").strip()
            for token in tokens.tokens[3:]
            if str(token or "").strip()
        ]
        if not args:
            account, error_text = self._resolve_account_or_text(user_id, "")
            if not account:
                yield event.plain_result(error_text or self._daily_usage())
                return
            yield event.plain_result(
                self._format_daily_settings_text(
                    account,
                    self.storage.get_daily_settings(
                        user_id,
                        str(account.get("account_id") or ""),
                    ),
                )
            )
            return

        action = args[0].lower()
        if action in {"帮助", "help"}:
            yield event.plain_result(self._daily_usage())
            return

        if action in {"查看", "view", "list"}:
            selector = " ".join(args[1:]).strip()
            account, error_text = self._resolve_account_or_text(user_id, selector)
            if not account:
                yield event.plain_result(error_text or self._daily_usage())
                return
            settings = self.storage.get_daily_settings(
                user_id,
                str(account.get("account_id") or ""),
            )
            yield event.plain_result(self._format_daily_settings_text(account, settings))
            return

        if action in {"重置", "reset"}:
            selector = " ".join(args[1:]).strip()
            account, error_text = self._resolve_account_or_text(user_id, selector)
            if not account:
                yield event.plain_result(error_text or self._daily_usage())
                return
            settings = self.storage.reset_daily_settings(
                user_id,
                str(account.get("account_id") or ""),
            )
            yield event.plain_result(
                "已重置日常配置。\n\n"
                + self._format_daily_settings_text(account, settings)
            )
            return

        if action not in {"设置", "set"} or len(args) < 3:
            yield event.plain_result(self._daily_usage())
            return

        field = self._normalize_daily_settings_field(args[1])
        if not field:
            yield event.plain_result(
                "不支持的日常配置项。\n"
                "当前支持：招募次数、挂机领取次数、黑市购买次数。"
            )
            return

        value_text = str(args[2] or "").strip()
        if not value_text.lstrip("-").isdigit():
            yield event.plain_result("配置值必须是整数。")
            return

        selector = " ".join(args[3:]).strip()
        account, error_text = self._resolve_account_or_text(user_id, selector)
        if not account:
            yield event.plain_result(error_text or self._daily_usage())
            return

        settings = self.storage.update_daily_settings(
            user_id,
            str(account.get("account_id") or ""),
            **{field: int(value_text)},
        )
        yield event.plain_result(
            "已更新日常配置。\n"
            f"- 别名: {account.get('alias')}\n"
            f"- 字段: {self._daily_settings_field_label(field)}\n"
            f"- 新值: {settings.get(field)}\n\n"
            + self._format_daily_settings_text(account, settings)
        )

    def _resolve_car_helper_query(
        self,
        user_id: str,
        raw_tokens: list[str],
    ) -> tuple[dict[str, Any] | None, str, str | None]:
        normalized_tokens = [
            str(token or "").strip()
            for token in raw_tokens
            if str(token or "").strip()
        ]
        if normalized_tokens and normalized_tokens[0].lower() in {
            "查看",
            "状态",
            "查询",
            "list",
        }:
            normalized_tokens = normalized_tokens[1:]

        account: dict[str, Any] | None = None
        member_selector = ""
        if normalized_tokens:
            trailing_selector = normalized_tokens[-1]
            account = self.storage.resolve_account(user_id, trailing_selector)
            if account:
                member_selector = " ".join(normalized_tokens[:-1]).strip()
            else:
                member_selector = " ".join(normalized_tokens).strip()

        if not account:
            account, error_text = self._resolve_account_or_text(user_id, "")
            if not account:
                return None, member_selector, error_text
            return account, member_selector, None

        return account, member_selector, None

    def _build_car_helper_member_ids(
        self,
        selector: str,
    ) -> list[str] | None:
        normalized = str(selector or "").replace("，", ",").strip()
        if not normalized:
            return None
        parts = [item.strip() for item in normalized.split(",") if item.strip()]
        if parts and all(part.isdigit() for part in parts):
            return parts
        return None

    def _format_car_overview_text(
        self,
        account: dict[str, Any],
        overview: dict[str, Any],
    ) -> str:
        summary = overview.get("summary", {}) or {}
        cars = overview.get("cars", []) or []
        claimable_cars = [car for car in cars if car.get("claimable")]
        idle_cars = [car for car in cars if car.get("status") == "idle"]
        running_cars = [car for car in cars if car.get("status") == "running"]

        lines = [
            "车辆概览",
            f"- 别名: {account.get('alias')}",
            f"- 账号ID: {account.get('account_id', '')[:8]}",
            f"- 总数: {summary.get('totalCars', 0)}",
            f"- 空闲: {summary.get('idleCars', 0)}",
            f"- 行驶中: {summary.get('runningCars', 0)}",
            f"- 可收取: {summary.get('claimableCars', 0)}",
            f"- 最高品质: {summary.get('highestColor', 0)}",
        ]

        if idle_cars:
            lines.append("")
            lines.append("可发车车辆:")
            for index, car in enumerate(idle_cars[:10], start=1):
                lines.append(
                    f"{index}. {car.get('gradeLabel', '未知')} id={car.get('id')}"
                )
            if len(idle_cars) > 10:
                lines.append(f"... 其余 {len(idle_cars) - 10} 辆未展开")
            car_send_block_reason = self._get_car_send_block_reason()
            if car_send_block_reason:
                lines.append("当前时段不可发车。")
                lines.append(car_send_block_reason)
            else:
                lines.append(
                    "可使用 /xyzw 车 发车 <车辆ID> [护卫 <护卫ID>] "
                    f"{account.get('alias')} 发车。"
                )
                lines.append(
                    "可使用 /xyzw 车 护卫成员 "
                    f"{account.get('alias')} 查看当前俱乐部成员护卫次数。"
                )

        if claimable_cars:
            lines.append("")
            lines.append("可收取车辆:")
            for index, car in enumerate(claimable_cars[:10], start=1):
                lines.append(
                    f"{index}. {car.get('gradeLabel', '未知')} id={car.get('id')}"
                )
            if len(claimable_cars) > 10:
                lines.append(f"... 其余 {len(claimable_cars) - 10} 辆未展开")
        elif running_cars:
            nearest = min(
                [int(car.get("claimableInSeconds") or 0) for car in running_cars if car.get("claimableInSeconds")],
                default=0,
            )
            if nearest > 0:
                lines.append("")
                lines.append(f"当前没有可收取车辆，最近一辆约 {nearest} 秒后可收取。")
            else:
                lines.append("")
                lines.append("当前没有可收取车辆。")
        else:
            lines.append("")
            lines.append("当前没有在途车辆。")

        return "\n".join(lines)

    def _format_car_send_result(
        self,
        account: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        sent_car = result.get("sentCar", {}) or {}
        after_summary = (result.get("after") or {}).get("summary", {}) or {}

        lines = [
            "车辆发车结果",
            f"- 别名: {account.get('alias')}",
            f"- 车辆ID: {result.get('carId') or '-'}",
            f"- 品质: {sent_car.get('gradeLabel') or '-'}",
            f"- 状态: {'已发车' if sent_car.get('status') == 'running' else '未知'}",
            f"- 发车后空闲: {after_summary.get('idleCars', 0)}",
            f"- 发车后行驶中: {after_summary.get('runningCars', 0)}",
            f"- 发车后可收取: {after_summary.get('claimableCars', 0)}",
        ]
        helper_id = result.get("helperId")
        if helper_id:
            lines.append(f"- 护卫ID: {helper_id}")
        lines.append("")
        lines.append("已发车，可在约 4 小时后使用 /xyzw 车 收车 处理。")
        return "\n".join(lines)

    def _format_car_helper_members_text(
        self,
        account: dict[str, Any],
        result: dict[str, Any],
        selector: str = "",
    ) -> str:
        summary = result.get("summary", {}) or {}
        helpers = result.get("helpers", []) or []
        current_role = result.get("currentRole", {}) or {}
        lines = [
            "护卫成员状态",
            f"- 别名: {account.get('alias')}",
            f"- 账号ID: {account.get('account_id', '')[:8]}",
            f"- 当前角色ID: {current_role.get('roleId') or '-'}",
            f"- 成员总数: {summary.get('totalMembers', 0)}",
            f"- 匹配数量: {summary.get('matchedMembers', len(helpers))}",
            f"- 可用成员: {summary.get('availableMembers', 0)}",
            f"- 已满成员: {summary.get('exhaustedMembers', 0)}",
            f"- 单成员上限: {summary.get('maxUsagePerMember', 4)}",
        ]
        if selector:
            lines.append(f"- 筛选: {selector}")

        if not helpers:
            lines.append("")
            lines.append("当前没有匹配的护卫成员。")
            lines.append(
                "可使用 /xyzw 车 护卫成员 [成员ID或名称关键字] [别名或ID前缀] 重新查询。"
            )
            return "\n".join(lines)

        lines.append("")
        lines.append("护卫成员:")
        for index, helper in enumerate(helpers, start=1):
            lines.append(
                f"{index}. {helper.get('displayName') or helper.get('roleId')}"
                f" (ID: {helper.get('roleId') or '-'})"
            )
            lines.append(
                "   "
                f"状态: {'可用' if helper.get('isAvailable') else '已满'}"
                f" | 已护卫 {helper.get('usedCount', 0)}/{helper.get('maxCount', 4)}"
                f" | 剩余 {helper.get('availableCount', 0)}"
            )
            lines.append(
                "   "
                f"战力: {helper.get('power', 0) or 0}"
                f" | 红粹: {helper.get('redQuench', 0) or 0}"
            )
        return "\n".join(lines)

    def _find_car_in_overview(
        self,
        overview: dict[str, Any],
        car_id: str | int,
    ) -> dict[str, Any] | None:
        normalized_car_id = str(car_id or "").strip()
        if not normalized_car_id:
            return None

        cars = overview.get("cars", []) or []
        for car in cars:
            if str(car.get("id") or "").strip() == normalized_car_id:
                return car
        return None

    def _car_requires_helper(self, car: dict[str, Any] | None) -> bool:
        if not isinstance(car, dict):
            return False
        return int(car.get("color") or 0) >= 5

    def _format_weekday_cn(self, weekday: int) -> str:
        return {
            0: "周一",
            1: "周二",
            2: "周三",
            3: "周四",
            4: "周五",
            5: "周六",
            6: "周日",
        }.get(weekday, f"星期{weekday}")

    def _car_send_window_text(self) -> str:
        return "周一至周三 06:00-20:00"

    def _is_car_activity_open_now(self, value: datetime | None = None) -> bool:
        current = value or datetime.now().astimezone()
        weekday = current.astimezone().weekday()
        return weekday in {0, 1, 2}

    def _is_car_send_open_now(self, value: datetime | None = None) -> bool:
        current = value or datetime.now().astimezone()
        localized = current.astimezone()
        if not self._is_car_activity_open_now(localized):
            return False
        return 6 <= localized.hour < 20

    def _get_car_send_block_reason(self, value: datetime | None = None) -> str | None:
        current = value or datetime.now().astimezone()
        localized = current.astimezone()
        if self._is_car_send_open_now(localized):
            return None

        current_text = localized.strftime("%Y-%m-%d %H:%M")
        weekday_text = self._format_weekday_cn(localized.weekday())
        if not self._is_car_activity_open_now(localized):
            return (
                f"当前时间: {current_text} ({weekday_text})\n"
                f"车辆发车仅在 {self._car_send_window_text()} 开放，当前只能查询/收车。"
            )
        if localized.hour < 6:
            return (
                f"当前时间: {current_text} ({weekday_text})\n"
                "每日 06:00 前不可发车，当前只能查询/收车。"
            )
        return (
            f"当前时间: {current_text} ({weekday_text})\n"
            "每日 20:00 后不可发车，当前只能查询/收车。"
        )

    def _format_car_claim_result(
        self,
        account: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        claimed_cars = result.get("claimedCars", []) or []
        failures = result.get("failures", []) or []
        after_summary = (result.get("after") or {}).get("summary", {}) or {}

        lines = [
            "一键收车结果",
            f"- 别名: {account.get('alias')}",
            f"- 成功收取: {result.get('claimedCount', 0)}",
            f"- 失败: {len(failures)}",
            f"- 收取后可收取: {after_summary.get('claimableCars', 0)}",
            f"- 收取后行驶中: {after_summary.get('runningCars', 0)}",
        ]

        if claimed_cars:
            lines.append("")
            lines.append("本次收取:")
            for index, car in enumerate(claimed_cars[:10], start=1):
                lines.append(
                    f"{index}. {car.get('gradeLabel', '未知')} id={car.get('id')}"
                )
            if len(claimed_cars) > 10:
                lines.append(f"... 其余 {len(claimed_cars) - 10} 辆未展开")

        if failures:
            lines.append("")
            lines.append("失败车辆:")
            for index, item in enumerate(failures[:10], start=1):
                lines.append(f"{index}. id={item.get('id')} - {item.get('message')}")
            if len(failures) > 10:
                lines.append(f"... 其余 {len(failures) - 10} 辆未展开")

        return "\n".join(lines)

    def _format_daily_result_text(
        self,
        account: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        summary = result.get("summary", {}) or {}
        steps = result.get("steps", []) or []
        failed_steps = [step for step in steps if step.get("status") == "failed"]
        skipped_steps = [step for step in steps if step.get("status") == "skipped"]
        task_snapshot = (
            result.get("finalDailyTaskSnapshot")
            or result.get("initialDailyTaskSnapshot")
            or {}
        )
        task_items = task_snapshot.get("tasks", []) or []

        lines = [
            "日常结果",
            f"- 别名: {account.get('alias')}",
            f"- 角色: {summary.get('roleName') or account.get('role_name') or '-'}",
            f"- 服务器: {summary.get('serverName') or account.get('server_name') or '-'}",
            f"- 总步骤: {result.get('totalCount', 0)}",
            f"- 成功: {result.get('successCount', 0)}",
            f"- 跳过: {result.get('skippedCount', 0)}",
            f"- 失败: {result.get('failedCount', 0)}",
        ]

        if task_snapshot:
            lines.append(
                f"- 每日积分: {task_snapshot.get('dailyPoint', 0)}/{task_snapshot.get('maxDailyPoint', 100)}"
            )
            lines.append(
                f"- 任务完成: {task_snapshot.get('completedCount', 0)}/{task_snapshot.get('totalCount', len(task_items))}"
            )

        if task_items:
            lines.append("")
            lines.append("每日任务详情")
            for item in task_items:
                lines.append(
                    f"{str(item.get('name') or '-')}"
                    f" - {'已完成' if item.get('completed') else '未完成'}"
                )

            setting_hints = self._build_daily_settings_hints(account, task_items)
            if setting_hints:
                lines.append("")
                lines.append("配置提示")
                lines.extend(setting_hints)

        if failed_steps:
            lines.append("")
            lines.append("失败步骤:")
            for index, step in enumerate(failed_steps, start=1):
                lines.append(
                    f"{index}. {step.get('description')} - {step.get('message') or step.get('code') or '未知错误'}"
                )

        if skipped_steps:
            lines.append("")
            lines.append("已跳过步骤:")
            for index, step in enumerate(skipped_steps, start=1):
                lines.append(
                    f"{index}. {step.get('description')} - {step.get('message') or step.get('code') or '已跳过'}"
                )

        if not failed_steps and not skipped_steps:
            lines.append("")
            lines.append("本次步骤均执行成功。")

        return "\n".join(lines)

    def _format_resource_result_text(
        self,
        account: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        params = result.get("params", {}) or {}
        lines = [
            "资源命令结果",
            f"- 别名: {account.get('alias')}",
            f"- 动作: {result.get('label') or result.get('action') or '-'}",
            f"- 命令: {result.get('command') or '-'}",
            f"- code: {result.get('code', 0)}",
        ]

        if result.get("action") == "open_wood_box":
            lines.append(f"- 数量: {params.get('number', '-')}")

        if result.get("action") == "recruit_free":
            lines.append("- 类型: 免费招募")
        elif result.get("action") == "recruit_paid":
            lines.append("- 类型: 付费招募")
        elif result.get("action") == "blackmarket_purchase":
            lines.append("- 类型: 黑市采购")
        elif result.get("action") == "legion_holy_shards":
            lines.append("- 类型: 军团四圣碎片")

        lines.append("")
        lines.append("执行完成。")
        return "\n".join(lines)

    def _format_dungeon_result_text(
        self,
        account: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        lines = [
            "副本命令结果",
            f"- 别名: {account.get('alias')}",
            f"- 动作: {result.get('label') or result.get('action') or '-'}",
            f"- code: {result.get('code', 0)}",
        ]

        if result.get("towerId") is not None:
            lines.append(f"- 当前塔层: {result.get('towerId')}")

        if result.get("action") in {"bosstower_low", "bosstower_high"}:
            lines.append(f"- BOSS 战斗: {result.get('executedBossBattles', 0)}")
            lines.append(f"- 宝箱开启: {result.get('executedBoxOpens', 0)}")

        if result.get("action") == "dream_team":
            lines.append(f"- 阵容: {result.get('teamId', '-')}")

        if result.get("action") == "dream_purchase":
            preset_label = (
                "金币商品"
                if result.get("purchasePreset") == "gold_items"
                else "自定义"
            )
            lines.append(f"- 购买预设: {preset_label}")
            lines.append(f"- 关卡数: {result.get('levelId', 0)}")
            lines.append(f"- 计划商品: {result.get('requestedItemCount', 0)}")
            lines.append(f"- 匹配货位: {result.get('matchedOperationCount', 0)}")
            lines.append(f"- 成功次数: {result.get('successCount', 0)}")
            lines.append(f"- 失败次数: {result.get('failCount', 0)}")
            lines.append(f"- 未上架商品: {result.get('unavailableItemCount', 0)}")
            purchase_results = result.get("purchaseResults", []) or []
            if purchase_results:
                lines.append("")
                lines.append("购买结果:")
                for index, item in enumerate(purchase_results[:6], start=1):
                    status = "成功" if item.get("success") else "失败"
                    lines.append(
                        f"{index}. {item.get('merchantName') or '-'} - "
                        f"{item.get('itemName') or '-'} - {status}"
                    )
                if len(purchase_results) > 6:
                    lines.append(f"... 其余 {len(purchase_results) - 6} 项未展开")
            unavailable_items = result.get("unavailableItems", []) or []
            if unavailable_items:
                lines.append("")
                lines.append("未上架商品:")
                for index, item in enumerate(unavailable_items[:4], start=1):
                    lines.append(
                        f"{index}. {item.get('merchantName') or '-'} - "
                        f"{item.get('itemName') or '-'}"
                    )
                if len(unavailable_items) > 4:
                    lines.append(f"... 其余 {len(unavailable_items) - 4} 项未展开")

        if result.get("action") == "weirdtower_overview":
            lines.append(f"- 当前章节: {result.get('chapter', 0)}")
            lines.append(f"- 当前层数: {result.get('floor', 0)}")
            lines.append(f"- 当前 towerId: {result.get('towerId', 0)}")
            lines.append(f"- 剩余能量: {result.get('energy', 0)}")
            lines.append(f"- 剩余道具: {result.get('lotteryLeftCnt', 0)}")
            lines.append(f"- 可领免费道具: {result.get('freeEnergy', 0)}")
            claimed_task_ids = result.get("claimedTaskIds", []) or []
            lines.append(
                "- 今日已领奖励任务: "
                + (",".join(str(task_id) for task_id in claimed_task_ids) if claimed_task_ids else "无")
            )

        if result.get("action") == "weirdtower_claim_free_energy":
            lines.append(f"- 领取数量: {result.get('freeEnergyClaimed', 0)}")

        if result.get("action") == "weirdtower_use_items":
            before = result.get("before", {}) or {}
            after = result.get("after", {}) or {}
            lines.append(f"- 计划数量: {result.get('targetUses', 0)}")
            lines.append(f"- 实际数量: {result.get('processedCount', 0)}")
            lines.append(f"- 使用前道具: {before.get('lotteryLeftCnt', 0)}")
            lines.append(f"- 使用后道具: {after.get('lotteryLeftCnt', 0)}")
            lines.append(f"- 使用前累计: {before.get('mergeCostTotalCnt', 0)}")
            lines.append(f"- 使用后累计: {after.get('mergeCostTotalCnt', 0)}")
            lines.append(
                f"- 累计奖励尝试: {'是' if result.get('claimCostProgressAttempted') else '否'}"
            )
            lines.append(
                f"- 累计奖励成功: {'是' if result.get('claimCostProgressSuccess') else '否'}"
            )

        if result.get("action") == "weirdtower_climb":
            before = result.get("before", {}) or {}
            after = result.get("after", {}) or {}
            lines.append(f"- 使用阵容: {result.get('teamId', '-')}")
            lines.append(f"- 计划次数: {result.get('maxFights', '-')}")
            lines.append(f"- 实际次数: {result.get('executedFightCount', 0)}")
            lines.append(f"- 切换阵容: {'是' if result.get('switchedFormation') else '否'}")
            lines.append(
                f"- 恢复原阵容: {'是' if result.get('restoredFormation') else '否'}"
            )
            lines.append(f"- 起始 towerId: {before.get('towerId', 0)}")
            lines.append(f"- 结束 towerId: {after.get('towerId', 0)}")
            lines.append(f"- 起始能量: {before.get('energy', 0)}")
            lines.append(f"- 结束能量: {after.get('energy', 0)}")
            claimed_task_ids = result.get("claimedTaskIds", []) or []
            lines.append(
                "- 本次领取任务: "
                + (",".join(str(task_id) for task_id in claimed_task_ids) if claimed_task_ids else "无")
            )

        if result.get("action") == "skinchallenge_overview":
            lines.append(f"- 活动ID: {result.get('actId') or '-'}")
            lines.append(f"- 活动状态: {'开放中' if result.get('active') else '未开放'}")
            lines.append(
                "- 今日开放 Boss: "
                + (",".join(str(value) for value in (result.get("todayOpenTowers") or [])) or "无")
            )
            lines.append(
                "- 今日未通关 Boss: "
                + (",".join(str(value) for value in (result.get("pendingTowers") or [])) or "无")
            )

        if result.get("action") == "skinchallenge_tower":
            lines.append(f"- 目标 Boss: {result.get('targetTowerType') or '-'}")
            lines.append(f"- 起始层数: {result.get('beforeLevel', 0)}")
            lines.append(f"- 结束层数: {result.get('afterLevel', 0)}")
            lines.append(f"- 战斗次数: {result.get('executedFightCount', 0)}")
            lines.append(f"- 成功次数: {result.get('successFightCount', 0)}")
            lines.append(f"- 连败次数: {result.get('failedFightCount', 0)}")

        if result.get("action") == "skinchallenge_today":
            lines.append(
                "- 今日开放 Boss: "
                + (",".join(str(value) for value in (result.get("todayOpenTowers") or [])) or "无")
            )
            lines.append(f"- 已完成 Boss: {result.get('completedTowerCount', 0)}")
            lines.append(f"- 未完成 Boss: {result.get('partialTowerCount', 0)}")
            lines.append(f"- 跳过 Boss: {result.get('skippedTowerCount', 0)}")
            lines.append(f"- 总战斗次数: {result.get('totalExecutedFightCount', 0)}")
            boss_results = result.get("bossResults", []) or []
            if boss_results:
                lines.append("")
                lines.append("补打结果:")
                for index, item in enumerate(boss_results[:6], start=1):
                    status = "完成" if item.get("towerCleared") else "未完成"
                    if item.get("skipped"):
                        status = "跳过"
                    lines.append(
                        f"{index}. Boss {item.get('towerType')} - {status}"
                        f" (战斗 {item.get('executedFightCount', 0)} 次)"
                    )
                if len(boss_results) > 6:
                    lines.append(f"... 其余 {len(boss_results) - 6} 个 Boss 未展开")

        lines.append("")
        lines.append(result.get("message") or "执行完成。")
        return "\n".join(lines)

    def _help_text(self) -> str:
        return (
            "XYZW 助手\n\n"
            "当前可用命令:\n"
            "/xyzw help\n"
            "/xyzw 健康\n"
            "/xyzw 计划\n"
            "/xyzw 架构\n"
            "/xyzw 绑定 [token|url|bin]\n"
            "/xyzw 账号\n"
            "/xyzw 状态 [别名或ID前缀]\n"
            "/xyzw 车 [查看] [别名或ID前缀]\n"
            "/xyzw 车 护卫成员 [成员ID或名称关键字] [别名或ID前缀]\n"
            "/xyzw 车 发车 <车辆ID> [护卫 <护卫ID>] [别名或ID前缀]\n"
            "/xyzw 车 收车 [别名或ID前缀]\n"
            "/xyzw 日常 [别名或ID前缀]\n"
            "/xyzw 副本 ...\n"
            "/xyzw 资源 ...\n"
            "/xyzw 定时 ...\n"
            "/xyzw 通知 绑定本群\n"
            "/xyzw 通知 查看\n"
            "/xyzw 通知 解绑\n"
            "/xyzw 通知 测试\n\n"
            "当前已实现: sidecar 健康检查、会话式 token 绑定、URL 绑定、BIN 导入绑定、多账号管理、默认账号切换、状态查询、车辆概览/护卫成员状态查询/发车/收车、简版日常、基础副本命令、基础资源命令、通知群绑定、收车提醒基础版、挂机提醒基础版、护卫成员提醒基础版、定时日常基础版、定时资源/副本执行基础版、活动开放提醒基础版。\n"
            "当前未实现: 批量任务、复杂副本编排。"
        )

    def _format_notify_state(self, user_id: str) -> str:
        state = self.storage.get_user_state(user_id)
        notify = state.get("notify", {})
        group = notify.get("group")
        mode = str(notify.get("mode", "group_broadcast") or "").strip()
        mode_text = {
            "group_broadcast": "群广播",
            "private_only": "仅私聊",
            "group_mention_first": "群广播",
        }.get(mode, mode or "群广播")
        lines = [
            "通知配置",
            f"- 用户ID: {user_id}",
            f"- 模式: {mode_text}",
        ]
        if group:
            lines.extend(
                [
                    f"- 通知群: {group['group_id']}",
                    f"- 群会话: {group.get('unified_msg_origin') or '-'}",
                    f"- 绑定时间: {group['bound_at']}",
                ]
            )
        else:
            lines.append("- 通知群: 未绑定")
        return "\n".join(lines)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _parse_iso_datetime(self, value: str | None) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _format_datetime_text(self, value: str | None) -> str:
        parsed = self._parse_iso_datetime(value)
        if not parsed:
            return "未记录"
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def _local_date_key(self, value: datetime | None = None) -> str:
        current = value or datetime.now().astimezone()
        return current.astimezone().strftime("%Y-%m-%d")

    def _parse_schedule_time(self, value: str | None) -> tuple[int, int] | None:
        raw = str(value or "").strip()
        if not raw or ":" not in raw:
            return None
        parts = raw.split(":", 1)
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            return None
        hour = int(parts[0])
        minute = int(parts[1])
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        return hour, minute

    def _car_reminder_status_text(self, status: str | None) -> str:
        return {
            "idle": "无可收取车辆",
            "ready": "已有可收取车辆",
            "blocked": "通知群未绑定",
            "error": "检查失败",
        }.get(str(status or "").strip().lower(), "未检查")

    def _hangup_reminder_status_text(self, status: str | None) -> str:
        return {
            "idle": "挂机中",
            "ready": "挂机已满",
            "blocked": "通知群未绑定",
            "error": "检查失败",
        }.get(str(status or "").strip().lower(), "未检查")

    def _helper_member_reminder_status_text(self, status: str | None) -> str:
        return {
            "idle": "未检查",
            "all_full": "监控成员已满护卫",
            "incomplete": "存在未满护卫成员",
            "window_closed": "当前非发车时段",
            "blocked": "通知群未绑定",
            "error": "检查失败",
        }.get(str(status or "").strip().lower(), "未检查")

    def _daily_task_status_text(self, status: str | None) -> str:
        return {
            "idle": "未执行",
            "success": "执行成功",
            "partial_failed": "部分失败",
            "failed": "执行失败",
            "blocked": "通知群未绑定",
            "running": "执行中",
            "empty": "无可执行项",
        }.get(str(status or "").strip().lower(), "未执行")

    def _activity_label(self, activity_key: str | None) -> str:
        return {
            "dream": "梦境",
            "bosstower": "宝库",
        }.get(_normalize_activity_key(activity_key or ""), str(activity_key or "-"))

    def _activity_reminder_status_text(self, status: str | None) -> str:
        return {
            "idle": "未提醒",
            "sent": "已提醒",
            "blocked": "通知群未绑定",
            "error": "提醒失败",
        }.get(str(status or "").strip().lower(), "未提醒")

    def _scheduled_action_category_label(self, category: str | None) -> str:
        return {
            "resource": "资源",
            "dungeon": "副本",
        }.get(str(category or "").strip().lower(), str(category or "-"))

    def _scheduled_action_status_text(self, status: str | None) -> str:
        return {
            "idle": "未执行",
            "success": "执行成功",
            "failed": "执行失败",
            "blocked": "通知群未绑定",
            "running": "执行中",
        }.get(str(status or "").strip().lower(), "未执行")

    def _resource_action_label(
        self,
        action: str,
        options: dict[str, Any] | None = None,
    ) -> str:
        normalized_options = options or {}
        mapping = {
            "recruit_free": "免费招募",
            "recruit_paid": "付费招募",
            "fish_free": "免费钓鱼",
            "claim_collection_free": "珍宝阁免费",
            "blackmarket_purchase": "黑市采购",
            "legion_holy_shards": "军团四圣碎片",
            "claim_discount_daily": "每日礼包",
        }
        if action == "open_wood_box":
            count = int(normalized_options.get("count") or 1)
            return f"开木箱 x{count}"
        return mapping.get(action, action)

    def _dungeon_action_label(
        self,
        action: str,
        options: dict[str, Any] | None = None,
    ) -> str:
        normalized_options = options or {}
        mapping = {
            "bosstower_low": "宝库前3",
            "bosstower_high": "宝库后2",
            "dream_purchase": "梦境金币商品购买",
            "weirdtower_overview": "怪异塔状态",
            "weirdtower_claim_free_energy": "怪异塔免费道具",
            "skinchallenge_overview": "换皮闯关状态",
            "skinchallenge_today": "换皮补打",
        }
        if action == "dream_team":
            return f"梦境阵容切换 #{int(normalized_options.get('team_id') or 107)}"
        if action == "weirdtower_use_items":
            return f"怪异塔用道具 x{int(normalized_options.get('max_uses') or 1)}"
        if action == "weirdtower_climb":
            max_fights = int(normalized_options.get("max_fights") or 5)
            team_id = int(normalized_options.get("team_id") or 0)
            if team_id > 0:
                return f"怪异塔爬塔 x{max_fights} (阵容 {team_id})"
            return f"怪异塔爬塔 x{max_fights}"
        if action == "skinchallenge_tower":
            return f"换皮挑战 Boss {int(normalized_options.get('tower_type') or 0)}"
        return mapping.get(action, action)

    def _parse_resource_command_spec(
        self,
        command_tokens: list[str],
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not command_tokens:
            return None, self._resource_usage()

        action_token = str(command_tokens[0] or "").lower()
        selector = ""
        options: dict[str, Any] = {}
        resource_action = ""

        if action_token in {"招募", "recruit"}:
            mode = "免费"
            if len(command_tokens) >= 2:
                candidate = str(command_tokens[1] or "").lower()
                if candidate in {"免费", "free"}:
                    selector = " ".join(command_tokens[2:]).strip()
                elif candidate in {"付费", "paid"}:
                    mode = "付费"
                    selector = " ".join(command_tokens[2:]).strip()
                else:
                    selector = " ".join(command_tokens[1:]).strip()
            resource_action = "recruit_paid" if mode == "付费" else "recruit_free"
        elif action_token in {"钓鱼", "fish"}:
            if len(command_tokens) >= 2 and str(command_tokens[1] or "").lower() in {
                "免费",
                "free",
            }:
                selector = " ".join(command_tokens[2:]).strip()
            else:
                selector = " ".join(command_tokens[1:]).strip()
            resource_action = "fish_free"
        elif action_token in {"开箱", "box"}:
            remaining_tokens = list(command_tokens[1:])
            if remaining_tokens and str(remaining_tokens[0] or "").lower() in {
                "木箱",
                "木质",
                "wood",
            }:
                remaining_tokens = remaining_tokens[1:]
            if remaining_tokens and str(remaining_tokens[0] or "").isdigit():
                options["count"] = int(remaining_tokens[0])
                remaining_tokens = remaining_tokens[1:]
            selector = " ".join(remaining_tokens).strip()
            resource_action = "open_wood_box"
        elif action_token in {"珍宝阁", "珍宝", "collection"}:
            selector = " ".join(command_tokens[1:]).strip()
            resource_action = "claim_collection_free"
        elif action_token in {"黑市", "商店", "store"}:
            selector = " ".join(command_tokens[1:]).strip()
            resource_action = "blackmarket_purchase"
        elif action_token in {"军团", "legion"}:
            remaining_tokens = list(command_tokens[1:])
            if remaining_tokens and str(remaining_tokens[0] or "").lower() in {
                "四圣碎片",
                "四圣",
                "碎片",
                "holy",
                "shards",
            }:
                selector = " ".join(remaining_tokens[1:]).strip()
                resource_action = "legion_holy_shards"
            else:
                return None, self._resource_usage()
        elif action_token in {"每日礼包", "礼包", "discount"}:
            selector = " ".join(command_tokens[1:]).strip()
            resource_action = "claim_discount_daily"
        else:
            return None, self._resource_usage()

        return {
            "category": "resource",
            "action": resource_action,
            "options": options or None,
            "selector": selector,
            "label": self._resource_action_label(resource_action, options),
            "timeout_ms": None,
        }, None

    def _resolve_dungeon_timeout_ms(self, action: str) -> int | None:
        if action in {"weirdtower_climb", "skinchallenge_tower"}:
            return 30000
        if action in {"weirdtower_use_items", "dream_purchase", "skinchallenge_today"}:
            return 90000
        return None

    def _parse_dungeon_command_spec(
        self,
        command_tokens: list[str],
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not command_tokens:
            return None, self._dungeon_usage()

        action_token = str(command_tokens[0] or "").lower()
        selector = ""
        options: dict[str, Any] = {}
        dungeon_action = ""

        if action_token in {"宝库", "bosstower"}:
            if len(command_tokens) < 2:
                return None, self._dungeon_usage()

            tier_token = str(command_tokens[1] or "").lower()
            if tier_token in {"前3", "13", "1-3", "low"}:
                dungeon_action = "bosstower_low"
            elif tier_token in {"后2", "45", "4-5", "high"}:
                dungeon_action = "bosstower_high"
            else:
                return None, self._dungeon_usage()
            selector = " ".join(command_tokens[2:]).strip()
        elif action_token in {"梦境", "dream"}:
            remaining_tokens = list(command_tokens[1:])
            if remaining_tokens and str(remaining_tokens[0] or "").lower() in {
                "购买",
                "商店",
                "buy",
                "shop",
            }:
                dungeon_action = "dream_purchase"
                remaining_tokens = remaining_tokens[1:]
                if remaining_tokens and str(remaining_tokens[0] or "").lower() in {
                    "金币",
                    "金币商品",
                    "gold",
                    "golditems",
                    "gold_items",
                }:
                    options["preset"] = "gold_items"
                    remaining_tokens = remaining_tokens[1:]
                selector = " ".join(remaining_tokens).strip()
            else:
                if remaining_tokens and str(remaining_tokens[0] or "").lower() in {
                    "阵容",
                    "team",
                }:
                    remaining_tokens = remaining_tokens[1:]
                if remaining_tokens and str(remaining_tokens[0] or "").isdigit():
                    options["team_id"] = int(remaining_tokens[0])
                    remaining_tokens = remaining_tokens[1:]
                selector = " ".join(remaining_tokens).strip()
                dungeon_action = "dream_team"
        elif action_token in {"怪异塔", "weirdtower"}:
            remaining_tokens = list(command_tokens[1:])
            if not remaining_tokens:
                dungeon_action = "weirdtower_overview"
            else:
                candidate = str(remaining_tokens[0] or "").lower()
                if candidate in {"查看", "状态", "概览", "info"}:
                    dungeon_action = "weirdtower_overview"
                    remaining_tokens = remaining_tokens[1:]
                elif candidate in {"免费道具", "免费", "领取免费道具", "free"}:
                    dungeon_action = "weirdtower_claim_free_energy"
                    remaining_tokens = remaining_tokens[1:]
                elif candidate in {"用道具", "使用道具", "道具", "use", "items"}:
                    dungeon_action = "weirdtower_use_items"
                    remaining_tokens = remaining_tokens[1:]
                    selector_tokens: list[str] = []
                    index = 0
                    while index < len(remaining_tokens):
                        current = str(remaining_tokens[index] or "").lower()
                        next_token = (
                            remaining_tokens[index + 1]
                            if index + 1 < len(remaining_tokens)
                            else ""
                        )
                        if current in {"数量", "count"} and str(next_token).isdigit():
                            options["max_uses"] = int(next_token)
                            index += 2
                            continue
                        if current.isdigit() and "max_uses" not in options:
                            options["max_uses"] = int(current)
                            index += 1
                            continue
                        selector_tokens.append(remaining_tokens[index])
                        index += 1
                    remaining_tokens = selector_tokens
                elif candidate in {"爬塔", "climb"}:
                    dungeon_action = "weirdtower_climb"
                    remaining_tokens = remaining_tokens[1:]
                    selector_tokens: list[str] = []
                    index = 0
                    while index < len(remaining_tokens):
                        current = str(remaining_tokens[index] or "").lower()
                        next_token = (
                            remaining_tokens[index + 1]
                            if index + 1 < len(remaining_tokens)
                            else ""
                        )
                        if current in {"次数", "count"} and str(next_token).isdigit():
                            options["max_fights"] = int(next_token)
                            index += 2
                            continue
                        if current in {"阵容", "team"} and str(next_token).isdigit():
                            options["team_id"] = int(next_token)
                            index += 2
                            continue
                        selector_tokens.append(remaining_tokens[index])
                        index += 1
                    remaining_tokens = selector_tokens
                else:
                    dungeon_action = "weirdtower_overview"
            selector = " ".join(remaining_tokens).strip()
        elif action_token in {"换皮", "换皮闯关", "skin"}:
            remaining_tokens = list(command_tokens[1:])
            if not remaining_tokens:
                dungeon_action = "skinchallenge_overview"
            else:
                candidate = str(remaining_tokens[0] or "").lower()
                if candidate in {"查看", "状态", "概览", "info"}:
                    dungeon_action = "skinchallenge_overview"
                    remaining_tokens = remaining_tokens[1:]
                elif candidate in {"补打", "全部", "today", "all"}:
                    dungeon_action = "skinchallenge_today"
                    remaining_tokens = remaining_tokens[1:]
                elif candidate in {"挑战", "打", "run"}:
                    dungeon_action = "skinchallenge_tower"
                    remaining_tokens = remaining_tokens[1:]
                    selector_tokens: list[str] = []
                    boss_selected = False
                    for item in remaining_tokens:
                        if not boss_selected and str(item or "").isdigit():
                            options["tower_type"] = int(item)
                            boss_selected = True
                            continue
                        selector_tokens.append(item)
                    remaining_tokens = selector_tokens
                else:
                    dungeon_action = "skinchallenge_overview"
            selector = " ".join(remaining_tokens).strip()
        else:
            return None, self._dungeon_usage()

        return {
            "category": "dungeon",
            "action": dungeon_action,
            "options": options or None,
            "selector": selector,
            "label": self._dungeon_action_label(dungeon_action, options),
            "timeout_ms": self._resolve_dungeon_timeout_ms(dungeon_action),
        }, None

    async def _run_resource_action_request(
        self,
        user_id: str,
        account: dict[str, Any],
        action: str,
        options: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        response = await self._call_with_account_token_ready(
            user_id,
            account,
            lambda ready_account: self.sidecar.run_resource_action(
                ready_account.get("token", ""),
                action,
                options=options,
                timeout_ms=timeout_ms,
            ),
            reason=f"resource:{action}",
        )
        if not response.get("ok"):
            return {
                "ok": False,
                "error_message": response.get("message", "未知错误"),
                "data": {},
            }

        data = response.get("data", {}) or {}
        if not data.get("success"):
            return {
                "ok": False,
                "error_message": data.get("error")
                or data.get("message")
                or data.get("code")
                or "未知错误",
                "data": data,
            }

        return {
            "ok": True,
            "error_message": "",
            "data": data,
        }

    async def _run_dungeon_action_request(
        self,
        user_id: str,
        account: dict[str, Any],
        action: str,
        options: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        response = await self._call_with_account_token_ready(
            user_id,
            account,
            lambda ready_account: self.sidecar.run_dungeon_action(
                ready_account.get("token", ""),
                action,
                options=options,
                timeout_ms=timeout_ms,
            ),
            reason=f"dungeon:{action}",
        )
        if not response.get("ok"):
            return {
                "ok": False,
                "error_message": response.get("message", "未知错误"),
                "data": {},
            }

        data = response.get("data", {}) or {}
        if not data.get("success"):
            return {
                "ok": False,
                "error_message": data.get("message")
                or data.get("error")
                or data.get("code")
                or "未知错误",
                "data": data,
            }

        return {
            "ok": True,
            "error_message": "",
            "data": data,
        }

    def _schedule_usage(self) -> str:
        return (
            "定时命令\n\n"
            "/xyzw 定时 查看\n"
            "/xyzw 定时 收车 开启 [间隔分钟] [别名或ID前缀]\n"
            "/xyzw 定时 收车 关闭 [别名或ID前缀]\n"
            "/xyzw 定时 收车 检查 [别名或ID前缀]\n\n"
            "/xyzw 定时 挂机 开启 [间隔分钟] [别名或ID前缀]\n"
            "/xyzw 定时 挂机 关闭 [别名或ID前缀]\n"
            "/xyzw 定时 挂机 检查 [别名或ID前缀]\n\n"
            "/xyzw 定时 护卫成员 查看\n"
            "/xyzw 定时 护卫成员 开启 [间隔分钟] <成员ID1,成员ID2,...> [别名或ID前缀]\n"
            "/xyzw 定时 护卫成员 关闭 [别名或ID前缀]\n"
            "/xyzw 定时 护卫成员 检查 [别名或ID前缀]\n\n"
            "/xyzw 定时 日常 开启 <HH:MM> [别名或ID前缀]\n"
            "/xyzw 定时 日常 关闭 [别名或ID前缀]\n"
            "/xyzw 定时 日常 执行 [别名或ID前缀]\n\n"
            "/xyzw 定时 资源 查看\n"
            "/xyzw 定时 资源 开启 <HH:MM> <资源命令参数...>\n"
            "/xyzw 定时 资源 关闭 <资源命令参数...>\n"
            "/xyzw 定时 资源 执行 <资源命令参数...>\n\n"
            "/xyzw 定时 副本 查看\n"
            "/xyzw 定时 副本 开启 <HH:MM> <副本命令参数...>\n"
            "/xyzw 定时 副本 关闭 <副本命令参数...>\n"
            "/xyzw 定时 副本 执行 <副本命令参数...>\n\n"
            "/xyzw 定时 活动 查看\n"
            "/xyzw 定时 活动 开启 <梦境|宝库> [HH:MM]\n"
            "/xyzw 定时 活动 关闭 <梦境|宝库>\n\n"
            f"默认收车提醒间隔: {self.car_reminder_default_interval_minutes} 分钟\n"
            f"默认挂机提醒间隔: {self.hangup_reminder_default_interval_minutes} 分钟\n"
            f"默认护卫成员提醒间隔: {self.helper_member_reminder_default_interval_minutes} 分钟\n"
            "默认活动提醒时间: 00:00\n"
            "通知渠道固定为群广播，请先使用 /xyzw 通知 绑定本群。"
        )

    def _format_duration_text(self, seconds: int | float | None) -> str:
        try:
            total = max(0, int(seconds or 0))
        except (TypeError, ValueError):
            total = 0
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}小时{minutes}分{secs}秒"
        if minutes > 0:
            return f"{minutes}分{secs}秒"
        return f"{secs}秒"

    def _format_helper_member_ids_text(self, member_ids: Any) -> str:
        if not isinstance(member_ids, list):
            return "-"
        normalized_ids = [str(item or "").strip() for item in member_ids if str(item or "").strip()]
        return ",".join(normalized_ids) if normalized_ids else "-"

    def _format_helper_member_runtime_text(self, members: Any) -> str:
        if not isinstance(members, list) or not members:
            return "无"

        parts: list[str] = []
        for item in members:
            if not isinstance(item, dict):
                continue
            display_name = str(item.get("displayName") or item.get("roleId") or "-").strip()
            role_id = str(item.get("roleId") or "-").strip() or "-"
            used_count = int(item.get("usedCount") or 0)
            max_count = int(item.get("maxCount") or 4)
            parts.append(f"{display_name}(ID:{role_id} {used_count}/{max_count})")
        return "；".join(parts) if parts else "无"

    def _append_helper_member_reminder_lines(
        self,
        lines: list[str],
        user_id: str,
        reminders: list[dict[str, Any]],
    ) -> None:
        lines.append("护卫成员提醒:")
        for index, reminder in enumerate(reminders, start=1):
            account = self.storage.get_account_by_id(
                user_id,
                str(reminder.get("account_id") or ""),
            )
            alias = account.get("alias") if account else "已删除账号"
            enabled_text = "启用" if reminder.get("enabled") else "关闭"
            lines.append(f"{index}. {alias} [{enabled_text}]")
            lines.append(
                f"   间隔: {int(reminder.get('interval_minutes') or self.helper_member_reminder_default_interval_minutes)} 分钟"
            )
            lines.append(
                f"   成员ID: {self._format_helper_member_ids_text(reminder.get('member_ids'))}"
            )
            lines.append(f"   时段: {self._car_send_window_text()}")
            lines.append(
                f"   状态: {self._helper_member_reminder_status_text(reminder.get('last_status'))}"
            )
            lines.append(
                f"   最近检查: {self._format_datetime_text(reminder.get('last_checked_at'))}"
            )
            lines.append(
                f"   最近通知: {self._format_datetime_text(reminder.get('last_notified_at'))}"
            )
            lines.append(
                f"   最近未满: {self._format_helper_member_runtime_text(reminder.get('last_pending_members'))}"
            )
            if reminder.get("last_error_message"):
                lines.append(f"   最近错误: {reminder.get('last_error_message')}")

    def _append_activity_schedule_lines(
        self,
        lines: list[str],
        reminders: list[dict[str, Any]],
    ) -> None:
        lines.append("活动开放提醒:")
        for index, reminder in enumerate(reminders, start=1):
            activity_key = _normalize_activity_key(str(reminder.get("activity_key") or ""))
            enabled_text = "启用" if reminder.get("enabled") else "关闭"
            lines.append(f"{index}. {self._activity_label(activity_key)} [{enabled_text}]")
            lines.append(f"   时间: {reminder.get('notify_time') or '-'}")
            lines.append(
                f"   今日开放: {'是' if self._is_activity_open(activity_key) else '否'}"
            )
            lines.append(
                f"   状态: {self._activity_reminder_status_text(reminder.get('last_status'))}"
            )
            lines.append(
                f"   最近检查: {self._format_datetime_text(reminder.get('last_checked_at'))}"
            )
            lines.append(
                f"   最近通知: {self._format_datetime_text(reminder.get('last_notified_at'))}"
            )
            if reminder.get("last_error_message"):
                lines.append(f"   最近错误: {reminder.get('last_error_message')}")

    def _append_action_task_schedule_lines(
        self,
        lines: list[str],
        user_id: str,
        schedules: list[dict[str, Any]],
        category: str,
    ) -> None:
        category_label = self._scheduled_action_category_label(category)
        lines.append(f"定时{category_label}执行:")
        for index, schedule in enumerate(schedules, start=1):
            account = self.storage.get_account_by_id(
                user_id,
                str(schedule.get("account_id") or ""),
            )
            alias = account.get("alias") if account else "已删除账号"
            enabled_text = "启用" if schedule.get("enabled") else "关闭"
            lines.append(
                f"{index}. {schedule.get('label') or '-'} [{enabled_text}]"
            )
            lines.append(f"   账号: {alias}")
            lines.append(f"   时间: {schedule.get('schedule_time') or '-'}")
            lines.append(
                f"   状态: {self._scheduled_action_status_text(schedule.get('last_status'))}"
            )
            lines.append(
                f"   最近执行: {self._format_datetime_text(schedule.get('last_run_at'))}"
            )
            lines.append(
                f"   最近通知: {self._format_datetime_text(schedule.get('last_notified_at'))}"
            )
            if schedule.get("last_result_message"):
                lines.append(f"   最近结果: {schedule.get('last_result_message')}")
            if schedule.get("last_error_message"):
                lines.append(f"   最近错误: {schedule.get('last_error_message')}")

    def _format_activity_schedule_state(self, user_id: str) -> str:
        lines = [
            "活动开放提醒配置",
            f"- 用户ID: {user_id}",
            f"- 通知目标: {self.notifier.preview_target(user_id)}",
            f"- 巡检轮询: {self.scheduler_poll_interval_seconds} 秒",
        ]
        reminders = self.storage.list_activity_reminders(user_id)
        if not reminders:
            lines.append("")
            lines.append("当前还没有活动开放提醒配置。")
            lines.append("")
            lines.append(self._schedule_usage())
            return "\n".join(lines)

        lines.append("")
        self._append_activity_schedule_lines(lines, reminders)
        return "\n".join(lines)

    def _format_helper_member_schedule_state(self, user_id: str) -> str:
        lines = [
            "护卫成员提醒配置",
            f"- 用户ID: {user_id}",
            f"- 通知目标: {self.notifier.preview_target(user_id)}",
            f"- 巡检轮询: {self.scheduler_poll_interval_seconds} 秒",
        ]
        reminders = self.storage.list_helper_member_reminders(user_id)
        if not reminders:
            lines.append("")
            lines.append("当前还没有护卫成员提醒配置。")
            lines.append("")
            lines.append(self._schedule_usage())
            return "\n".join(lines)

        lines.append("")
        self._append_helper_member_reminder_lines(lines, user_id, reminders)
        return "\n".join(lines)

    def _format_action_task_schedule_state(
        self,
        user_id: str,
        category: str,
    ) -> str:
        category_label = self._scheduled_action_category_label(category)
        lines = [
            f"定时{category_label}执行配置",
            f"- 用户ID: {user_id}",
            f"- 通知目标: {self.notifier.preview_target(user_id)}",
            f"- 巡检轮询: {self.scheduler_poll_interval_seconds} 秒",
        ]
        schedules = self.storage.list_action_tasks(user_id, category=category)
        if not schedules:
            lines.append("")
            lines.append(f"当前还没有定时{category_label}执行配置。")
            lines.append("")
            lines.append(self._schedule_usage())
            return "\n".join(lines)

        lines.append("")
        self._append_action_task_schedule_lines(lines, user_id, schedules, category)
        return "\n".join(lines)

    def _format_schedule_state(self, user_id: str) -> str:
        lines = [
            "定时任务配置",
            f"- 用户ID: {user_id}",
            f"- 通知目标: {self.notifier.preview_target(user_id)}",
            f"- 巡检轮询: {self.scheduler_poll_interval_seconds} 秒",
        ]

        reminders = self.storage.list_car_reminders(user_id)
        hangup_reminders = self.storage.list_hangup_reminders(user_id)
        helper_member_reminders = self.storage.list_helper_member_reminders(user_id)
        daily_tasks = self.storage.list_daily_tasks(user_id)
        activity_reminders = self.storage.list_activity_reminders(user_id)
        resource_tasks = self.storage.list_action_tasks(user_id, category="resource")
        dungeon_tasks = self.storage.list_action_tasks(user_id, category="dungeon")
        if (
            not reminders
            and not hangup_reminders
            and not helper_member_reminders
            and not daily_tasks
            and not activity_reminders
            and not resource_tasks
            and not dungeon_tasks
        ):
            lines.append("")
            lines.append("当前还没有启用任何定时任务。")
            lines.append("")
            lines.append(self._schedule_usage())
            return "\n".join(lines)

        if reminders:
            lines.append("")
            lines.append("收车提醒:")
            for index, reminder in enumerate(reminders, start=1):
                account = self.storage.get_account_by_id(
                    user_id,
                    str(reminder.get("account_id") or ""),
                )
                alias = account.get("alias") if account else "已删除账号"
                enabled_text = "启用" if reminder.get("enabled") else "关闭"
                lines.append(f"{index}. {alias} [{enabled_text}]")
                lines.append(
                    f"   间隔: {int(reminder.get('interval_minutes') or self.car_reminder_default_interval_minutes)} 分钟"
                )
                lines.append(
                    f"   状态: {self._car_reminder_status_text(reminder.get('last_status'))}"
                )
                lines.append(
                    f"   最近检查: {self._format_datetime_text(reminder.get('last_checked_at'))}"
                )
                lines.append(
                    f"   最近通知: {self._format_datetime_text(reminder.get('last_notified_at'))}"
                )
                if reminder.get("last_error_message"):
                    lines.append(f"   最近错误: {reminder.get('last_error_message')}")

        if hangup_reminders:
            lines.append("")
            lines.append("挂机提醒:")
            for index, reminder in enumerate(hangup_reminders, start=1):
                account = self.storage.get_account_by_id(
                    user_id,
                    str(reminder.get("account_id") or ""),
                )
                alias = account.get("alias") if account else "已删除账号"
                enabled_text = "启用" if reminder.get("enabled") else "关闭"
                lines.append(f"{index}. {alias} [{enabled_text}]")
                lines.append(
                    f"   间隔: {int(reminder.get('interval_minutes') or self.hangup_reminder_default_interval_minutes)} 分钟"
                )
                lines.append(
                    f"   状态: {self._hangup_reminder_status_text(reminder.get('last_status'))}"
                )
                lines.append(
                    "   最近剩余: "
                    f"{self._format_duration_text(reminder.get('last_remaining_seconds'))}"
                )
                lines.append(
                    f"   最近检查: {self._format_datetime_text(reminder.get('last_checked_at'))}"
                )
                lines.append(
                    f"   最近通知: {self._format_datetime_text(reminder.get('last_notified_at'))}"
                )
                if reminder.get("last_error_message"):
                    lines.append(f"   最近错误: {reminder.get('last_error_message')}")

        if helper_member_reminders:
            lines.append("")
            self._append_helper_member_reminder_lines(
                lines,
                user_id,
                helper_member_reminders,
            )

        if daily_tasks:
            lines.append("")
            lines.append("定时日常:")
            for index, schedule in enumerate(daily_tasks, start=1):
                account = self.storage.get_account_by_id(
                    user_id,
                    str(schedule.get("account_id") or ""),
                )
                alias = account.get("alias") if account else "已删除账号"
                enabled_text = "启用" if schedule.get("enabled") else "关闭"
                lines.append(f"{index}. {alias} [{enabled_text}]")
                lines.append(
                    f"   时间: {schedule.get('schedule_time') or '-'}"
                )
                lines.append(
                    f"   状态: {self._daily_task_status_text(schedule.get('last_status'))}"
                )
                lines.append(
                    f"   最近执行: {self._format_datetime_text(schedule.get('last_run_at'))}"
                )
                lines.append(
                    f"   最近通知: {self._format_datetime_text(schedule.get('last_notified_at'))}"
                )
                lines.append(
                    "   最近结果: "
                    f"总{int(schedule.get('last_total_count') or 0)} / "
                    f"成{int(schedule.get('last_success_count') or 0)} / "
                    f"跳{int(schedule.get('last_skipped_count') or 0)} / "
                    f"失{int(schedule.get('last_failed_count') or 0)}"
                )
                if schedule.get("last_error_message"):
                    lines.append(f"   最近错误: {schedule.get('last_error_message')}")

        if resource_tasks:
            lines.append("")
            self._append_action_task_schedule_lines(
                lines,
                user_id,
                resource_tasks,
                "resource",
            )

        if dungeon_tasks:
            lines.append("")
            self._append_action_task_schedule_lines(
                lines,
                user_id,
                dungeon_tasks,
                "dungeon",
            )

        if activity_reminders:
            lines.append("")
            self._append_activity_schedule_lines(lines, activity_reminders)

        return "\n".join(lines)

    def _build_car_ready_signature(self, overview: dict[str, Any]) -> str:
        claimable_ids = sorted(
            str(car.get("id") or "")
            for car in (overview.get("cars") or [])
            if car.get("claimable")
        )
        return ",".join(item for item in claimable_ids if item)

    def _build_car_reminder_message(
        self,
        account: dict[str, Any],
        overview: dict[str, Any],
    ) -> str:
        claimable_cars = [
            car for car in (overview.get("cars") or []) if car.get("claimable")
        ]
        lines = [
            "XYZW 收车提醒",
            f"账号: {account.get('alias')}",
            f"可收取: {len(claimable_cars)}",
        ]
        if claimable_cars:
            lines.append("车辆:")
            for index, car in enumerate(claimable_cars[:6], start=1):
                lines.append(
                    f"{index}. {car.get('gradeLabel', '未知')} id={car.get('id')}"
                )
            if len(claimable_cars) > 6:
                lines.append(f"... 其余 {len(claimable_cars) - 6} 辆未展开")
        lines.append(f"可使用 /xyzw 车 收车 {account.get('alias')} 立即处理。")
        return "\n".join(lines)

    def _format_car_reminder_check_result(
        self,
        account: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        lines = [
            "收车提醒检查",
            f"- 别名: {account.get('alias')}",
            f"- 状态: {result.get('status_text') or '-'}",
            f"- 可收取: {result.get('ready_count', 0)}",
        ]
        if result.get("notified"):
            lines.append("- 通知: 已发送")
        elif result.get("success") and result.get("ready_count", 0) > 0:
            lines.append("- 通知: 本轮未重复发送")

        error_message = result.get("error_message")
        if error_message:
            lines.append(f"- 错误: {error_message}")

        claimable_cars = result.get("claimable_cars", []) or []
        if claimable_cars:
            lines.append("")
            lines.append("可收取车辆:")
            for index, car in enumerate(claimable_cars[:6], start=1):
                lines.append(
                    f"{index}. {car.get('gradeLabel', '未知')} id={car.get('id')}"
                )
            if len(claimable_cars) > 6:
                lines.append(f"... 其余 {len(claimable_cars) - 6} 辆未展开")

        message = result.get("message")
        if message:
            lines.append("")
            lines.append(message)
        return "\n".join(lines)

    def _build_helper_member_reminder_signature(
        self,
        helpers: list[dict[str, Any]],
    ) -> str:
        items = [
            (
                str(helper.get("roleId") or "").strip(),
                int(helper.get("usedCount") or 0),
                int(helper.get("maxCount") or 4),
            )
            for helper in helpers
            if str(helper.get("roleId") or "").strip()
        ]
        items.sort(key=lambda item: item[0])
        return "|".join(f"{role_id}:{used_count}/{max_count}" for role_id, used_count, max_count in items)

    def _build_helper_member_runtime_snapshot(
        self,
        helpers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        snapshot: list[dict[str, Any]] = []
        for helper in helpers:
            role_id = str(helper.get("roleId") or "").strip()
            if not role_id:
                continue
            snapshot.append(
                {
                    "roleId": role_id,
                    "displayName": str(
                        helper.get("displayName")
                        or helper.get("name")
                        or helper.get("nickname")
                        or role_id
                    ).strip(),
                    "usedCount": int(helper.get("usedCount") or 0),
                    "maxCount": int(helper.get("maxCount") or 4),
                    "availableCount": int(helper.get("availableCount") or 0),
                }
            )
        return snapshot

    def _build_helper_member_reminder_message(
        self,
        account: dict[str, Any],
        reminder: dict[str, Any],
        pending_helpers: list[dict[str, Any]],
        matched_count: int,
    ) -> str:
        lines = [
            "XYZW 护卫成员提醒",
            f"账号: {account.get('alias')}",
            f"监控成员ID: {self._format_helper_member_ids_text(reminder.get('member_ids'))}",
            f"匹配成员: {matched_count}",
            f"未满成员: {len(pending_helpers)}",
            f"时段: 当前为疯狂赛车发车时段（{self._car_send_window_text()}）",
        ]
        if pending_helpers:
            lines.append("待补护卫成员:")
            for index, helper in enumerate(pending_helpers, start=1):
                lines.append(
                    f"{index}. {helper.get('displayName') or helper.get('roleId')}"
                    f" (ID: {helper.get('roleId') or '-'})"
                    f" - 已护卫 {int(helper.get('usedCount') or 0)}/{int(helper.get('maxCount') or 4)}"
                    f" - 剩余 {int(helper.get('availableCount') or 0)}"
                )
        lines.append(
            f"可使用 /xyzw 车 护卫成员 {self._format_helper_member_ids_text(reminder.get('member_ids'))} {account.get('alias')} 查看详情。"
        )
        return "\n".join(lines)

    def _format_helper_member_reminder_check_result(
        self,
        account: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        lines = [
            "护卫成员提醒检查",
            f"- 别名: {account.get('alias')}",
            f"- 状态: {result.get('status_text') or '-'}",
            f"- 监控成员ID: {self._format_helper_member_ids_text(result.get('member_ids'))}",
            f"- 匹配成员: {int(result.get('matched_count') or 0)}",
            f"- 未满成员: {int(result.get('pending_count') or 0)}",
        ]
        if result.get("notified"):
            lines.append("- 通知: 已发送")
        elif result.get("success") and int(result.get("pending_count") or 0) > 0:
            lines.append("- 通知: 本轮未重复发送")

        error_message = str(result.get("error_message") or "").strip()
        if error_message:
            lines.append(f"- 错误: {error_message}")

        pending_helpers = result.get("pending_helpers", []) or []
        if pending_helpers:
            lines.append("")
            lines.append("待补护卫成员:")
            for index, helper in enumerate(pending_helpers, start=1):
                lines.append(
                    f"{index}. {helper.get('displayName') or helper.get('roleId')}"
                    f" (ID: {helper.get('roleId') or '-'})"
                    f" - 已护卫 {int(helper.get('usedCount') or 0)}/{int(helper.get('maxCount') or 4)}"
                    f" - 剩余 {int(helper.get('availableCount') or 0)}"
                )

        message = str(result.get("message") or "").strip()
        if message:
            lines.append("")
            lines.append(message)
        return "\n".join(lines)

    def _extract_hangup_state(self, describe_data: dict[str, Any]) -> dict[str, Any]:
        role_info = describe_data.get("roleInfo", {}) or {}
        role = role_info.get("role", {}) or {}
        hangup = role.get("hangUp") or role.get("hangup") or {}
        summary = describe_data.get("summary", {}) or {}
        now_ts = datetime.now(timezone.utc).timestamp()

        try:
            last_time = float(hangup.get("lastTime") or 0)
        except (TypeError, ValueError):
            last_time = 0.0
        try:
            hangup_time = float(
                hangup.get("hangUpTime")
                or hangup.get("hangupTime")
                or 0
            )
        except (TypeError, ValueError):
            hangup_time = 0.0

        if last_time > 0 and hangup_time > 0:
            elapsed_seconds = max(0, int(now_ts - last_time))
            remaining_seconds = max(0, int(hangup_time - elapsed_seconds))
            if elapsed_seconds > int(hangup_time):
                elapsed_seconds = int(hangup_time)
            return {
                "available": True,
                "ready": remaining_seconds <= 0,
                "remaining_seconds": remaining_seconds,
                "elapsed_seconds": elapsed_seconds,
                "hangup_time_seconds": int(hangup_time),
                "hangup_minutes": summary.get("hangUpMinutes"),
            }

        return {
            "available": False,
            "ready": False,
            "remaining_seconds": 0,
            "elapsed_seconds": 0,
            "hangup_time_seconds": 0,
            "hangup_minutes": summary.get("hangUpMinutes"),
        }

    def _build_hangup_reminder_message(
        self,
        account: dict[str, Any],
        hangup_state: dict[str, Any],
    ) -> str:
        lines = [
            "XYZW 挂机提醒",
            f"账号: {account.get('alias')}",
            f"状态: {'挂机已满' if hangup_state.get('ready') else '挂机中'}",
            f"累计挂机: {self._format_duration_text(hangup_state.get('elapsed_seconds'))}",
        ]
        if hangup_state.get("hangup_time_seconds"):
            lines.append(
                f"挂机上限: {self._format_duration_text(hangup_state.get('hangup_time_seconds'))}"
            )
        lines.append(f"可使用 /xyzw 日常 {account.get('alias')} 立即处理。")
        return "\n".join(lines)

    def _format_hangup_reminder_check_result(
        self,
        account: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        lines = [
            "挂机提醒检查",
            f"- 别名: {account.get('alias')}",
            f"- 状态: {result.get('status_text') or '-'}",
            f"- 剩余: {self._format_duration_text(result.get('remaining_seconds'))}",
            f"- 已挂机: {self._format_duration_text(result.get('elapsed_seconds'))}",
        ]
        if result.get("notified"):
            lines.append("- 通知: 已发送")
        elif result.get("success") and result.get("ready"):
            lines.append("- 通知: 本轮未重复发送")

        error_message = result.get("error_message")
        if error_message:
            lines.append(f"- 错误: {error_message}")

        message = result.get("message")
        if message:
            lines.append("")
            lines.append(message)
        return "\n".join(lines)

    def _build_daily_task_notification_message(
        self,
        account: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        lines = [
            "XYZW 定时日常结果",
            f"账号: {account.get('alias')}",
            f"总步骤: {result.get('total_count', 0)}",
            f"成功: {result.get('success_count', 0)}",
            f"跳过: {result.get('skipped_count', 0)}",
            f"失败: {result.get('failed_count', 0)}",
        ]

        failed_steps = result.get("failed_steps", []) or []
        if failed_steps:
            lines.append("失败步骤:")
            for index, step in enumerate(failed_steps, start=1):
                lines.append(
                    f"{index}. {step.get('description') or '-'} - "
                    f"{step.get('message') or step.get('code') or '未知错误'}"
                )

        summary_message = result.get("message")
        if summary_message:
            lines.append(summary_message)
        return "\n".join(lines)

    def _format_daily_task_run_result(
        self,
        account: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        lines = [
            "定时日常执行",
            f"- 别名: {account.get('alias')}",
            f"- 状态: {result.get('status_text') or '-'}",
            f"- 总步骤: {result.get('total_count', 0)}",
            f"- 成功: {result.get('success_count', 0)}",
            f"- 跳过: {result.get('skipped_count', 0)}",
            f"- 失败: {result.get('failed_count', 0)}",
        ]
        if result.get("notified"):
            lines.append("- 通知: 已发送")

        error_message = result.get("error_message")
        if error_message:
            lines.append(f"- 错误: {error_message}")

        failed_steps = result.get("failed_steps", []) or []
        if failed_steps:
            lines.append("")
            lines.append("失败步骤:")
            for index, step in enumerate(failed_steps, start=1):
                lines.append(
                    f"{index}. {step.get('description') or '-'} - "
                    f"{step.get('message') or step.get('code') or '未知错误'}"
                )

        message = result.get("message")
        if message:
            lines.append("")
            lines.append(message)
        return "\n".join(lines)

    def _build_action_task_notification_message(
        self,
        account: dict[str, Any],
        schedule: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        category_label = self._scheduled_action_category_label(
            schedule.get("category")
        )
        lines = [
            f"XYZW 定时{category_label}结果",
            f"账号: {account.get('alias')}",
            f"任务: {schedule.get('label') or '-'}",
            f"时间: {schedule.get('schedule_time') or '-'}",
            f"状态: {result.get('status_text') or '-'}",
        ]
        error_message = str(result.get("error_message") or "").strip()
        result_text = str(result.get("result_text") or "").strip()
        if error_message:
            lines.append(f"错误: {error_message}")
        if result_text:
            lines.append("")
            lines.append(result_text)
        elif result.get("message"):
            lines.append(result.get("message"))
        return "\n".join(lines)

    def _format_action_task_run_result(
        self,
        account: dict[str, Any],
        schedule: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        category_label = self._scheduled_action_category_label(
            schedule.get("category")
        )
        lines = [
            f"定时{category_label}执行",
            f"- 别名: {account.get('alias')}",
            f"- 任务: {schedule.get('label') or '-'}",
            f"- 时间: {schedule.get('schedule_time') or '-'}",
            f"- 状态: {result.get('status_text') or '-'}",
        ]
        if result.get("notified"):
            lines.append("- 通知: 已发送")

        error_message = str(result.get("error_message") or "").strip()
        if error_message:
            lines.append(f"- 错误: {error_message}")

        message = str(result.get("message") or "").strip()
        if message:
            lines.append("")
            lines.append(message)
        return "\n".join(lines)

    def _build_activity_reminder_message(self, activity_key: str) -> str:
        label = self._activity_label(activity_key)
        lines = [
            "XYZW 活动开放提醒",
            f"活动: {label}",
            f"日期: {self._local_date_key()}",
            f"时间: {datetime.now().astimezone().strftime('%H:%M')}",
            f"{label}当前已开放。",
        ]
        if activity_key == "dream":
            lines.append("可使用 /xyzw 副本 梦境 [阵容编号] [别名或ID前缀] 执行。")
        elif activity_key == "bosstower":
            lines.append("可使用 /xyzw 副本 宝库 前3 或 /xyzw 副本 宝库 后2 执行。")
        return "\n".join(lines)

    def _is_car_reminder_due(self, reminder: dict[str, Any]) -> bool:
        last_checked_at = self._parse_iso_datetime(reminder.get("last_checked_at"))
        if not last_checked_at:
            return True
        try:
            interval_minutes = int(
                reminder.get("interval_minutes")
                or self.car_reminder_default_interval_minutes
            )
        except (TypeError, ValueError):
            interval_minutes = self.car_reminder_default_interval_minutes
        interval_minutes = max(5, min(interval_minutes, 720))
        elapsed_seconds = (
            datetime.now(timezone.utc) - last_checked_at.astimezone(timezone.utc)
        ).total_seconds()
        return elapsed_seconds >= interval_minutes * 60

    def _is_hangup_reminder_due(self, reminder: dict[str, Any]) -> bool:
        last_checked_at = self._parse_iso_datetime(reminder.get("last_checked_at"))
        if not last_checked_at:
            return True
        try:
            interval_minutes = int(
                reminder.get("interval_minutes")
                or self.hangup_reminder_default_interval_minutes
            )
        except (TypeError, ValueError):
            interval_minutes = self.hangup_reminder_default_interval_minutes
        interval_minutes = max(5, min(interval_minutes, 720))
        elapsed_seconds = (
            datetime.now(timezone.utc) - last_checked_at.astimezone(timezone.utc)
        ).total_seconds()
        return elapsed_seconds >= interval_minutes * 60

    def _is_helper_member_reminder_due(self, reminder: dict[str, Any]) -> bool:
        last_checked_at = self._parse_iso_datetime(reminder.get("last_checked_at"))
        if not last_checked_at:
            return True
        try:
            interval_minutes = int(
                reminder.get("interval_minutes")
                or self.helper_member_reminder_default_interval_minutes
            )
        except (TypeError, ValueError):
            interval_minutes = self.helper_member_reminder_default_interval_minutes
        interval_minutes = max(5, min(interval_minutes, 720))
        elapsed_seconds = (
            datetime.now(timezone.utc) - last_checked_at.astimezone(timezone.utc)
        ).total_seconds()
        return elapsed_seconds >= interval_minutes * 60

    def _is_time_based_schedule_due(self, schedule: dict[str, Any]) -> bool:
        parsed = self._parse_schedule_time(schedule.get("schedule_time"))
        if not parsed:
            return False

        now_local = datetime.now().astimezone()
        hour, minute = parsed
        if (now_local.hour, now_local.minute) < (hour, minute):
            return False

        return str(schedule.get("last_run_date") or "") != self._local_date_key(now_local)

    def _is_daily_task_due(self, schedule: dict[str, Any]) -> bool:
        return self._is_time_based_schedule_due(schedule)

    def _is_action_task_due(self, schedule: dict[str, Any]) -> bool:
        return self._is_time_based_schedule_due(schedule)

    def _is_dream_open_now(self, value: datetime | None = None) -> bool:
        current = value or datetime.now().astimezone()
        weekday = current.astimezone().weekday()
        return weekday in {0, 2, 3, 6}

    def _is_bosstower_open_now(self, value: datetime | None = None) -> bool:
        current = value or datetime.now().astimezone()
        weekday = current.astimezone().weekday()
        return weekday not in {0, 1}

    def _is_activity_open(
        self,
        activity_key: str,
        value: datetime | None = None,
    ) -> bool:
        normalized_key = _normalize_activity_key(activity_key)
        if normalized_key == "dream":
            return self._is_dream_open_now(value)
        if normalized_key == "bosstower":
            return self._is_bosstower_open_now(value)
        return False

    def _is_activity_reminder_due(self, reminder: dict[str, Any]) -> bool:
        activity_key = _normalize_activity_key(str(reminder.get("activity_key") or ""))
        if activity_key not in {"dream", "bosstower"}:
            return False

        parsed = self._parse_schedule_time(reminder.get("notify_time"))
        if not parsed:
            return False

        now_local = datetime.now().astimezone()
        if not self._is_activity_open(activity_key, now_local):
            return False

        hour, minute = parsed
        if (now_local.hour, now_local.minute) < (hour, minute):
            return False

        return str(reminder.get("last_notified_date") or "") != self._local_date_key(now_local)

    async def _scheduler_loop(self) -> None:
        await asyncio.sleep(3)
        while not self._scheduler_stop_event.is_set():
            try:
                await self._run_car_reminder_jobs()
                await self._run_hangup_reminder_jobs()
                await self._run_helper_member_reminder_jobs()
                await self._run_activity_reminder_jobs()
                await self._run_daily_task_jobs()
                await self._run_action_task_jobs()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.error("[XYZW] 后台巡检执行失败: %s", exc)

            try:
                await asyncio.wait_for(
                    self._scheduler_stop_event.wait(),
                    timeout=self.scheduler_poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def _run_car_reminder_jobs(self) -> None:
        for item in self.storage.iter_enabled_car_reminders():
            reminder = item.get("job", {}) or {}
            if not self._is_car_reminder_due(reminder):
                continue
            await self._check_car_reminder_once(
                user_id=str(item.get("user_id") or ""),
                account=item.get("account", {}) or {},
                reminder=reminder,
                allow_notify=True,
            )

    async def _run_hangup_reminder_jobs(self) -> None:
        for item in self.storage.iter_enabled_hangup_reminders():
            reminder = item.get("job", {}) or {}
            if not self._is_hangup_reminder_due(reminder):
                continue
            await self._check_hangup_reminder_once(
                user_id=str(item.get("user_id") or ""),
                account=item.get("account", {}) or {},
                reminder=reminder,
                allow_notify=True,
            )

    async def _run_helper_member_reminder_jobs(self) -> None:
        for item in self.storage.iter_enabled_helper_member_reminders():
            reminder = item.get("job", {}) or {}
            if not self._is_helper_member_reminder_due(reminder):
                continue
            await self._check_helper_member_reminder_once(
                user_id=str(item.get("user_id") or ""),
                account=item.get("account", {}) or {},
                reminder=reminder,
                allow_notify=True,
            )

    async def _run_activity_reminder_jobs(self) -> None:
        for item in self.storage.iter_enabled_activity_reminders():
            reminder = item.get("job", {}) or {}
            if not self._is_activity_reminder_due(reminder):
                continue
            await self._check_activity_reminder_once(
                user_id=str(item.get("user_id") or ""),
                reminder=reminder,
                allow_notify=True,
            )

    async def _run_daily_task_jobs(self) -> None:
        for item in self.storage.iter_enabled_daily_tasks():
            schedule = item.get("job", {}) or {}
            if not self._is_daily_task_due(schedule):
                continue
            await self._run_daily_task_once(
                user_id=str(item.get("user_id") or ""),
                account=item.get("account", {}) or {},
                schedule=schedule,
                allow_notify=True,
                force_run=False,
            )

    async def _run_action_task_jobs(self) -> None:
        for item in self.storage.iter_enabled_action_tasks():
            schedule = item.get("job", {}) or {}
            if not self._is_action_task_due(schedule):
                continue
            await self._run_action_task_once(
                user_id=str(item.get("user_id") or ""),
                account=item.get("account", {}) or {},
                schedule=schedule,
                allow_notify=True,
                force_run=False,
            )

    async def _check_car_reminder_once(
        self,
        user_id: str,
        account: dict[str, Any],
        reminder: dict[str, Any],
        allow_notify: bool = True,
    ) -> dict[str, Any]:
        account_id = str(account.get("account_id") or "")
        now_iso = self._now_iso()
        notify_group = self.storage.get_notify_group(user_id)
        if not notify_group:
            self.storage.update_car_reminder_runtime(
                user_id,
                account_id,
                last_checked_at=now_iso,
                last_status="blocked",
                last_error_message="未绑定通知群",
                last_error_at=now_iso,
            )
            return {
                "success": False,
                "notified": False,
                "ready_count": 0,
                "status_text": self._car_reminder_status_text("blocked"),
                "error_message": "未绑定通知群",
                "message": "请先使用 /xyzw 通知 绑定本群。",
                "claimable_cars": [],
            }

        response = await self._call_with_account_token_ready(
            user_id,
            account,
            lambda ready_account: self.sidecar.get_car_overview(
                ready_account.get("token", ""),
                timeout_ms=self.car_reminder_check_timeout_ms,
            ),
            reason="car_reminder",
        )
        if not response.get("ok"):
            error_message = response.get("message", "未知错误")
            self.storage.update_car_reminder_runtime(
                user_id,
                account_id,
                last_checked_at=now_iso,
                last_status="error",
                last_error_message=error_message,
                last_error_at=now_iso,
            )
            return {
                "success": False,
                "notified": False,
                "ready_count": 0,
                "status_text": self._car_reminder_status_text("error"),
                "error_message": error_message,
                "message": f"车辆查询失败: {error_message}",
                "claimable_cars": [],
            }

        overview = (response.get("data", {}) or {}).get("overview", {}) or {}
        claimable_cars = [
            car for car in (overview.get("cars") or []) if car.get("claimable")
        ]
        ready_count = len(claimable_cars)
        signature = self._build_car_ready_signature(overview)
        last_signature = str(reminder.get("last_ready_signature") or "")
        last_status = str(reminder.get("last_status") or "")
        runtime_fields: dict[str, Any] = {
            "last_checked_at": now_iso,
            "last_ready_count": ready_count,
            "last_error_message": "",
            "last_error_at": "",
        }

        if ready_count <= 0:
            runtime_fields.update(
                last_status="idle",
                last_ready_signature="",
            )
            self.storage.update_car_reminder_runtime(
                user_id,
                account_id,
                **runtime_fields,
            )
            return {
                "success": True,
                "notified": False,
                "ready_count": 0,
                "status_text": self._car_reminder_status_text("idle"),
                "message": "当前没有可收取车辆。",
                "claimable_cars": [],
            }

        should_notify = allow_notify and (
            signature != last_signature or last_status != "ready"
        )
        if not should_notify:
            runtime_fields["last_status"] = "ready"
            self.storage.update_car_reminder_runtime(
                user_id,
                account_id,
                **runtime_fields,
            )
            return {
                "success": True,
                "notified": False,
                "ready_count": ready_count,
                "status_text": self._car_reminder_status_text("ready"),
                "message": "当前已有可收取车辆，且本轮不重复发送提醒。",
                "claimable_cars": claimable_cars,
            }

        notify_result = await self.notifier.push_group_message(
            user_id=user_id,
            text=self._build_car_reminder_message(account, overview),
        )
        if notify_result.success:
            runtime_fields.update(
                last_status="ready",
                last_ready_signature=signature,
                last_notified_at=now_iso,
            )
            self.storage.update_car_reminder_runtime(
                user_id,
                account_id,
                **runtime_fields,
            )
            return {
                "success": True,
                "notified": True,
                "ready_count": ready_count,
                "status_text": self._car_reminder_status_text("ready"),
                "message": f"收车提醒已发送，channel={notify_result.channel}",
                "claimable_cars": claimable_cars,
            }

        runtime_fields.update(
            last_status="error",
            last_error_message=notify_result.detail or "通知发送失败",
            last_error_at=now_iso,
        )
        self.storage.update_car_reminder_runtime(
            user_id,
            account_id,
            **runtime_fields,
        )
        return {
            "success": False,
            "notified": False,
            "ready_count": ready_count,
            "status_text": self._car_reminder_status_text("error"),
            "error_message": notify_result.detail or "通知发送失败",
            "message": "收车提醒发送失败。",
            "claimable_cars": claimable_cars,
        }

    async def _check_hangup_reminder_once(
        self,
        user_id: str,
        account: dict[str, Any],
        reminder: dict[str, Any],
        allow_notify: bool = True,
    ) -> dict[str, Any]:
        account_id = str(account.get("account_id") or "")
        now_iso = self._now_iso()
        notify_group = self.storage.get_notify_group(user_id)
        if not notify_group:
            self.storage.update_hangup_reminder_runtime(
                user_id,
                account_id,
                last_checked_at=now_iso,
                last_status="blocked",
                last_error_message="未绑定通知群",
                last_error_at=now_iso,
            )
            return {
                "success": False,
                "notified": False,
                "ready": False,
                "remaining_seconds": 0,
                "elapsed_seconds": 0,
                "status_text": self._hangup_reminder_status_text("blocked"),
                "error_message": "未绑定通知群",
                "message": "请先使用 /xyzw 通知 绑定本群。",
            }

        response = await self._call_with_account_token_ready(
            user_id,
            account,
            lambda ready_account: self.sidecar.describe_account(
                ready_account.get("token", ""),
                timeout_ms=self.hangup_reminder_check_timeout_ms,
            ),
            reason="hangup_reminder",
        )
        if not response.get("ok"):
            error_message = response.get("message", "未知错误")
            self.storage.update_hangup_reminder_runtime(
                user_id,
                account_id,
                last_checked_at=now_iso,
                last_status="error",
                last_remaining_seconds=0,
                last_elapsed_seconds=0,
                last_error_message=error_message,
                last_error_at=now_iso,
            )
            return {
                "success": False,
                "notified": False,
                "ready": False,
                "remaining_seconds": 0,
                "elapsed_seconds": 0,
                "status_text": self._hangup_reminder_status_text("error"),
                "error_message": error_message,
                "message": f"挂机状态查询失败: {error_message}",
            }

        describe_data = response.get("data", {}) or {}
        hangup_state = self._extract_hangup_state(describe_data)
        if not hangup_state.get("available"):
            self.storage.update_hangup_reminder_runtime(
                user_id,
                account_id,
                last_checked_at=now_iso,
                last_status="error",
                last_remaining_seconds=0,
                last_elapsed_seconds=0,
                last_error_message="未解析到挂机状态",
                last_error_at=now_iso,
            )
            return {
                "success": False,
                "notified": False,
                "ready": False,
                "remaining_seconds": 0,
                "elapsed_seconds": 0,
                "status_text": self._hangup_reminder_status_text("error"),
                "error_message": "未解析到挂机状态",
                "message": "当前账号未返回可用的挂机状态。",
            }

        remaining_seconds = int(hangup_state.get("remaining_seconds") or 0)
        elapsed_seconds = int(hangup_state.get("elapsed_seconds") or 0)
        ready = bool(hangup_state.get("ready"))
        last_status = str(reminder.get("last_status") or "")
        runtime_fields: dict[str, Any] = {
            "last_checked_at": now_iso,
            "last_remaining_seconds": remaining_seconds,
            "last_elapsed_seconds": elapsed_seconds,
            "last_error_message": "",
            "last_error_at": "",
        }

        if not ready:
            runtime_fields["last_status"] = "idle"
            self.storage.update_hangup_reminder_runtime(
                user_id,
                account_id,
                **runtime_fields,
            )
            return {
                "success": True,
                "notified": False,
                "ready": False,
                "remaining_seconds": remaining_seconds,
                "elapsed_seconds": elapsed_seconds,
                "status_text": self._hangup_reminder_status_text("idle"),
                "error_message": "",
                "message": f"当前仍在挂机中，剩余 {self._format_duration_text(remaining_seconds)}。",
            }

        should_notify = allow_notify and last_status != "ready"
        if not should_notify:
            runtime_fields["last_status"] = "ready"
            self.storage.update_hangup_reminder_runtime(
                user_id,
                account_id,
                **runtime_fields,
            )
            return {
                "success": True,
                "notified": False,
                "ready": True,
                "remaining_seconds": remaining_seconds,
                "elapsed_seconds": elapsed_seconds,
                "status_text": self._hangup_reminder_status_text("ready"),
                "error_message": "",
                "message": "挂机已满，且本轮不重复发送提醒。",
            }

        notify_result = await self.notifier.push_group_message(
            user_id=user_id,
            text=self._build_hangup_reminder_message(account, hangup_state),
        )
        if notify_result.success:
            runtime_fields.update(
                last_status="ready",
                last_notified_at=now_iso,
            )
            self.storage.update_hangup_reminder_runtime(
                user_id,
                account_id,
                **runtime_fields,
            )
            return {
                "success": True,
                "notified": True,
                "ready": True,
                "remaining_seconds": remaining_seconds,
                "elapsed_seconds": elapsed_seconds,
                "status_text": self._hangup_reminder_status_text("ready"),
                "error_message": "",
                "message": f"挂机提醒已发送，channel={notify_result.channel}",
            }

        runtime_fields.update(
            last_status="error",
            last_error_message=notify_result.detail or "通知发送失败",
            last_error_at=now_iso,
        )
        self.storage.update_hangup_reminder_runtime(
            user_id,
            account_id,
            **runtime_fields,
        )
        return {
            "success": False,
            "notified": False,
            "ready": True,
            "remaining_seconds": remaining_seconds,
            "elapsed_seconds": elapsed_seconds,
            "status_text": self._hangup_reminder_status_text("error"),
            "error_message": notify_result.detail or "通知发送失败",
            "message": "挂机提醒发送失败。",
        }

    async def _check_helper_member_reminder_once(
        self,
        user_id: str,
        account: dict[str, Any],
        reminder: dict[str, Any],
        allow_notify: bool = True,
    ) -> dict[str, Any]:
        account_id = str(account.get("account_id") or "")
        now_iso = self._now_iso()
        member_ids = [
            str(item or "").strip()
            for item in (reminder.get("member_ids") or [])
            if str(item or "").strip()
        ]
        notify_group = self.storage.get_notify_group(user_id)
        if not notify_group:
            self.storage.update_helper_member_reminder_runtime(
                user_id,
                account_id,
                last_checked_at=now_iso,
                last_status="blocked",
                last_signature="",
                last_pending_count=0,
                last_pending_members=[],
                last_error_message="未绑定通知群",
                last_error_at=now_iso,
            )
            return {
                "success": False,
                "notified": False,
                "matched_count": 0,
                "pending_count": 0,
                "pending_helpers": [],
                "member_ids": member_ids,
                "status_text": self._helper_member_reminder_status_text("blocked"),
                "error_message": "未绑定通知群",
                "message": "请先使用 /xyzw 通知 绑定本群。",
            }

        if not self._is_car_send_open_now():
            message = self._get_car_send_block_reason() or "当前非发车时段。"
            self.storage.update_helper_member_reminder_runtime(
                user_id,
                account_id,
                last_checked_at=now_iso,
                last_status="window_closed",
                last_signature="",
                last_pending_count=0,
                last_pending_members=[],
                last_error_message="",
                last_error_at="",
            )
            return {
                "success": True,
                "notified": False,
                "matched_count": 0,
                "pending_count": 0,
                "pending_helpers": [],
                "member_ids": member_ids,
                "status_text": self._helper_member_reminder_status_text("window_closed"),
                "error_message": "",
                "message": message,
            }

        response = await self._call_with_account_token_ready(
            user_id,
            account,
            lambda ready_account: self.sidecar.get_car_helpers(
                ready_account.get("token", ""),
                member_ids=member_ids,
                keyword=None,
                timeout_ms=self.car_helper_query_timeout_ms,
            ),
            reason="helper_member_reminder",
        )
        if not response.get("ok"):
            error_message = response.get("message", "未知错误")
            self.storage.update_helper_member_reminder_runtime(
                user_id,
                account_id,
                last_checked_at=now_iso,
                last_status="error",
                last_signature="",
                last_pending_count=0,
                last_pending_members=[],
                last_error_message=error_message,
                last_error_at=now_iso,
            )
            return {
                "success": False,
                "notified": False,
                "matched_count": 0,
                "pending_count": 0,
                "pending_helpers": [],
                "member_ids": member_ids,
                "status_text": self._helper_member_reminder_status_text("error"),
                "error_message": error_message,
                "message": f"护卫成员查询失败: {error_message}",
            }

        data = response.get("data", {}) or {}
        helpers = data.get("helpers", []) or []
        if not helpers:
            error_message = "未匹配到指定的护卫成员"
            self.storage.update_helper_member_reminder_runtime(
                user_id,
                account_id,
                last_checked_at=now_iso,
                last_status="error",
                last_signature="",
                last_pending_count=0,
                last_pending_members=[],
                last_error_message=error_message,
                last_error_at=now_iso,
            )
            return {
                "success": False,
                "notified": False,
                "matched_count": 0,
                "pending_count": 0,
                "pending_helpers": [],
                "member_ids": member_ids,
                "status_text": self._helper_member_reminder_status_text("error"),
                "error_message": error_message,
                "message": (
                    "未匹配到指定的护卫成员，请检查成员ID是否仍在当前俱乐部中。"
                ),
            }

        pending_helpers = [
            helper for helper in helpers if int(helper.get("usedCount") or 0) < int(helper.get("maxCount") or 4)
        ]
        pending_snapshot = self._build_helper_member_runtime_snapshot(pending_helpers)
        signature = self._build_helper_member_reminder_signature(helpers)
        last_signature = str(reminder.get("last_signature") or "")
        last_status = str(reminder.get("last_status") or "")
        runtime_fields: dict[str, Any] = {
            "last_checked_at": now_iso,
            "last_pending_count": len(pending_helpers),
            "last_pending_members": pending_snapshot,
            "last_error_message": "",
            "last_error_at": "",
        }

        if not pending_helpers:
            runtime_fields.update(
                last_status="all_full",
                last_signature="",
            )
            self.storage.update_helper_member_reminder_runtime(
                user_id,
                account_id,
                **runtime_fields,
            )
            return {
                "success": True,
                "notified": False,
                "matched_count": len(helpers),
                "pending_count": 0,
                "pending_helpers": [],
                "member_ids": member_ids,
                "status_text": self._helper_member_reminder_status_text("all_full"),
                "error_message": "",
                "message": "当前监控成员都已达到 4/4 护卫次数。",
            }

        should_notify = allow_notify and (
            signature != last_signature or last_status != "incomplete"
        )
        if not should_notify:
            runtime_fields.update(
                last_status="incomplete",
                last_signature=signature,
            )
            self.storage.update_helper_member_reminder_runtime(
                user_id,
                account_id,
                **runtime_fields,
            )
            return {
                "success": True,
                "notified": False,
                "matched_count": len(helpers),
                "pending_count": len(pending_helpers),
                "pending_helpers": pending_snapshot,
                "member_ids": member_ids,
                "status_text": self._helper_member_reminder_status_text("incomplete"),
                "error_message": "",
                "message": "当前仍有未满护卫成员，但本轮不重复发送提醒。",
            }

        notify_result = await self.notifier.push_group_message(
            user_id=user_id,
            text=self._build_helper_member_reminder_message(
                account,
                reminder,
                pending_helpers,
                len(helpers),
            ),
        )
        if notify_result.success:
            runtime_fields.update(
                last_status="incomplete",
                last_signature=signature,
                last_notified_at=now_iso,
            )
            self.storage.update_helper_member_reminder_runtime(
                user_id,
                account_id,
                **runtime_fields,
            )
            return {
                "success": True,
                "notified": True,
                "matched_count": len(helpers),
                "pending_count": len(pending_helpers),
                "pending_helpers": pending_snapshot,
                "member_ids": member_ids,
                "status_text": self._helper_member_reminder_status_text("incomplete"),
                "error_message": "",
                "message": f"护卫成员提醒已发送，channel={notify_result.channel}",
            }

        runtime_fields.update(
            last_status="error",
            last_signature="",
            last_error_message=notify_result.detail or "通知发送失败",
            last_error_at=now_iso,
        )
        self.storage.update_helper_member_reminder_runtime(
            user_id,
            account_id,
            **runtime_fields,
        )
        return {
            "success": False,
            "notified": False,
            "matched_count": len(helpers),
            "pending_count": len(pending_helpers),
            "pending_helpers": pending_snapshot,
            "member_ids": member_ids,
            "status_text": self._helper_member_reminder_status_text("error"),
            "error_message": notify_result.detail or "通知发送失败",
            "message": "护卫成员提醒发送失败。",
        }

    async def _check_activity_reminder_once(
        self,
        user_id: str,
        reminder: dict[str, Any],
        allow_notify: bool = True,
    ) -> dict[str, Any]:
        activity_key = _normalize_activity_key(str(reminder.get("activity_key") or ""))
        label = self._activity_label(activity_key)
        now_iso = self._now_iso()
        today_key = self._local_date_key()

        if activity_key not in {"dream", "bosstower"}:
            return {
                "success": False,
                "notified": False,
                "status_text": self._activity_reminder_status_text("error"),
                "error_message": "不支持的活动类型",
                "message": "活动开放提醒配置无效。",
            }

        if allow_notify and not self.storage.get_notify_group(user_id):
            self.storage.update_activity_reminder_runtime(
                user_id,
                activity_key,
                last_checked_at=now_iso,
                last_status="blocked",
                last_error_message="未绑定通知群",
                last_error_at=now_iso,
            )
            return {
                "success": False,
                "notified": False,
                "status_text": self._activity_reminder_status_text("blocked"),
                "error_message": "未绑定通知群",
                "message": "请先使用 /xyzw 通知 绑定本群。",
            }

        if not self._is_activity_open(activity_key):
            self.storage.update_activity_reminder_runtime(
                user_id,
                activity_key,
                last_checked_at=now_iso,
                last_status="idle",
                last_error_message="",
                last_error_at="",
            )
            return {
                "success": True,
                "notified": False,
                "status_text": self._activity_reminder_status_text("idle"),
                "error_message": "",
                "message": f"{label}当前未开放。",
            }

        if not allow_notify:
            return {
                "success": True,
                "notified": False,
                "status_text": self._activity_reminder_status_text("idle"),
                "error_message": "",
                "message": f"{label}当前已开放。",
            }

        notify_result = await self.notifier.push_group_message(
            user_id=user_id,
            text=self._build_activity_reminder_message(activity_key),
        )
        if notify_result.success:
            self.storage.update_activity_reminder_runtime(
                user_id,
                activity_key,
                last_checked_at=now_iso,
                last_notified_date=today_key,
                last_notified_at=now_iso,
                last_status="sent",
                last_error_message="",
                last_error_at="",
            )
            return {
                "success": True,
                "notified": True,
                "status_text": self._activity_reminder_status_text("sent"),
                "error_message": "",
                "message": f"{label}开放提醒已发送，channel={notify_result.channel}",
            }

        self.storage.update_activity_reminder_runtime(
            user_id,
            activity_key,
            last_checked_at=now_iso,
            last_status="error",
            last_error_message=notify_result.detail or "通知发送失败",
            last_error_at=now_iso,
        )
        return {
            "success": False,
            "notified": False,
            "status_text": self._activity_reminder_status_text("error"),
            "error_message": notify_result.detail or "通知发送失败",
            "message": f"{label}开放提醒发送失败。",
        }

    async def _run_daily_task_once(
        self,
        user_id: str,
        account: dict[str, Any],
        schedule: dict[str, Any],
        allow_notify: bool = True,
        force_run: bool = False,
    ) -> dict[str, Any]:
        account_id = str(account.get("account_id") or "")
        now_iso = self._now_iso()
        today_key = self._local_date_key()

        if self._is_daily_task_busy(account_id):
            return {
                "success": False,
                "notified": False,
                "total_count": 0,
                "success_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "failed_steps": [],
                "status_text": self._daily_task_status_text("running"),
                "error_message": "",
                "message": "当前账号的定时日常正在执行，请稍后再试。",
            }

        if allow_notify and not self.storage.get_notify_group(user_id):
            self.storage.update_daily_task_runtime(
                user_id,
                account_id,
                last_run_date=today_key,
                last_run_at=now_iso,
                last_status="blocked",
                last_total_count=0,
                last_success_count=0,
                last_skipped_count=0,
                last_failed_count=0,
                last_error_message="未绑定通知群",
                last_error_at=now_iso,
            )
            return {
                "success": False,
                "notified": False,
                "total_count": 0,
                "success_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "failed_steps": [],
                "status_text": self._daily_task_status_text("blocked"),
                "error_message": "未绑定通知群",
                "message": "请先使用 /xyzw 通知 绑定本群。",
            }

        if not force_run and not self._is_daily_task_due(schedule):
            return {
                "success": True,
                "notified": False,
                "total_count": int(schedule.get("last_total_count") or 0),
                "success_count": int(schedule.get("last_success_count") or 0),
                "skipped_count": int(schedule.get("last_skipped_count") or 0),
                "failed_count": int(schedule.get("last_failed_count") or 0),
                "failed_steps": [],
                "status_text": self._daily_task_status_text(schedule.get("last_status")),
                "error_message": "",
                "message": "当前还未到定时日常执行时间，或今天已执行过。",
            }

        self._running_daily_task_account_ids.add(account_id)
        try:
            daily_options = self._build_daily_task_options(account)
            response = await self._call_with_account_token_ready(
                user_id,
                account,
                lambda ready_account: self.sidecar.run_daily_task(
                    ready_account.get("token", ""),
                    options=daily_options,
                    timeout_ms=self.daily_task_timeout_ms,
                ),
                reason="scheduled_daily",
            )
            if not response.get("ok"):
                error_message = response.get("message", "未知错误")
                self.storage.update_daily_task_runtime(
                    user_id,
                    account_id,
                    last_run_date=today_key,
                    last_run_at=now_iso,
                    last_status="failed",
                    last_total_count=0,
                    last_success_count=0,
                    last_skipped_count=0,
                    last_failed_count=0,
                    last_error_message=error_message,
                    last_error_at=now_iso,
                )
                return {
                    "success": False,
                    "notified": False,
                    "total_count": 0,
                    "success_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0,
                    "failed_steps": [],
                    "status_text": self._daily_task_status_text("failed"),
                    "error_message": error_message,
                    "message": f"日常执行失败: {error_message}",
                }

            data = response.get("data", {}) or {}
            steps = data.get("steps", []) or []
            failed_steps = [step for step in steps if step.get("status") == "failed"]
            total_count = int(data.get("totalCount") or 0)
            success_count = int(data.get("successCount") or 0)
            skipped_count = int(data.get("skippedCount") or 0)
            failed_count = int(data.get("failedCount") or 0)

            if total_count <= 0:
                status = "empty"
                message = "当前没有可执行的简版日常。"
            elif failed_count > 0:
                status = "partial_failed" if success_count > 0 or skipped_count > 0 else "failed"
                message = "定时日常已执行完成，但存在失败步骤。"
            else:
                status = "success"
                message = "定时日常执行完成。"

            runtime_fields = {
                "last_run_date": today_key,
                "last_run_at": now_iso,
                "last_status": status,
                "last_total_count": total_count,
                "last_success_count": success_count,
                "last_skipped_count": skipped_count,
                "last_failed_count": failed_count,
                "last_error_message": "",
                "last_error_at": "",
            }
            result = {
                "success": status in {"success", "empty", "partial_failed"},
                "notified": False,
                "total_count": total_count,
                "success_count": success_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
                "failed_steps": failed_steps,
                "status_text": self._daily_task_status_text(status),
                "error_message": "",
                "message": message,
                "daily_result": data,
            }

            if allow_notify and status != "empty":
                notify_result = await self.notifier.push_group_message(
                    user_id=user_id,
                    text=self._build_daily_task_notification_message(account, result),
                )
                if notify_result.success:
                    runtime_fields["last_notified_at"] = now_iso
                    result["notified"] = True
                else:
                    runtime_fields["last_error_message"] = notify_result.detail or "通知发送失败"
                    runtime_fields["last_error_at"] = now_iso
                    result["error_message"] = notify_result.detail or "通知发送失败"

            self.storage.update_daily_task_runtime(
                user_id,
                account_id,
                **runtime_fields,
            )
            return result
        finally:
            self._running_daily_task_account_ids.discard(account_id)

    async def _run_action_task_once(
        self,
        user_id: str,
        account: dict[str, Any],
        schedule: dict[str, Any],
        allow_notify: bool = True,
        force_run: bool = False,
    ) -> dict[str, Any]:
        job_id = str(schedule.get("job_id") or "").strip()
        category = str(schedule.get("category") or "").strip().lower()
        action = str(schedule.get("action") or "").strip()
        label = str(schedule.get("label") or "").strip()
        now_iso = self._now_iso()
        today_key = self._local_date_key()

        if not job_id or category not in {"resource", "dungeon"} or not action:
            return {
                "success": False,
                "notified": False,
                "status_text": self._scheduled_action_status_text("failed"),
                "error_message": "定时执行配置无效",
                "message": "当前定时执行配置不完整，请重新创建。",
                "result_text": "",
            }

        if job_id in self._running_action_task_job_ids:
            return {
                "success": False,
                "notified": False,
                "status_text": self._scheduled_action_status_text("running"),
                "error_message": "",
                "message": "当前定时执行任务正在运行，请稍后再试。",
                "result_text": "",
            }

        if allow_notify and not self.storage.get_notify_group(user_id):
            self.storage.update_action_task_runtime(
                user_id,
                job_id,
                last_run_date=today_key,
                last_run_at=now_iso,
                last_status="blocked",
                last_error_message="未绑定通知群",
                last_error_at=now_iso,
                last_result_message="",
            )
            return {
                "success": False,
                "notified": False,
                "status_text": self._scheduled_action_status_text("blocked"),
                "error_message": "未绑定通知群",
                "message": "请先使用 /xyzw 通知 绑定本群。",
                "result_text": "",
            }

        if not force_run and not self._is_action_task_due(schedule):
            return {
                "success": True,
                "notified": False,
                "status_text": self._scheduled_action_status_text(
                    schedule.get("last_status")
                ),
                "error_message": "",
                "message": "当前还未到定时执行时间，或今天已执行过。",
                "result_text": "",
            }

        options = schedule.get("options")
        timeout_ms = int(schedule.get("timeout_ms") or 0) or None

        self._running_action_task_job_ids.add(job_id)
        try:
            if category == "resource":
                execution = await self._run_resource_action_request(
                    user_id=user_id,
                    account=account,
                    action=action,
                    options=options,
                    timeout_ms=timeout_ms,
                )
            else:
                execution = await self._run_dungeon_action_request(
                    user_id=user_id,
                    account=account,
                    action=action,
                    options=options,
                    timeout_ms=timeout_ms,
                )

            data = execution.get("data", {}) or {}
            runtime_fields: dict[str, Any] = {
                "last_run_date": today_key,
                "last_run_at": now_iso,
            }

            if execution.get("ok"):
                result_text = (
                    self._format_resource_result_text(account, data)
                    if category == "resource"
                    else self._format_dungeon_result_text(account, data)
                )
                runtime_fields.update(
                    last_status="success",
                    last_error_message="",
                    last_error_at="",
                    last_result_message=str(
                        data.get("message")
                        or data.get("label")
                        or label
                        or "执行完成"
                    ).strip(),
                )
                result = {
                    "success": True,
                    "notified": False,
                    "status_text": self._scheduled_action_status_text("success"),
                    "error_message": "",
                    "message": f"定时{self._scheduled_action_category_label(category)}执行完成。",
                    "result_text": result_text,
                    "action_result": data,
                }
            else:
                error_message = str(execution.get("error_message") or "未知错误").strip()
                runtime_fields.update(
                    last_status="failed",
                    last_error_message=error_message,
                    last_error_at=now_iso,
                    last_result_message=error_message,
                )
                result = {
                    "success": False,
                    "notified": False,
                    "status_text": self._scheduled_action_status_text("failed"),
                    "error_message": error_message,
                    "message": f"定时{self._scheduled_action_category_label(category)}执行失败。",
                    "result_text": "",
                    "action_result": data,
                }

            if allow_notify:
                notify_result = await self.notifier.push_group_message(
                    user_id=user_id,
                    text=self._build_action_task_notification_message(
                        account,
                        schedule,
                        result,
                    ),
                )
                if notify_result.success:
                    runtime_fields["last_notified_at"] = now_iso
                    result["notified"] = True
                else:
                    notify_error = notify_result.detail or "通知发送失败"
                    if result.get("success"):
                        runtime_fields["last_error_message"] = notify_error
                    else:
                        runtime_fields["last_error_message"] = (
                            f"{runtime_fields.get('last_error_message')}; 通知失败: {notify_error}"
                        )
                    runtime_fields["last_error_at"] = now_iso
                    result["error_message"] = (
                        notify_error
                        if result.get("success")
                        else f"{result.get('error_message')}; 通知失败: {notify_error}"
                    )

            self.storage.update_action_task_runtime(
                user_id,
                job_id,
                **runtime_fields,
            )
            return result
        finally:
            self._running_action_task_job_ids.discard(job_id)

    @filter.command("xyzw")
    async def handle_xyzw_command(self, event: AstrMessageEvent):
        self._remember_bot_self_id(event)
        tokens = self.parse_commands(event.message_str)

        if tokens.len < 2:
            yield event.plain_result(self._help_text())
            return

        subcommand = tokens.tokens[1].lower()

        if subcommand in {"help", "帮助"}:
            yield event.plain_result(self._help_text())
            return

        if subcommand in {"健康", "health"}:
            async for result in self._handle_healthcheck(event):
                yield result
            return

        if subcommand == "计划":
            yield event.plain_result(
                "实施计划文档已生成：\n"
                "astrbot_plugin_xyzw/docs/IMPLEMENTATION_PLAN.md\n\n"
                "README 中也包含当前实现说明和目录结构。"
            )
            return

        if subcommand == "架构":
            yield event.plain_result(self.sidecar.describe())
            return

        if subcommand == "绑定":
            async for result in self._handle_bind(event, tokens):
                yield result
            return

        if subcommand == "账号":
            async for result in self._handle_account(event, tokens):
                yield result
            return

        if subcommand == "状态":
            async for result in self._handle_status(event, tokens):
                yield result
            return

        if subcommand in {"车", "车辆"}:
            async for result in self._handle_car(event, tokens):
                yield result
            return

        if subcommand == "日常":
            async for result in self._handle_daily(event, tokens):
                yield result
            return

        if subcommand in {"副本", "dungeon"}:
            async for result in self._handle_dungeon(event, tokens):
                yield result
            return

        if subcommand in {"资源", "resource"}:
            async for result in self._handle_resource(event, tokens):
                yield result
            return

        if subcommand == "通知":
            async for result in self._handle_notify(event, tokens):
                yield result
            return

        if subcommand in {"定时", "schedule"}:
            async for result in self._handle_schedule(event, tokens):
                yield result
            return

        yield event.plain_result(f"未知命令: {subcommand}\n\n{self._help_text()}")

    async def _handle_healthcheck(self, event: AstrMessageEvent):
        response = await self.sidecar.healthcheck()
        if response.get("ok"):
            data = response.get("data", {})
            yield event.plain_result(
                "sidecar 健康检查成功\n"
                f"- service: {data.get('service', '-')}\n"
                f"- version: {data.get('version', '-')}\n"
                f"- uptimeSeconds: {data.get('uptimeSeconds', '-')}\n"
                f"- allowOrigin: {data.get('cors', {}).get('allowOrigin', '-')}"
            )
            return

        yield event.plain_result(
            "sidecar 健康检查失败\n"
            f"- code: {response.get('code', '-')}\n"
            f"- message: {response.get('message', '-')}"
        )

    async def _handle_bind(self, event: AstrMessageEvent, tokens):
        if tokens.len >= 3 and tokens.tokens[2].lower() in {"help", "帮助"}:
            yield event.plain_result(self._binding_usage())
            return

        if self.binding_private_only and not event.is_private_chat():
            yield event.plain_result(self._binding_permission_text())
            return

        bind_mode = "token"
        if tokens.len >= 3:
            candidate = tokens.tokens[2].lower()
            if candidate in {"token", "manual", "手动"}:
                if tokens.len >= 4 and tokens.tokens[3].lower() in {"help", "帮助"}:
                    yield event.plain_result(self._binding_usage())
                    return
                bind_mode = "token"
            elif candidate in {"url", "链接", "地址"}:
                if tokens.len >= 4 and tokens.tokens[3].lower() in {"help", "帮助"}:
                    yield event.plain_result(self._binding_url_usage())
                    return
                bind_mode = "url"
            elif candidate in {"bin", "文件", "base64"}:
                if tokens.len >= 4 and tokens.tokens[3].lower() in {"help", "帮助"}:
                    yield event.plain_result(self._binding_bin_usage())
                    return
                bind_mode = "bin"
            elif candidate in {"wx", "wechat", "微信", "扫码"}:
                if tokens.len >= 4 and tokens.tokens[3].lower() in {"help", "帮助"}:
                    yield event.plain_result(self._binding_wx_usage())
                    return
                bind_mode = "wx"

        if bind_mode == "bin":
            async for result in self._handle_bind_bin(event):
                yield result
            return

        if bind_mode == "url":
            async for result in self._handle_bind_url(event):
                yield result
            return

        if bind_mode == "wx":
            async for result in self._handle_bind_wx(event):
                yield result
            return

        async for result in self._handle_bind_token(event):
            yield result

    async def _handle_bind_token(self, event: AstrMessageEvent):
        user_id = self._get_user_id(event)
        session_state: dict[str, Any] = {
            "stage": "token",
            "token": "",
            "summary": {},
        }

        yield event.plain_result(
            "开始绑定 XYZW 账号。\n"
            "第 1 步：请直接发送 WebSocket-ready token。\n"
            "输入 取消 可退出本次绑定。"
        )

        @session_waiter(timeout=180, record_history_chains=False)
        async def bind_waiter(
            controller: SessionController,
            wait_event: AstrMessageEvent,
        ) -> None:
            text = (wait_event.message_str or "").strip()
            if not text:
                await wait_event.send(
                    wait_event.plain_result("请输入有效内容，或发送 取消 结束绑定。")
                )
                controller.keep(timeout=180, reset_timeout=True)
                return

            if text.lower() in {"取消", "退出", "cancel", "quit"}:
                await wait_event.send(wait_event.plain_result("已取消本次账号绑定。"))
                controller.stop()
                return

            if session_state["stage"] == "token":
                response = await self.sidecar.verify_token(text)
                if not response.get("ok"):
                    await wait_event.send(
                        wait_event.plain_result(
                            "token 校验失败，请重新发送。\n"
                            f"原因: {response.get('message', '未知错误')}"
                        )
                    )
                    controller.keep(timeout=180, reset_timeout=True)
                    return

                data = response.get("data", {})
                if not data.get("verified"):
                    await wait_event.send(
                        wait_event.plain_result("token 校验未通过，请重新发送。")
                    )
                    controller.keep(timeout=180, reset_timeout=True)
                    return

                summary = data.get("summary", {}) or {}
                session_state["token"] = text
                session_state["summary"] = summary
                suggestion = self._build_alias_suggestion(summary)
                session_state["stage"] = "alias"

                await wait_event.send(
                    wait_event.plain_result(
                        "token 校验成功。\n\n"
                        f"{self._format_summary(summary)}\n\n"
                        "第 2 步：请发送账号别名。\n"
                        f"建议别名: {suggestion}"
                    )
                )
                controller.keep(timeout=180, reset_timeout=True)
                return

            alias = text or self._build_alias_suggestion(session_state["summary"])
            try:
                saved = self.storage.save_account_with_options(
                    user_id=user_id,
                    alias=alias,
                    token=session_state["token"],
                    summary=session_state["summary"],
                    import_method="manual",
                )
            except ValueError as exc:
                await wait_event.send(
                    wait_event.plain_result(
                        f"{exc}\n请重新发送新的账号别名，或输入 取消 结束。"
                    )
                )
                controller.keep(timeout=180, reset_timeout=True)
                return

            await wait_event.send(
                wait_event.plain_result(
                    self._format_bind_result(
                        account=saved["account"],
                        summary=session_state["summary"],
                        created=bool(saved["created"]),
                        is_default=bool(saved["is_default"]),
                    )
                )
            )
            controller.stop()

        try:
            await bind_waiter(event, session_filter=UserBindSessionFilter())
        except TimeoutError:
            yield event.plain_result("账号绑定超时，已结束本次会话。")
        except Exception as exc:
            self.logger.error("[XYZW] 账号绑定失败: %s", exc)
            yield event.plain_result(f"账号绑定失败: {exc}")
        finally:
            event.stop_event()

    async def _handle_bind_bin(self, event: AstrMessageEvent):
        user_id = self._get_user_id(event)
        session_state: dict[str, Any] = {
            "stage": "bin_input",
            "bin_base64": "",
            "roles": [],
            "token": "",
            "summary": {},
            "selected_role": None,
            "source_url": "",
        }

        yield event.plain_result(
            "开始 BIN 导入绑定。\n"
            "第 1 步：请发送 BIN 文件，或直接发送 BIN 的 base64 文本。\n"
            "输入 取消 可退出本次绑定。"
        )

        @session_waiter(timeout=240, record_history_chains=False)
        async def bind_waiter(
            controller: SessionController,
            wait_event: AstrMessageEvent,
        ) -> None:
            text = (wait_event.message_str or "").strip()
            if text.lower() in {"取消", "退出", "cancel", "quit"}:
                await wait_event.send(wait_event.plain_result("已取消本次账号绑定。"))
                controller.stop()
                return

            if session_state["stage"] == "bin_input":
                bin_base64, error_text = await self._extract_bin_base64_from_event(
                    wait_event
                )
                if not bin_base64:
                    await wait_event.send(
                        wait_event.plain_result(error_text or "未获取到有效 BIN 数据。")
                    )
                    controller.keep(timeout=240, reset_timeout=True)
                    return

                response = await self.sidecar.get_server_list_from_bin_base64(bin_base64)
                if not response.get("ok"):
                    await wait_event.send(
                        wait_event.plain_result(
                            "BIN 解析失败，请重新发送。\n"
                            f"原因: {response.get('message', '未知错误')}"
                        )
                    )
                    controller.keep(timeout=240, reset_timeout=True)
                    return

                roles = response.get("data", {}).get("roles", []) or []
                if not roles:
                    await wait_event.send(
                        wait_event.plain_result(
                            "未从 BIN 中解析到可绑定角色，请确认文件是否正确后重试。"
                        )
                    )
                    controller.keep(timeout=240, reset_timeout=True)
                    return

                session_state["bin_base64"] = bin_base64
                session_state["roles"] = roles
                source_url, source_error = await self._register_bin_source_url(
                    bin_base64,
                    source_type="bin",
                    metadata={"bind_mode": "bin"},
                )
                if not source_url and source_error:
                    self.logger.warning("[XYZW] BIN refresh_url 注册失败: %s", source_error)
                session_state["source_url"] = source_url or ""

                if len(roles) == 1:
                    selected_role = roles[0]
                    token, summary, resolve_error = await self._resolve_bin_binding_token(
                        bin_base64,
                        selected_role,
                    )
                    if resolve_error:
                        await wait_event.send(wait_event.plain_result(resolve_error))
                        controller.keep(timeout=240, reset_timeout=True)
                        return

                    session_state["selected_role"] = selected_role
                    session_state["token"] = token or ""
                    session_state["summary"] = summary or {}
                    session_state["source_url"] = (
                        self._finalize_source_url_for_role(
                            session_state.get("source_url"),
                            selected_role,
                        )
                        or ""
                    )
                    session_state["stage"] = "alias"
                    suggestion = self._build_alias_suggestion(summary or {})

                    await wait_event.send(
                        wait_event.plain_result(
                            "BIN 解析成功，已自动选择唯一角色。\n\n"
                            f"{self._format_summary(summary or {})}\n\n"
                            "第 2 步：请发送账号别名。\n"
                            f"建议别名: {suggestion}"
                        )
                    )
                    controller.keep(timeout=240, reset_timeout=True)
                    return

                session_state["stage"] = "role_select"
                await wait_event.send(
                    wait_event.plain_result(self._format_bind_role_choices(roles))
                )
                controller.keep(timeout=240, reset_timeout=True)
                return

            if session_state["stage"] == "role_select":
                selected_role = self._select_bind_role(session_state["roles"], text)
                if not selected_role:
                    await wait_event.send(
                        wait_event.plain_result(
                            "未找到目标角色，请重新发送序号或 serverId。\n\n"
                            f"{self._format_bind_role_choices(session_state['roles'])}"
                        )
                    )
                    controller.keep(timeout=240, reset_timeout=True)
                    return

                token, summary, resolve_error = await self._resolve_bin_binding_token(
                    session_state["bin_base64"],
                    selected_role,
                )
                if resolve_error:
                    await wait_event.send(wait_event.plain_result(resolve_error))
                    controller.keep(timeout=240, reset_timeout=True)
                    return

                session_state["selected_role"] = selected_role
                session_state["token"] = token or ""
                session_state["summary"] = summary or {}
                session_state["source_url"] = (
                    self._finalize_source_url_for_role(
                        session_state.get("source_url"),
                        selected_role,
                    )
                    or ""
                )
                session_state["stage"] = "alias"
                suggestion = self._build_alias_suggestion(summary or {})

                await wait_event.send(
                    wait_event.plain_result(
                        "角色选择成功。\n\n"
                        f"{self._format_summary(summary or {})}\n\n"
                        "第 3 步：请发送账号别名。\n"
                        f"建议别名: {suggestion}"
                    )
                )
                controller.keep(timeout=240, reset_timeout=True)
                return

            if not text:
                await wait_event.send(
                    wait_event.plain_result("请输入账号别名，或发送 取消 结束绑定。")
                )
                controller.keep(timeout=240, reset_timeout=True)
                return

            try:
                saved = self.storage.save_account_with_options(
                    user_id=user_id,
                    alias=text,
                    token=session_state["token"],
                    summary=session_state["summary"],
                    import_method="bin",
                    source_url=session_state.get("source_url") or None,
                )
            except ValueError as exc:
                await wait_event.send(
                    wait_event.plain_result(
                        f"{exc}\n请重新发送新的账号别名，或输入 取消 结束。"
                    )
                )
                controller.keep(timeout=240, reset_timeout=True)
                return

            await wait_event.send(
                wait_event.plain_result(
                    self._format_bind_result(
                        account=saved["account"],
                        summary=session_state["summary"],
                        created=bool(saved["created"]),
                        is_default=bool(saved["is_default"]),
                    )
                )
            )
            controller.stop()

        try:
            await bind_waiter(event, session_filter=UserBindSessionFilter())
        except TimeoutError:
            yield event.plain_result("BIN 绑定超时，已结束本次会话。")
        except Exception as exc:
            self.logger.error("[XYZW] BIN 绑定失败: %s", exc)
            yield event.plain_result(f"BIN 绑定失败: {exc}")
        finally:
            event.stop_event()

    async def _handle_bind_wx(self, event: AstrMessageEvent):
        user_id = self._get_user_id(event)
        session_state: dict[str, Any] = {
            "stage": "waiting_scan",
            "qr_uuid": "",
            "bin_base64": "",
            "roles": [],
            "token": "",
            "summary": {},
            "selected_role": None,
            "source_url": "",
            "closed": False,
            "scan_notified": False,
        }

        start_response = await self.sidecar.start_wechat_qrcode()
        if not start_response.get("ok"):
            yield event.plain_result(
                f"微信扫码二维码获取失败: {start_response.get('message', '未知错误')}"
            )
            return

        qrcode_data = start_response.get("data", {}) or {}
        qrcode_url = str(qrcode_data.get("qrcode_url") or "").strip()
        qr_uuid = str(qrcode_data.get("uuid") or "").strip()
        if not qrcode_url or not qr_uuid:
            yield event.plain_result("微信扫码二维码获取失败: sidecar 未返回有效二维码信息。")
            return

        session_state["qr_uuid"] = qr_uuid
        qr_chain = [
            Comp.Plain(
                "开始微信扫码绑定。\n"
                "请使用微信扫描下方二维码并确认登录。\n"
                "扫码成功后会自动进入角色选择。\n"
                "输入 取消 可退出本次绑定。"
            ),
            Comp.Image.fromURL(qrcode_url),
        ]
        private_sent = await self._push_private_chain(user_id, qr_chain)
        if private_sent:
            yield event.plain_result(
                "微信扫码二维码已发送到你的 QQ 私聊。\n"
                "请在私聊窗口中扫码并确认登录。\n"
                "输入 取消 可退出本次绑定。"
            )
        else:
            yield event.chain_result(qr_chain)

        async def poll_wechat_status() -> None:
            while not session_state["closed"]:
                if session_state["stage"] not in {"waiting_scan", "scanned"}:
                    return

                status_response = await self.sidecar.get_wechat_qrcode_status(
                    session_state["qr_uuid"]
                )
                if not status_response.get("ok"):
                    await self._push_text_to_session(
                        session="",
                        text=(
                            "微信扫码状态查询失败，请稍后重试。\n"
                            f"- 原因: {status_response.get('message', '未知错误')}"
                        ),
                        sender_uin=event.get_self_id(),
                        fallback_user_id=user_id,
                    )
                    session_state["closed"] = True
                    return

                status_data = status_response.get("data", {}) or {}
                state = str(status_data.get("state") or "").strip().lower()
                if state == "scanned" and not session_state["scan_notified"]:
                    session_state["stage"] = "scanned"
                    session_state["scan_notified"] = True
                    await self._push_text_to_session(
                        session="",
                        text="二维码已扫码，请在微信里确认登录。",
                        sender_uin=event.get_self_id(),
                        fallback_user_id=user_id,
                    )
                elif state in {"confirmed", "success"}:
                    consume_response = await self.sidecar.consume_wechat_qrcode(
                        uuid=session_state["qr_uuid"]
                    )
                    if not consume_response.get("ok"):
                        await self._push_text_to_session(
                            session="",
                            text=(
                                "扫码已确认，但登录处理失败。\n"
                                f"- 原因: {consume_response.get('message', '未知错误')}"
                            ),
                            sender_uin=event.get_self_id(),
                            fallback_user_id=user_id,
                        )
                        session_state["closed"] = True
                        return

                    consume_data = consume_response.get("data", {}) or {}
                    roles = consume_data.get("roles", []) or []
                    bin_base64 = str(consume_data.get("bin_base64") or "").strip()
                    source_payload = consume_data.get("source", {}) or {}
                    session_state["bin_base64"] = bin_base64
                    session_state["roles"] = roles
                    session_state["source_url"] = self._build_source_url_from_payload(
                        source_payload
                    )

                    if not bin_base64 or not roles:
                        await self._push_text_to_session(
                            session="",
                            text="扫码成功，但未解析到可绑定角色，请重新发起绑定。",
                            sender_uin=event.get_self_id(),
                            fallback_user_id=user_id,
                        )
                        session_state["closed"] = True
                        return

                    if len(roles) == 1:
                        selected_role = roles[0]
                        token, summary, resolve_error = await self._resolve_bin_binding_token(
                            bin_base64,
                            selected_role,
                        )
                        if resolve_error:
                            await self._push_text_to_session(
                                session="",
                                text=f"扫码成功，但角色 token 生成失败。\n- 原因: {resolve_error}",
                                sender_uin=event.get_self_id(),
                                fallback_user_id=user_id,
                            )
                            session_state["closed"] = True
                            return

                        session_state["selected_role"] = selected_role
                        session_state["token"] = token or ""
                        session_state["summary"] = summary or {}
                        session_state["source_url"] = (
                            self._finalize_source_url_for_role(
                                session_state.get("source_url"),
                                selected_role,
                            )
                            or ""
                        )
                        session_state["stage"] = "alias"
                        suggestion = self._build_alias_suggestion(summary or {})
                        await self._push_text_to_session(
                            session="",
                            text=(
                                "扫码登录成功，已自动选择唯一角色。\n\n"
                                f"{self._format_summary(summary or {})}\n\n"
                                "请发送账号别名。\n"
                                f"建议别名: {suggestion}"
                            ),
                            sender_uin=event.get_self_id(),
                            fallback_user_id=user_id,
                        )
                        return

                    session_state["stage"] = "role_select"
                    await self._push_text_to_session(
                        session="",
                        text=(
                            "扫码登录成功，请选择要绑定的角色。\n\n"
                            f"{self._format_bind_role_choices(roles)}"
                        ),
                        sender_uin=event.get_self_id(),
                        fallback_user_id=user_id,
                    )
                    return
                elif state == "expired":
                    await self._push_text_to_session(
                        session="",
                        text="二维码已过期，请重新发送 /xyzw 绑定 wx。",
                        sender_uin=event.get_self_id(),
                        fallback_user_id=user_id,
                    )
                    session_state["closed"] = True
                    return

                await asyncio.sleep(self.wechat_qrcode_poll_interval_ms / 1000)

        poll_task = asyncio.create_task(
            poll_wechat_status(),
            name=f"xyzw_bind_wx_poll:{qr_uuid}",
        )
        self._track_background_task(poll_task, f"xyzw_bind_wx_poll:{qr_uuid}")

        @session_waiter(timeout=240, record_history_chains=False)
        async def bind_waiter(
            controller: SessionController,
            wait_event: AstrMessageEvent,
        ) -> None:
            text = (wait_event.message_str or "").strip()
            if text.lower() in {"取消", "退出", "cancel", "quit"}:
                session_state["closed"] = True
                poll_task.cancel()
                await wait_event.send(wait_event.plain_result("已取消本次微信扫码绑定。"))
                controller.stop()
                return

            if session_state["closed"]:
                await wait_event.send(
                    wait_event.plain_result("本次微信扫码绑定已结束，请重新发送 /xyzw 绑定 wx。")
                )
                controller.stop()
                return

            if session_state["stage"] in {"waiting_scan", "scanned"}:
                await wait_event.send(
                    wait_event.plain_result("请先完成微信扫码确认，或输入 取消 结束绑定。")
                )
                controller.keep(timeout=240, reset_timeout=True)
                return

            if session_state["stage"] == "role_select":
                selected_role = self._select_bind_role(session_state["roles"], text)
                if not selected_role:
                    await wait_event.send(
                        wait_event.plain_result(
                            "未找到目标角色，请重新发送序号或 serverId。\n\n"
                            f"{self._format_bind_role_choices(session_state['roles'])}"
                        )
                    )
                    controller.keep(timeout=240, reset_timeout=True)
                    return

                token, summary, resolve_error = await self._resolve_bin_binding_token(
                    session_state["bin_base64"],
                    selected_role,
                )
                if resolve_error:
                    await wait_event.send(wait_event.plain_result(resolve_error))
                    controller.keep(timeout=240, reset_timeout=True)
                    return

                session_state["selected_role"] = selected_role
                session_state["token"] = token or ""
                session_state["summary"] = summary or {}
                session_state["source_url"] = (
                    self._finalize_source_url_for_role(
                        session_state.get("source_url"),
                        selected_role,
                    )
                    or ""
                )
                session_state["stage"] = "alias"
                suggestion = self._build_alias_suggestion(summary or {})
                await wait_event.send(
                    wait_event.plain_result(
                        "角色选择成功。\n\n"
                        f"{self._format_summary(summary or {})}\n\n"
                        "第 3 步：请发送账号别名。\n"
                        f"建议别名: {suggestion}"
                    )
                )
                controller.keep(timeout=240, reset_timeout=True)
                return

            if not text:
                await wait_event.send(
                    wait_event.plain_result("请输入账号别名，或发送 取消 结束绑定。")
                )
                controller.keep(timeout=240, reset_timeout=True)
                return

            try:
                saved = self.storage.save_account_with_options(
                    user_id=user_id,
                    alias=text,
                    token=session_state["token"],
                    summary=session_state["summary"],
                    import_method="wx_qrcode",
                    source_url=session_state.get("source_url") or None,
                )
            except ValueError as exc:
                await wait_event.send(
                    wait_event.plain_result(
                        f"{exc}\n请重新发送新的账号别名，或输入 取消 结束。"
                    )
                )
                controller.keep(timeout=240, reset_timeout=True)
                return

            session_state["closed"] = True
            poll_task.cancel()
            await wait_event.send(
                wait_event.plain_result(
                    self._format_bind_result(
                        account=saved["account"],
                        summary=session_state["summary"],
                        created=bool(saved["created"]),
                        is_default=bool(saved["is_default"]),
                    )
                )
            )
            controller.stop()

        try:
            await bind_waiter(event, session_filter=UserBindSessionFilter())
        except TimeoutError:
            session_state["closed"] = True
            poll_task.cancel()
            yield event.plain_result("微信扫码绑定超时，已结束本次会话。")
        except Exception as exc:
            session_state["closed"] = True
            poll_task.cancel()
            self.logger.error("[XYZW] 微信扫码绑定失败: %s", exc)
            yield event.plain_result(f"微信扫码绑定失败: {exc}")
        finally:
            event.stop_event()

    async def _handle_bind_url(self, event: AstrMessageEvent):
        user_id = self._get_user_id(event)
        session_state: dict[str, Any] = {
            "stage": "url",
            "source_url": "",
            "token": "",
            "summary": {},
        }

        yield event.plain_result(
            "开始 URL 导入绑定。\n"
            "第 1 步：请直接发送返回 JSON 的 HTTP/HTTPS 地址。\n"
            "要求 JSON 顶层为原始 token 对象，或包含 token / data.token。\n"
            "输入 取消 可退出本次绑定。"
        )

        @session_waiter(timeout=180, record_history_chains=False)
        async def bind_waiter(
            controller: SessionController,
            wait_event: AstrMessageEvent,
        ) -> None:
            text = (wait_event.message_str or "").strip()
            if not text:
                await wait_event.send(
                    wait_event.plain_result("请输入有效 URL，或发送 取消 结束绑定。")
                )
                controller.keep(timeout=180, reset_timeout=True)
                return

            if text.lower() in {"取消", "退出", "cancel", "quit"}:
                await wait_event.send(wait_event.plain_result("已取消本次账号绑定。"))
                controller.stop()
                return

            if session_state["stage"] == "url":
                token, fetch_error = await self._fetch_token_from_url(text)
                if not token:
                    await wait_event.send(
                        wait_event.plain_result(
                            f"URL 拉取失败，请重新发送。\n原因: {fetch_error or '未知错误'}"
                        )
                    )
                    controller.keep(timeout=180, reset_timeout=True)
                    return

                response = await self.sidecar.verify_token(token)
                if not response.get("ok"):
                    await wait_event.send(
                        wait_event.plain_result(
                            "URL 中的 token 校验失败，请重新发送。\n"
                            f"原因: {response.get('message', '未知错误')}"
                        )
                    )
                    controller.keep(timeout=180, reset_timeout=True)
                    return

                data = response.get("data", {})
                if not data.get("verified"):
                    await wait_event.send(
                        wait_event.plain_result(
                            "URL 中的 token 校验未通过，请重新发送。"
                        )
                    )
                    controller.keep(timeout=180, reset_timeout=True)
                    return

                summary = data.get("summary", {}) or {}
                session_state["source_url"] = text
                session_state["token"] = token
                session_state["summary"] = summary
                session_state["stage"] = "alias"
                suggestion = self._build_alias_suggestion(summary)

                await wait_event.send(
                    wait_event.plain_result(
                        "URL 拉取成功，token 校验通过。\n\n"
                        f"{self._format_summary(summary)}\n\n"
                        "第 2 步：请发送账号别名。\n"
                        f"建议别名: {suggestion}"
                    )
                )
                controller.keep(timeout=180, reset_timeout=True)
                return

            try:
                saved = self.storage.save_account_with_options(
                    user_id=user_id,
                    alias=text,
                    token=session_state["token"],
                    summary=session_state["summary"],
                    import_method="url",
                    source_url=session_state["source_url"],
                )
            except ValueError as exc:
                await wait_event.send(
                    wait_event.plain_result(
                        f"{exc}\n请重新发送新的账号别名，或输入 取消 结束。"
                    )
                )
                controller.keep(timeout=180, reset_timeout=True)
                return

            await wait_event.send(
                wait_event.plain_result(
                    self._format_bind_result(
                        account=saved["account"],
                        summary=session_state["summary"],
                        created=bool(saved["created"]),
                        is_default=bool(saved["is_default"]),
                    )
                )
            )
            controller.stop()

        try:
            await bind_waiter(event, session_filter=UserBindSessionFilter())
        except TimeoutError:
            yield event.plain_result("URL 绑定超时，已结束本次会话。")
        except Exception as exc:
            self.logger.error("[XYZW] URL 绑定失败: %s", exc)
            yield event.plain_result(f"URL 绑定失败: {exc}")
        finally:
            event.stop_event()

    async def _handle_account(self, event: AstrMessageEvent, tokens):
        user_id = self._get_user_id(event)
        if tokens.len < 3:
            yield event.plain_result(self._format_account_list(user_id))
            return

        action = tokens.tokens[2].lower()

        if action in {"查看", "list", "列表"}:
            yield event.plain_result(self._format_account_list(user_id))
            return

        if action == "默认":
            if tokens.len < 4:
                yield event.plain_result(self._account_manage_usage())
                return
            selector = " ".join(tokens.tokens[3:]).strip()
            try:
                account = self.storage.set_default_account(user_id, selector)
            except ValueError as exc:
                yield event.plain_result(str(exc))
                return
            yield event.plain_result(
                f"默认账号已切换为: {account.get('alias')}\n"
                f"id: {account.get('account_id', '')[:8]}"
            )
            return

        if action == "重命名":
            if tokens.len < 5:
                yield event.plain_result(self._account_manage_usage())
                return
            selector = tokens.tokens[3]
            new_alias = " ".join(tokens.tokens[4:]).strip()
            try:
                account = self.storage.rename_account(user_id, selector, new_alias)
            except ValueError as exc:
                yield event.plain_result(str(exc))
                return
            yield event.plain_result(
                f"账号别名已更新为: {account.get('alias')}\n"
                f"id: {account.get('account_id', '')[:8]}"
            )
            return

        if action == "删除":
            if self.binding_private_only and not event.is_private_chat():
                yield event.plain_result(self._binding_permission_text())
                return
            if tokens.len < 4:
                yield event.plain_result(self._account_manage_usage())
                return
            selector = " ".join(tokens.tokens[3:]).strip()
            try:
                account = self.storage.delete_account(user_id, selector)
            except ValueError as exc:
                yield event.plain_result(str(exc))
                return
            yield event.plain_result(
                f"已删除账号: {account.get('alias')}\n"
                f"id: {account.get('account_id', '')[:8]}"
            )
            return

        yield event.plain_result(self._account_manage_usage())

    async def _handle_status(self, event: AstrMessageEvent, tokens):
        user_id = self._get_user_id(event)
        selector = " ".join(tokens.tokens[2:]).strip() if tokens.len >= 3 else ""
        account, error_text = self._resolve_account_or_text(user_id, selector)

        if not account:
            yield event.plain_result(error_text or self._status_usage())
            return

        response = await self._call_with_account_token_ready(
            user_id,
            account,
            lambda ready_account: self.sidecar.describe_account(
                ready_account.get("token", "")
            ),
            reason="status",
        )
        if not response.get("ok"):
            yield event.plain_result(
                f"状态查询失败: {response.get('message', '未知错误')}"
            )
            return

        data = response.get("data", {})
        summary = data.get("summary", {}) or {}
        yield event.plain_result(
            "账号状态\n"
            f"- 别名: {account.get('alias')}\n"
            f"- 账号ID: {account.get('account_id', '')[:8]}\n"
            f"- token: {account.get('token_preview')}\n\n"
            f"{self._format_summary(summary)}"
        )

    async def _handle_car(self, event: AstrMessageEvent, tokens):
        user_id = self._get_user_id(event)
        action = "查看"
        selector = ""
        car_id = ""
        helper_id = ""
        helper_member_selector = ""

        if tokens.len >= 3:
            candidate = tokens.tokens[2].lower()
            if candidate in {"查看", "状态", "概览"}:
                selector = " ".join(tokens.tokens[3:]).strip()
            elif candidate in {"护卫成员", "护卫", "成员", "helpers", "helper"}:
                action = "护卫成员"
                helper_member_selector = " ".join(tokens.tokens[3:]).strip()
            elif candidate in {"发车", "发送", "send"}:
                action = "发车"
                if tokens.len < 4:
                    yield event.plain_result(self._car_usage())
                    return
                car_id = str(tokens.tokens[3] or "").strip()
                remaining_tokens = [str(token or "").strip() for token in tokens.tokens[4:]]
                if remaining_tokens:
                    keyword = remaining_tokens[0].lower()
                    if keyword in {"护卫", "helper", "guard"}:
                        if len(remaining_tokens) < 2 or not remaining_tokens[1]:
                            yield event.plain_result(
                                "发车参数不完整。\n"
                                "用法: /xyzw 车 发车 <车辆ID> [护卫 <护卫ID>] [别名或ID前缀]"
                            )
                            return
                        helper_id = remaining_tokens[1]
                        selector = " ".join(remaining_tokens[2:]).strip()
                    else:
                        selector = " ".join(remaining_tokens).strip()
            elif candidate in {"收车", "领取"}:
                action = "收车"
                selector = " ".join(tokens.tokens[3:]).strip()
            elif candidate in {"help", "帮助"}:
                yield event.plain_result(self._car_usage())
                return
            else:
                selector = " ".join(tokens.tokens[2:]).strip()

        if action == "护卫成员":
            helper_account, helper_member_selector, error_text = (
                self._resolve_car_helper_query(
                    user_id,
                    tokens.tokens[3:] if tokens.len >= 4 else [],
                )
            )
            if not helper_account:
                yield event.plain_result(error_text or self._car_usage())
                return

            member_ids = self._build_car_helper_member_ids(helper_member_selector)
            response = await self._call_with_account_token_ready(
                user_id,
                helper_account,
                lambda ready_account: self.sidecar.get_car_helpers(
                    ready_account.get("token", ""),
                    member_ids=member_ids,
                    keyword=None if member_ids else helper_member_selector or None,
                    timeout_ms=self.car_helper_query_timeout_ms,
                ),
                reason="car_helpers",
            )
            if not response.get("ok"):
                yield event.plain_result(
                    f"护卫成员查询失败: {response.get('message', '未知错误')}"
                )
                return

            data = response.get("data", {}) or {}
            yield event.plain_result(
                self._format_car_helper_members_text(
                    helper_account,
                    data,
                    helper_member_selector,
                )
            )
            return

        account, error_text = self._resolve_account_or_text(user_id, selector)
        if not account:
            yield event.plain_result(error_text or self._car_usage())
            return

        if action == "发车":
            if not car_id:
                yield event.plain_result(self._car_usage())
                return
            car_send_block_reason = self._get_car_send_block_reason()
            if car_send_block_reason:
                yield event.plain_result(
                    "当前不可发车。\n"
                    f"{car_send_block_reason}"
                )
                return
            if helper_id and not helper_id.isdigit():
                yield event.plain_result(
                    f"护卫ID 格式错误: {helper_id}\n"
                    "请使用数字 ID，例如: /xyzw 车 发车 <车辆ID> 护卫 123456"
                )
                return

            overview_response = await self._call_with_account_token_ready(
                user_id,
                account,
                lambda ready_account: self.sidecar.get_car_overview(
                    ready_account.get("token", "")
                ),
                reason="car_send_precheck",
            )
            if not overview_response.get("ok"):
                yield event.plain_result(
                    f"发车前检查失败: {overview_response.get('message', '未知错误')}"
                )
                return

            overview_data = overview_response.get("data", {}) or {}
            overview = overview_data.get("overview", {}) or {}
            target_car = self._find_car_in_overview(overview, car_id)
            if not target_car:
                yield event.plain_result(
                    f"发车失败: 未找到车辆 {car_id}\n"
                    "请先使用 /xyzw 车 查看当前可发车车辆列表。"
                )
                return

            if str(target_car.get("status") or "").strip() != "idle":
                yield event.plain_result(
                    f"发车失败: 车辆 {car_id} 当前不可发车\n"
                    f"- 当前状态: {target_car.get('status') or '未知'}"
                )
                return

            if self._car_requires_helper(target_car) and not helper_id:
                yield event.plain_result(
                    "当前车辆品级较高，发车必须提供护卫ID。\n"
                    f"- 车辆ID: {car_id}\n"
                    f"- 品质: {target_car.get('gradeLabel') or '未知'}\n"
                    "用法: /xyzw 车 发车 <车辆ID> 护卫 <护卫ID> [别名或ID前缀]"
                )
                return

            response = await self._call_with_account_token_ready(
                user_id,
                account,
                lambda ready_account: self.sidecar.send_car(
                    ready_account.get("token", ""),
                    car_id=car_id,
                    helper_id=helper_id or None,
                ),
                reason="car_send",
            )
            if not response.get("ok"):
                yield event.plain_result(
                    f"发车失败: {response.get('message', '未知错误')}"
                )
                return

            data = response.get("data", {}) or {}
            yield event.plain_result(self._format_car_send_result(account, data))
            return

        if action == "收车":
            response = await self._call_with_account_token_ready(
                user_id,
                account,
                lambda ready_account: self.sidecar.claim_ready_cars(
                    ready_account.get("token", "")
                ),
                reason="car_claim",
            )
            if not response.get("ok"):
                yield event.plain_result(
                    f"收车失败: {response.get('message', '未知错误')}"
                )
                return

            data = response.get("data", {})
            if not data.get("claimedCount") and not data.get("failures"):
                yield event.plain_result(
                    f"当前没有可收取车辆。\n账号: {account.get('alias')}"
                )
                return

            yield event.plain_result(self._format_car_claim_result(account, data))
            return

        response = await self._call_with_account_token_ready(
            user_id,
            account,
            lambda ready_account: self.sidecar.get_car_overview(
                ready_account.get("token", "")
            ),
            reason="car_overview",
        )
        if not response.get("ok"):
            yield event.plain_result(
                f"车辆查询失败: {response.get('message', '未知错误')}"
            )
            return

        data = response.get("data", {})
        overview = data.get("overview", {}) or {}
        yield event.plain_result(self._format_car_overview_text(account, overview))

    async def _handle_daily(self, event: AstrMessageEvent, tokens):
        user_id = self._get_user_id(event)
        if tokens.len >= 3 and tokens.tokens[2].lower() in {"配置", "config", "settings"}:
            async for result in self._handle_daily_config(event, tokens):
                yield result
            return

        if tokens.len >= 3 and tokens.tokens[2].lower() in {"help", "帮助"}:
            yield event.plain_result(self._daily_usage())
            return

        selector = " ".join(tokens.tokens[2:]).strip() if tokens.len >= 3 else ""
        account, error_text = self._resolve_account_or_text(user_id, selector)
        if not account:
            yield event.plain_result(error_text or self._daily_usage())
            return
        account_id = str(account.get("account_id") or "")
        if self._is_daily_task_busy(account_id):
            yield event.plain_result(
                self._manual_daily_busy_message(str(account.get("alias") or ""))
            )
            return

        self._pending_daily_task_account_ids.add(account_id)
        task = asyncio.create_task(
            self._run_manual_daily_task_background(
                account=account,
                session=event.unified_msg_origin,
                fallback_group_id=event.get_group_id(),
                fallback_user_id=user_id,
                sender_uin=event.get_self_id(),
            ),
            name=f"xyzw_manual_daily:{account_id}",
        )
        self._track_background_task(task, f"xyzw_manual_daily:{account_id}")
        yield event.plain_result(
            self._manual_daily_accepted_message(str(account.get("alias") or ""))
        )

    async def _handle_resource(self, event: AstrMessageEvent, tokens):
        user_id = self._get_user_id(event)
        if tokens.len < 3:
            yield event.plain_result(self._resource_usage())
            return

        if tokens.tokens[2].lower() in {"help", "帮助"}:
            yield event.plain_result(self._resource_usage())
            return

        spec, error_text = self._parse_resource_command_spec(tokens.tokens[2:])
        if not spec:
            yield event.plain_result(error_text or self._resource_usage())
            return

        account, error_text = self._resolve_account_or_text(
            user_id,
            str(spec.get("selector") or ""),
        )
        if not account:
            yield event.plain_result(error_text or self._resource_usage())
            return

        execution = await self._run_resource_action_request(
            user_id=user_id,
            account=account,
            action=str(spec.get("action") or ""),
            options=spec.get("options"),
            timeout_ms=spec.get("timeout_ms"),
        )
        if not execution.get("ok"):
            data = execution.get("data", {}) or {}
            action_label = (
                data.get("label")
                or data.get("action")
                or spec.get("label")
                or "资源命令"
            )
            error_message = execution.get("error_message") or "未知错误"
            if data:
                yield event.plain_result(f"{action_label}执行失败: {error_message}")
            else:
                yield event.plain_result(f"资源执行失败: {error_message}")
            return

        yield event.plain_result(
            self._format_resource_result_text(account, execution.get("data", {}) or {})
        )

    async def _handle_dungeon(self, event: AstrMessageEvent, tokens):
        user_id = self._get_user_id(event)
        if tokens.len < 3:
            yield event.plain_result(self._dungeon_usage())
            return

        if tokens.tokens[2].lower() in {"help", "帮助"}:
            yield event.plain_result(self._dungeon_usage())
            return

        spec, error_text = self._parse_dungeon_command_spec(tokens.tokens[2:])
        if not spec:
            yield event.plain_result(error_text or self._dungeon_usage())
            return

        account, error_text = self._resolve_account_or_text(
            user_id,
            str(spec.get("selector") or ""),
        )
        if not account:
            yield event.plain_result(error_text or self._dungeon_usage())
            return

        execution = await self._run_dungeon_action_request(
            user_id=user_id,
            account=account,
            action=str(spec.get("action") or ""),
            options=spec.get("options"),
            timeout_ms=spec.get("timeout_ms"),
        )
        if not execution.get("ok"):
            data = execution.get("data", {}) or {}
            action_label = (
                data.get("label")
                or data.get("action")
                or spec.get("label")
                or "副本命令"
            )
            error_message = execution.get("error_message") or "未知错误"
            if data:
                yield event.plain_result(f"{action_label}执行失败: {error_message}")
            else:
                yield event.plain_result(f"副本执行失败: {error_message}")
            return

        yield event.plain_result(
            self._format_dungeon_result_text(account, execution.get("data", {}) or {})
        )

    async def _handle_schedule(self, event: AstrMessageEvent, tokens):
        user_id = self._get_user_id(event)
        if tokens.len < 3:
            yield event.plain_result(self._schedule_usage())
            return

        action = tokens.tokens[2].lower()
        if action in {"help", "帮助"}:
            yield event.plain_result(self._schedule_usage())
            return

        if action in {"查看", "list"}:
            yield event.plain_result(self._format_schedule_state(user_id))
            return

        if action not in {
            "收车",
            "car",
            "挂机",
            "hangup",
            "护卫成员",
            "护卫",
            "helper",
            "helpers",
            "日常",
            "daily",
            "资源",
            "resource",
            "副本",
            "dungeon",
            "活动",
            "activity",
        }:
            yield event.plain_result(self._schedule_usage())
            return

        if tokens.len < 4:
            yield event.plain_result(self._schedule_usage())
            return

        subaction = tokens.tokens[3].lower()
        remaining_tokens = tokens.tokens[4:]

        if action in {"挂机", "hangup"}:
            if subaction in {"开启", "启用", "on"}:
                if not self.storage.get_notify_group(user_id):
                    yield event.plain_result(
                        "请先绑定通知群，再开启挂机提醒。\n"
                        "使用 /xyzw 通知 绑定本群"
                    )
                    return

                interval_minutes = self.hangup_reminder_default_interval_minutes
                if remaining_tokens and remaining_tokens[0].isdigit():
                    interval_minutes = max(5, min(int(remaining_tokens[0]), 720))
                    remaining_tokens = remaining_tokens[1:]

                selector = " ".join(remaining_tokens).strip()
                account, error_text = self._resolve_account_or_text(user_id, selector)
                if not account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                self.storage.upsert_hangup_reminder(
                    user_id=user_id,
                    account_id=str(account.get("account_id") or ""),
                    interval_minutes=interval_minutes,
                    enabled=True,
                )
                yield event.plain_result(
                    "已开启挂机提醒。\n"
                    f"- 别名: {account.get('alias')}\n"
                    f"- 间隔: {interval_minutes} 分钟\n"
                    "- 通知渠道: 群广播\n"
                    f"- 说明: 后台会按 {self.scheduler_poll_interval_seconds} 秒轮询并在到期时检查。"
                )
                return

            if subaction in {"关闭", "禁用", "off"}:
                selector = " ".join(remaining_tokens).strip()
                account, error_text = self._resolve_account_or_text(user_id, selector)
                if not account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                try:
                    self.storage.disable_hangup_reminder(
                        user_id=user_id,
                        account_id=str(account.get("account_id") or ""),
                    )
                except ValueError as exc:
                    yield event.plain_result(str(exc))
                    return

                yield event.plain_result(
                    "已关闭挂机提醒。\n"
                    f"- 别名: {account.get('alias')}"
                )
                return

            if subaction in {"检查", "巡检", "check", "run"}:
                selector = " ".join(remaining_tokens).strip()
                account, error_text = self._resolve_account_or_text(user_id, selector)
                if not account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                reminder = self.storage.get_hangup_reminder(
                    user_id=user_id,
                    account_id=str(account.get("account_id") or ""),
                )
                if not reminder or not reminder.get("enabled"):
                    yield event.plain_result(
                        "当前账号还未开启挂机提醒，请先开启。\n"
                        "/xyzw 定时 挂机 开启 [间隔分钟] [别名或ID前缀]"
                    )
                    return

                result = await self._check_hangup_reminder_once(
                    user_id=user_id,
                    account=account,
                    reminder=reminder,
                    allow_notify=True,
                )
                yield event.plain_result(
                    self._format_hangup_reminder_check_result(account, result)
                )
                return

            yield event.plain_result(self._schedule_usage())
            return

        if action in {"护卫成员", "护卫", "helper", "helpers"}:
            if subaction in {"查看", "list"}:
                yield event.plain_result(
                    self._format_helper_member_schedule_state(user_id)
                )
                return

            if subaction in {"开启", "启用", "on"}:
                if not self.storage.get_notify_group(user_id):
                    yield event.plain_result(
                        "请先绑定通知群，再开启护卫成员提醒。\n"
                        "使用 /xyzw 通知 绑定本群"
                    )
                    return

                interval_minutes = self.helper_member_reminder_default_interval_minutes
                if remaining_tokens and remaining_tokens[0].isdigit():
                    interval_minutes = max(5, min(int(remaining_tokens[0]), 720))
                    remaining_tokens = remaining_tokens[1:]

                helper_account, helper_member_selector, error_text = (
                    self._resolve_car_helper_query(user_id, remaining_tokens)
                )
                if not helper_account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                member_ids = self._build_car_helper_member_ids(helper_member_selector)
                if not member_ids:
                    yield event.plain_result(
                        "请提供成员 ID 列表，使用英文逗号分隔。\n"
                        "用法: /xyzw 定时 护卫成员 开启 [间隔分钟] <成员ID1,成员ID2,...> [别名或ID前缀]"
                    )
                    return

                self.storage.upsert_helper_member_reminder(
                    user_id=user_id,
                    account_id=str(helper_account.get("account_id") or ""),
                    interval_minutes=interval_minutes,
                    member_ids=member_ids,
                    enabled=True,
                )
                yield event.plain_result(
                    "已开启护卫成员提醒。\n"
                    f"- 别名: {helper_account.get('alias')}\n"
                    f"- 间隔: {interval_minutes} 分钟\n"
                    f"- 成员ID: {','.join(member_ids)}\n"
                    f"- 时段: {self._car_send_window_text()}\n"
                    "- 通知渠道: 群广播"
                )
                return

            if subaction in {"关闭", "禁用", "off"}:
                selector = " ".join(remaining_tokens).strip()
                account, error_text = self._resolve_account_or_text(user_id, selector)
                if not account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                try:
                    self.storage.disable_helper_member_reminder(
                        user_id=user_id,
                        account_id=str(account.get("account_id") or ""),
                    )
                except ValueError as exc:
                    yield event.plain_result(str(exc))
                    return

                yield event.plain_result(
                    "已关闭护卫成员提醒。\n"
                    f"- 别名: {account.get('alias')}"
                )
                return

            if subaction in {"检查", "巡检", "check", "run"}:
                selector = " ".join(remaining_tokens).strip()
                account, error_text = self._resolve_account_or_text(user_id, selector)
                if not account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                reminder = self.storage.get_helper_member_reminder(
                    user_id=user_id,
                    account_id=str(account.get("account_id") or ""),
                )
                if not reminder or not reminder.get("enabled"):
                    yield event.plain_result(
                        "当前账号还未开启护卫成员提醒，请先开启。\n"
                        "/xyzw 定时 护卫成员 开启 [间隔分钟] <成员ID1,成员ID2,...> [别名或ID前缀]"
                    )
                    return

                result = await self._check_helper_member_reminder_once(
                    user_id=user_id,
                    account=account,
                    reminder=reminder,
                    allow_notify=True,
                )
                yield event.plain_result(
                    self._format_helper_member_reminder_check_result(account, result)
                )
                return

            yield event.plain_result(self._schedule_usage())
            return

        if action in {"活动", "activity"}:
            if subaction in {"查看", "list"}:
                yield event.plain_result(self._format_activity_schedule_state(user_id))
                return

            if subaction in {"开启", "启用", "on"}:
                if not self.storage.get_notify_group(user_id):
                    yield event.plain_result(
                        "请先绑定通知群，再开启活动开放提醒。\n"
                        "使用 /xyzw 通知 绑定本群"
                    )
                    return

                if not remaining_tokens:
                    yield event.plain_result(
                        "请提供活动名称。\n"
                        "/xyzw 定时 活动 开启 <梦境|宝库> [HH:MM]"
                    )
                    return

                activity_key = _normalize_activity_key(remaining_tokens[0])
                if activity_key not in {"dream", "bosstower"}:
                    yield event.plain_result(
                        "当前仅支持 梦境 / 宝库 两类活动提醒。"
                    )
                    return

                notify_time = "00:00"
                if len(remaining_tokens) >= 2:
                    notify_time = remaining_tokens[1]
                    if self._parse_schedule_time(notify_time) is None:
                        yield event.plain_result(
                            "时间格式无效，请使用 HH:MM，例如 09:00。"
                        )
                        return
                if len(remaining_tokens) > 2:
                    yield event.plain_result(self._schedule_usage())
                    return

                self.storage.upsert_activity_reminder(
                    user_id=user_id,
                    activity_key=activity_key,
                    notify_time=notify_time,
                    enabled=True,
                )
                yield event.plain_result(
                    "已开启活动开放提醒。\n"
                    f"- 活动: {self._activity_label(activity_key)}\n"
                    f"- 时间: {notify_time}\n"
                    "- 通知渠道: 群广播\n"
                    "- 说明: 到达提醒时间且活动当天开放时，后台会自动推送一次。"
                )
                return

            if subaction in {"关闭", "禁用", "off"}:
                if not remaining_tokens:
                    yield event.plain_result(
                        "请提供活动名称。\n"
                        "/xyzw 定时 活动 关闭 <梦境|宝库>"
                    )
                    return

                activity_key = _normalize_activity_key(remaining_tokens[0])
                if activity_key not in {"dream", "bosstower"}:
                    yield event.plain_result(
                        "当前仅支持 梦境 / 宝库 两类活动提醒。"
                    )
                    return

                try:
                    self.storage.disable_activity_reminder(
                        user_id=user_id,
                        activity_key=activity_key,
                    )
                except ValueError as exc:
                    yield event.plain_result(str(exc))
                    return

                yield event.plain_result(
                    "已关闭活动开放提醒。\n"
                    f"- 活动: {self._activity_label(activity_key)}"
                )
                return

            yield event.plain_result(self._schedule_usage())
            return

        if action in {"日常", "daily"}:
            if subaction in {"开启", "启用", "on"}:
                if not self.storage.get_notify_group(user_id):
                    yield event.plain_result(
                        "请先绑定通知群，再开启定时日常。\n"
                        "使用 /xyzw 通知 绑定本群"
                    )
                    return

                if not remaining_tokens:
                    yield event.plain_result(
                        "请提供执行时间，格式为 HH:MM。\n"
                        "/xyzw 定时 日常 开启 <HH:MM> [别名或ID前缀]"
                    )
                    return

                schedule_time = remaining_tokens[0]
                if self._parse_schedule_time(schedule_time) is None:
                    yield event.plain_result(
                        "时间格式无效，请使用 HH:MM，例如 06:30。"
                    )
                    return

                selector = " ".join(remaining_tokens[1:]).strip()
                account, error_text = self._resolve_account_or_text(user_id, selector)
                if not account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                self.storage.upsert_daily_task(
                    user_id=user_id,
                    account_id=str(account.get("account_id") or ""),
                    schedule_time=schedule_time,
                    enabled=True,
                )
                yield event.plain_result(
                    "已开启定时日常。\n"
                    f"- 别名: {account.get('alias')}\n"
                    f"- 时间: {schedule_time}\n"
                    "- 通知渠道: 群广播\n"
                    f"- 执行超时: {self.daily_task_timeout_ms} ms"
                )
                return

            if subaction in {"关闭", "禁用", "off"}:
                selector = " ".join(remaining_tokens).strip()
                account, error_text = self._resolve_account_or_text(user_id, selector)
                if not account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                try:
                    self.storage.disable_daily_task(
                        user_id=user_id,
                        account_id=str(account.get("account_id") or ""),
                    )
                except ValueError as exc:
                    yield event.plain_result(str(exc))
                    return

                yield event.plain_result(
                    "已关闭定时日常。\n"
                    f"- 别名: {account.get('alias')}"
                )
                return

            if subaction in {"执行", "run", "test"}:
                selector = " ".join(remaining_tokens).strip()
                account, error_text = self._resolve_account_or_text(user_id, selector)
                if not account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                schedule = self.storage.get_daily_task(
                    user_id=user_id,
                    account_id=str(account.get("account_id") or ""),
                )
                if not schedule or not schedule.get("enabled"):
                    yield event.plain_result(
                        "当前账号还未开启定时日常，请先开启。\n"
                        "/xyzw 定时 日常 开启 <HH:MM> [别名或ID前缀]"
                    )
                    return

                account_id = str(account.get("account_id") or "")
                if self._is_daily_task_busy(account_id):
                    yield event.plain_result(
                        self._manual_daily_busy_message(
                            str(account.get("alias") or "")
                        )
                    )
                    return

                self._pending_daily_task_account_ids.add(account_id)
                task = asyncio.create_task(
                    self._run_manual_scheduled_daily_background(
                        user_id=user_id,
                        account=account,
                        schedule=schedule,
                        session=event.unified_msg_origin,
                        fallback_group_id=event.get_group_id(),
                        fallback_user_id=user_id,
                        sender_uin=event.get_self_id(),
                    ),
                    name=f"xyzw_manual_scheduled_daily:{account_id}",
                )
                self._track_background_task(
                    task,
                    f"xyzw_manual_scheduled_daily:{account_id}",
                )
                yield event.plain_result(
                    self._manual_daily_accepted_message(
                        str(account.get("alias") or "")
                    )
                )
                return

            yield event.plain_result(self._schedule_usage())
            return

        if action in {"资源", "resource", "副本", "dungeon"}:
            category = "resource" if action in {"资源", "resource"} else "dungeon"
            category_label = self._scheduled_action_category_label(category)
            parser = (
                self._parse_resource_command_spec
                if category == "resource"
                else self._parse_dungeon_command_spec
            )

            if subaction in {"查看", "list"}:
                yield event.plain_result(
                    self._format_action_task_schedule_state(user_id, category)
                )
                return

            if subaction in {"开启", "启用", "on"}:
                if not self.storage.get_notify_group(user_id):
                    yield event.plain_result(
                        f"请先绑定通知群，再开启定时{category_label}执行。\n"
                        "使用 /xyzw 通知 绑定本群"
                    )
                    return

                if not remaining_tokens:
                    yield event.plain_result(self._schedule_usage())
                    return

                schedule_time = remaining_tokens[0]
                if self._parse_schedule_time(schedule_time) is None:
                    yield event.plain_result(
                        "时间格式无效，请使用 HH:MM，例如 06:30。"
                    )
                    return

                spec, error_text = parser(remaining_tokens[1:])
                if not spec:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                account, error_text = self._resolve_account_or_text(
                    user_id,
                    str(spec.get("selector") or ""),
                )
                if not account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                self.storage.upsert_action_task(
                    user_id=user_id,
                    account_id=str(account.get("account_id") or ""),
                    category=category,
                    action=str(spec.get("action") or ""),
                    label=str(spec.get("label") or ""),
                    schedule_time=schedule_time,
                    options=spec.get("options"),
                    timeout_ms=spec.get("timeout_ms"),
                    enabled=True,
                )
                yield event.plain_result(
                    f"已开启定时{category_label}执行。\n"
                    f"- 账号: {account.get('alias')}\n"
                    f"- 任务: {spec.get('label') or '-'}\n"
                    f"- 时间: {schedule_time}\n"
                    "- 通知渠道: 群广播"
                )
                return

            if subaction in {"关闭", "禁用", "off"}:
                spec, error_text = parser(remaining_tokens)
                if not spec:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                account, error_text = self._resolve_account_or_text(
                    user_id,
                    str(spec.get("selector") or ""),
                )
                if not account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                try:
                    self.storage.disable_action_task(
                        user_id=user_id,
                        account_id=str(account.get("account_id") or ""),
                        category=category,
                        action=str(spec.get("action") or ""),
                        options=spec.get("options"),
                    )
                except ValueError as exc:
                    yield event.plain_result(str(exc))
                    return

                yield event.plain_result(
                    f"已关闭定时{category_label}执行。\n"
                    f"- 账号: {account.get('alias')}\n"
                    f"- 任务: {spec.get('label') or '-'}"
                )
                return

            if subaction in {"执行", "run", "test"}:
                spec, error_text = parser(remaining_tokens)
                if not spec:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                account, error_text = self._resolve_account_or_text(
                    user_id,
                    str(spec.get("selector") or ""),
                )
                if not account:
                    yield event.plain_result(error_text or self._schedule_usage())
                    return

                schedule = self.storage.get_action_task(
                    user_id=user_id,
                    account_id=str(account.get("account_id") or ""),
                    category=category,
                    action=str(spec.get("action") or ""),
                    options=spec.get("options"),
                )
                if not schedule or not schedule.get("enabled"):
                    yield event.plain_result(
                        f"当前任务还未开启定时{category_label}执行，请先开启。\n"
                        f"/xyzw 定时 {category_label} 开启 <HH:MM> <命令参数...>"
                    )
                    return

                result = await self._run_action_task_once(
                    user_id=user_id,
                    account=account,
                    schedule=schedule,
                    allow_notify=False,
                    force_run=True,
                )
                if result.get("result_text"):
                    yield event.plain_result(str(result.get("result_text")))
                    return

                yield event.plain_result(
                    self._format_action_task_run_result(account, schedule, result)
                )
                return

            yield event.plain_result(self._schedule_usage())
            return

        if subaction in {"开启", "启用", "on"}:
            if not self.storage.get_notify_group(user_id):
                yield event.plain_result(
                    "请先绑定通知群，再开启收车提醒。\n"
                    "使用 /xyzw 通知 绑定本群"
                )
                return

            interval_minutes = self.car_reminder_default_interval_minutes
            if remaining_tokens and remaining_tokens[0].isdigit():
                interval_minutes = max(5, min(int(remaining_tokens[0]), 720))
                remaining_tokens = remaining_tokens[1:]

            selector = " ".join(remaining_tokens).strip()
            account, error_text = self._resolve_account_or_text(user_id, selector)
            if not account:
                yield event.plain_result(error_text or self._schedule_usage())
                return

            self.storage.upsert_car_reminder(
                user_id=user_id,
                account_id=str(account.get("account_id") or ""),
                interval_minutes=interval_minutes,
                enabled=True,
            )
            yield event.plain_result(
                "已开启收车提醒。\n"
                f"- 别名: {account.get('alias')}\n"
                f"- 间隔: {interval_minutes} 分钟\n"
                "- 提醒渠道: 群广播\n"
                f"- 说明: 后台会按 {self.scheduler_poll_interval_seconds} 秒轮询并在到期时检查。"
            )
            return

        if subaction in {"关闭", "禁用", "off"}:
            selector = " ".join(remaining_tokens).strip()
            account, error_text = self._resolve_account_or_text(user_id, selector)
            if not account:
                yield event.plain_result(error_text or self._schedule_usage())
                return

            try:
                self.storage.disable_car_reminder(
                    user_id=user_id,
                    account_id=str(account.get("account_id") or ""),
                )
            except ValueError as exc:
                yield event.plain_result(str(exc))
                return

            yield event.plain_result(
                "已关闭收车提醒。\n"
                f"- 别名: {account.get('alias')}"
            )
            return

        if subaction in {"检查", "巡检", "check", "run"}:
            selector = " ".join(remaining_tokens).strip()
            account, error_text = self._resolve_account_or_text(user_id, selector)
            if not account:
                yield event.plain_result(error_text or self._schedule_usage())
                return

            reminder = self.storage.get_car_reminder(
                user_id=user_id,
                account_id=str(account.get("account_id") or ""),
            )
            if not reminder or not reminder.get("enabled"):
                yield event.plain_result(
                    "当前账号还未开启收车提醒，请先开启。\n"
                    "/xyzw 定时 收车 开启 [间隔分钟] [别名或ID前缀]"
                )
                return

            result = await self._check_car_reminder_once(
                user_id=user_id,
                account=account,
                reminder=reminder,
                allow_notify=True,
            )
            yield event.plain_result(
                self._format_car_reminder_check_result(account, result)
            )
            return

        yield event.plain_result(self._schedule_usage())

    async def _handle_notify(self, event: AstrMessageEvent, tokens):
        user_id = self._get_user_id(event)
        if tokens.len < 3:
            yield event.plain_result(
                "通知子命令:\n"
                "/xyzw 通知 绑定本群\n"
                "/xyzw 通知 查看\n"
                "/xyzw 通知 解绑\n"
                "/xyzw 通知 测试"
            )
            return

        action = tokens.tokens[2].lower()

        if action == "绑定本群":
            group_id = str(event.get_group_id() or "")
            if not group_id:
                yield event.plain_result("请在目标群里执行该命令。")
                return
            self.storage.bind_notify_group(
                user_id=user_id,
                group_id=group_id,
                unified_msg_origin=event.unified_msg_origin,
            )
            yield event.plain_result(
                "已将当前群绑定为默认通知群。\n"
                f"group_id: {group_id}\n"
                "后续通知会优先按当前群会话发送普通群消息。"
            )
            return

        if action == "查看":
            yield event.plain_result(self._format_notify_state(user_id))
            return

        if action == "解绑":
            self.storage.clear_notify_group(user_id)
            yield event.plain_result("已清除默认通知群绑定。")
            return

        if action == "测试":
            result = await self.notifier.push_group_message(
                user_id=user_id,
                text="这是一条 XYZW 插件发出的群广播测试消息。",
            )
            if result.success:
                yield event.plain_result(f"测试通知已发送，channel={result.channel}")
            else:
                yield event.plain_result(f"测试通知发送失败: {result.detail}")
            return

        yield event.plain_result(f"未知通知子命令: {action}")
