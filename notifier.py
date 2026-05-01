"""通知路由。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Node, Nodes, Plain
from astrbot.api.star import StarTools

from .storage import XyzwStorage


@dataclass(slots=True)
class NotifyResult:
    success: bool
    channel: str
    detail: str = ""


class NotificationRouter:
    """负责通知发送策略。

    当前策略：
    - 优先按群会话发送消息
    - 会话不可用时回退到按群号发送
    - 可选降级到私聊
    - 长文本自动转 QQ 合并转发
    """

    def __init__(
        self,
        storage: XyzwStorage,
        allow_private_fallback: bool = True,
        forward_sender_uin_resolver: Callable[[], str | None] | None = None,
    ):
        self.storage = storage
        self.allow_private_fallback = allow_private_fallback
        self.forward_sender_uin_resolver = forward_sender_uin_resolver
        self.forward_threshold = 280
        self.forward_line_threshold = 8
        self.forward_single_node = True
        self.forward_chunk_char_limit = 600
        self.forward_chunk_line_limit = 18
        self.forward_sender_name = "XYZW 插件"

    def preview_target(self, user_id: str) -> str:
        target = self.storage.get_notify_group(user_id)
        if not target:
            return "未绑定通知群"
        session = str(target.get("unified_msg_origin") or "").strip()
        if session:
            return f"群广播优先，session={session}"
        return f"群广播，group_id={target['group_id']}"

    def _should_use_forward(self, text: str) -> bool:
        normalized = str(text or "")
        return (
            len(normalized) >= self.forward_threshold
            or normalized.count("\n") >= self.forward_line_threshold
        )

    def _resolve_forward_sender_uin(self, sender_uin: str | None = None) -> str:
        value = str(sender_uin or "").strip()
        if value:
            return value
        if callable(self.forward_sender_uin_resolver):
            try:
                resolved = str(self.forward_sender_uin_resolver() or "").strip()
                if resolved:
                    return resolved
            except Exception as exc:
                logger.warning("解析转发消息发送者失败: %s", exc)
        return "10000"

    def _split_forward_chunks(self, text: str) -> list[str]:
        normalized = str(text or "")
        lines = normalized.splitlines() or [normalized]
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for line in lines:
            addition = len(line) + (1 if current else 0)
            if current and (
                len(current) >= self.forward_chunk_line_limit
                or current_len + addition > self.forward_chunk_char_limit
            ):
                chunks.append("\n".join(current).strip("\n"))
                current = [line]
                current_len = len(line)
                continue

            current.append(line)
            current_len += addition

        if current:
            chunks.append("\n".join(current).strip("\n"))

        normalized_chunks = [chunk for chunk in chunks if chunk]
        return normalized_chunks or [normalized]

    def build_message_chain(
        self,
        text: str,
        sender_uin: str | None = None,
        sender_name: str | None = None,
    ) -> MessageChain:
        normalized = str(text or "").strip()
        if not self._should_use_forward(normalized):
            return MessageChain().message(normalized)

        node_name = str(sender_name or self.forward_sender_name).strip() or "XYZW 插件"
        node_uin = self._resolve_forward_sender_uin(sender_uin)
        chunks = [normalized] if self.forward_single_node else self._split_forward_chunks(normalized)
        nodes = [
            Node(
                uin=node_uin,
                name=node_name,
                content=[Plain(chunk)],
            )
            for chunk in chunks
        ]
        return MessageChain([Nodes(nodes)])

    async def push_group_message(self, user_id: str, text: str) -> NotifyResult:
        target = self.storage.get_notify_group(user_id)
        if not target:
            return NotifyResult(False, "none", "未绑定通知群")

        chain = self.build_message_chain(text)
        session = str(target.get("unified_msg_origin") or "").strip()
        group_id = str(target.get("group_id") or "").strip()

        if session:
            try:
                sent = await StarTools.send_message(
                    session=session,
                    message_chain=chain,
                )
                if not sent:
                    raise RuntimeError("未找到匹配的平台会话")
                return NotifyResult(True, "group_session", f"session={session}")
            except Exception as exc:
                logger.error("按群会话发送通知失败: %s", exc)
                if not group_id and not self.allow_private_fallback:
                    return NotifyResult(False, "group_session", str(exc))

        if group_id:
            try:
                await StarTools.send_message_by_id(
                    type="GroupMessage",
                    id=group_id,
                    message_chain=chain,
                )
                return NotifyResult(True, "group_id", f"group_id={group_id}")
            except Exception as exc:
                logger.error("按群号发送通知失败: %s", exc)
                if not self.allow_private_fallback:
                    return NotifyResult(False, "group_id", str(exc))
                return await self.push_private(user_id, f"[群通知失败降级]\n{text}")

        return NotifyResult(False, "none", "未找到可用的群会话或群号")

    async def push_group_mention(self, user_id: str, text: str) -> NotifyResult:
        return await self.push_group_message(user_id=user_id, text=text)

    async def push_private(self, user_id: str, text: str) -> NotifyResult:
        try:
            await StarTools.send_message_by_id(
                type="PrivateMessage",
                id=user_id,
                message_chain=self.build_message_chain(text),
            )
            return NotifyResult(True, "private", f"user_id={user_id}")
        except Exception as exc:
            logger.error("私聊通知发送失败: %s", exc)
            return NotifyResult(False, "private", str(exc))
