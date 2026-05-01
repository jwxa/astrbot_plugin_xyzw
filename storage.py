"""插件本地存储。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_alias(alias: str) -> str:
    return " ".join((alias or "").strip().split())


def _normalize_activity_key(activity_key: str) -> str:
    normalized = _normalize_alias(activity_key).lower()
    mapping = {
        "梦境": "dream",
        "梦": "dream",
        "dream": "dream",
        "mengjing": "dream",
        "dungeon": "dream",
        "宝库": "bosstower",
        "宝": "bosstower",
        "baoku": "bosstower",
        "bosstower": "bosstower",
        "boss_tower": "bosstower",
        "boss-tower": "bosstower",
    }
    return mapping.get(normalized, normalized)


def _normalize_action_category(category: str) -> str:
    normalized = _normalize_alias(category).lower()
    mapping = {
        "资源": "resource",
        "resource": "resource",
        "副本": "dungeon",
        "dungeon": "dungeon",
    }
    return mapping.get(normalized, normalized)


def _normalize_action_options(options: dict[str, Any] | None) -> dict[str, Any]:
    payload = options or {}
    try:
        normalized = json.loads(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        )
    except Exception:
        normalized = {}
    return normalized if isinstance(normalized, dict) else {}


def _normalize_member_ids(member_ids: list[str] | tuple[str, ...] | set[str] | str | None) -> list[str]:
    if member_ids is None:
        return []
    if isinstance(member_ids, str):
        items = [member_ids]
    else:
        items = list(member_ids)

    normalized_items: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = str(item or "").strip()
        if not normalized or not normalized.isdigit() or normalized in seen:
            continue
        seen.add(normalized)
        normalized_items.append(normalized)
    return normalized_items


def _build_action_job_id(
    account_id: str,
    category: str,
    action: str,
    options: dict[str, Any] | None,
) -> str:
    normalized_options = _normalize_action_options(options)
    source = json.dumps(
        {
            "account_id": str(account_id or "").strip(),
            "category": _normalize_action_category(category),
            "action": str(action or "").strip(),
            "options": normalized_options,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def _build_account_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _mask_token(token: str) -> str:
    token = (token or "").strip()
    if len(token) <= 16:
        return f"{token[:4]}***{token[-4:]}" if token else "***"
    return f"{token[:12]}...{token[-8:]}"


def _coerce_daily_setting_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = default
    return max(minimum, min(maximum, numeric))


def _default_daily_settings() -> dict[str, int]:
    return {
        "recruit_count": 1,
        "hangup_claim_count": 1,
        "blackmarket_purchase_count": 1,
        "arena_battle_count": 1,
    }


def _normalize_daily_settings(settings: Any) -> dict[str, int]:
    payload = settings if isinstance(settings, dict) else {}
    defaults = _default_daily_settings()
    return {
        "recruit_count": _coerce_daily_setting_int(
            payload.get("recruit_count", payload.get("recruitCount")),
            default=defaults["recruit_count"],
            minimum=0,
            maximum=20,
        ),
        "hangup_claim_count": _coerce_daily_setting_int(
            payload.get("hangup_claim_count", payload.get("hangUpClaimCount")),
            default=defaults["hangup_claim_count"],
            minimum=0,
            maximum=5,
        ),
        "blackmarket_purchase_count": _coerce_daily_setting_int(
            payload.get(
                "blackmarket_purchase_count",
                payload.get("blackMarketPurchaseCount"),
            ),
            default=defaults["blackmarket_purchase_count"],
            minimum=0,
            maximum=20,
        ),
        "arena_battle_count": _coerce_daily_setting_int(
            payload.get("arena_battle_count", payload.get("arenaBattleCount")),
            default=defaults["arena_battle_count"],
            minimum=1,
            maximum=3,
        ),
    }


def _normalize_daily_settings_updates(fields: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "recruitCount": "recruit_count",
        "hangUpClaimCount": "hangup_claim_count",
        "blackMarketPurchaseCount": "blackmarket_purchase_count",
        "arenaBattleCount": "arena_battle_count",
    }
    normalized: dict[str, Any] = {}
    for key, value in fields.items():
        normalized[mapping.get(str(key), str(key))] = value
    return normalized


class XyzwStorage:
    """负责插件本地状态读写。

    当前负责：
    - 用户维度通知配置
    - 多账号绑定
    - 默认账号管理
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_file = data_dir / "state.json"
        self.data: dict[str, Any] = {"users": {}}
        self._load()

    def _load(self) -> None:
        if not self.data_file.exists():
            self.data = {"users": {}}
            return
        try:
            with open(self.data_file, "r", encoding="utf-8") as file:
                self.data = json.load(file)
        except Exception:
            self.data = {"users": {}}

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.data_file, "w", encoding="utf-8") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)

    def _ensure_account_shape(self, account: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(account, dict):
            return {"daily_settings": _default_daily_settings()}
        account["daily_settings"] = _normalize_daily_settings(
            account.get("daily_settings")
        )
        return account

    def ensure_user(self, user_id: str) -> dict[str, Any]:
        users = self.data.setdefault("users", {})
        if user_id not in users:
            users[user_id] = {
                "accounts": [],
                "default_account_id": "",
                "notify": {
                    "mode": "group_broadcast",
                    "group": None,
                },
                "schedules": {
                    "car_reminders": [],
                    "hangup_reminders": [],
                    "helper_member_reminders": [],
                    "daily_tasks": [],
                    "activity_reminders": [],
                    "action_tasks": [],
                },
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        user = users[user_id]
        user.setdefault("accounts", [])
        user.setdefault("default_account_id", "")
        notify = user.setdefault("notify", {})
        mode = str(notify.get("mode") or "").strip()
        if not mode or mode == "group_mention_first":
            notify["mode"] = "group_broadcast"
        notify.setdefault("group", None)
        schedules = user.setdefault("schedules", {})
        schedules.setdefault("car_reminders", [])
        schedules.setdefault("hangup_reminders", [])
        schedules.setdefault("helper_member_reminders", [])
        schedules.setdefault("daily_tasks", [])
        schedules.setdefault("activity_reminders", [])
        schedules.setdefault("action_tasks", [])
        user.setdefault("created_at", _now_iso())
        user.setdefault("updated_at", _now_iso())
        for account in user.setdefault("accounts", []):
            self._ensure_account_shape(account)
        return user

    def get_user_state(self, user_id: str) -> dict[str, Any]:
        return self.ensure_user(user_id)

    def set_notify_mode(self, user_id: str, mode: str) -> dict[str, Any]:
        user = self.ensure_user(user_id)
        notify = user.setdefault("notify", {})
        notify["mode"] = mode
        user["updated_at"] = _now_iso()
        self._save()
        return notify

    def bind_notify_group(
        self,
        user_id: str,
        group_id: str,
        unified_msg_origin: str,
    ) -> dict[str, Any]:
        user = self.ensure_user(user_id)
        notify = user.setdefault("notify", {})
        notify["mode"] = "group_broadcast"
        notify["group"] = {
            "group_id": group_id,
            "unified_msg_origin": str(unified_msg_origin or "").strip(),
            "bound_at": _now_iso(),
        }
        user["updated_at"] = _now_iso()
        self._save()
        return notify["group"]

    def clear_notify_group(self, user_id: str) -> None:
        user = self.ensure_user(user_id)
        notify = user.setdefault("notify", {})
        notify["group"] = None
        user["updated_at"] = _now_iso()
        self._save()

    def get_notify_group(self, user_id: str) -> dict[str, Any] | None:
        user = self.ensure_user(user_id)
        notify = user.setdefault("notify", {})
        return notify.get("group")

    def _get_car_reminder_entries(self, user_id: str) -> list[dict[str, Any]]:
        user = self.ensure_user(user_id)
        schedules = user.setdefault("schedules", {})
        return schedules.setdefault("car_reminders", [])

    def _get_daily_task_entries(self, user_id: str) -> list[dict[str, Any]]:
        user = self.ensure_user(user_id)
        schedules = user.setdefault("schedules", {})
        return schedules.setdefault("daily_tasks", [])

    def _get_hangup_reminder_entries(self, user_id: str) -> list[dict[str, Any]]:
        user = self.ensure_user(user_id)
        schedules = user.setdefault("schedules", {})
        return schedules.setdefault("hangup_reminders", [])

    def _get_helper_member_reminder_entries(self, user_id: str) -> list[dict[str, Any]]:
        user = self.ensure_user(user_id)
        schedules = user.setdefault("schedules", {})
        return schedules.setdefault("helper_member_reminders", [])

    def _get_activity_reminder_entries(self, user_id: str) -> list[dict[str, Any]]:
        user = self.ensure_user(user_id)
        schedules = user.setdefault("schedules", {})
        return schedules.setdefault("activity_reminders", [])

    def _get_action_task_entries(self, user_id: str) -> list[dict[str, Any]]:
        user = self.ensure_user(user_id)
        schedules = user.setdefault("schedules", {})
        return schedules.setdefault("action_tasks", [])

    def get_car_reminder(
        self,
        user_id: str,
        account_id: str,
    ) -> dict[str, Any] | None:
        account_id = str(account_id or "").strip()
        if not account_id:
            return None
        for item in self._get_car_reminder_entries(user_id):
            if str(item.get("account_id") or "") == account_id:
                return item
        return None

    def list_car_reminders(self, user_id: str) -> list[dict[str, Any]]:
        entries = self._get_car_reminder_entries(user_id)
        return sorted(
            entries,
            key=lambda item: (
                not bool(item.get("enabled")),
                item.get("created_at", ""),
                item.get("account_id", ""),
            ),
        )

    def upsert_car_reminder(
        self,
        user_id: str,
        account_id: str,
        interval_minutes: int,
        enabled: bool = True,
    ) -> dict[str, Any]:
        if interval_minutes <= 0:
            raise ValueError("interval_minutes 必须大于 0")

        account_id = str(account_id or "").strip()
        if not account_id:
            raise ValueError("account_id 不能为空")

        entries = self._get_car_reminder_entries(user_id)
        now = _now_iso()
        entry = self.get_car_reminder(user_id, account_id)
        if entry is None:
            entry = {
                "job_type": "car_claim_ready",
                "account_id": account_id,
                "enabled": bool(enabled),
                "interval_minutes": int(interval_minutes),
                "last_checked_at": "",
                "last_notified_at": "",
                "last_ready_signature": "",
                "last_ready_count": 0,
                "last_status": "idle",
                "last_error_message": "",
                "last_error_at": "",
                "created_at": now,
                "updated_at": now,
            }
            entries.append(entry)
        else:
            entry["enabled"] = bool(enabled)
            entry["interval_minutes"] = int(interval_minutes)
            if enabled:
                entry["last_checked_at"] = ""
                entry["last_notified_at"] = ""
                entry["last_ready_signature"] = ""
                entry["last_ready_count"] = 0
                entry["last_status"] = "idle"
                entry["last_error_message"] = ""
                entry["last_error_at"] = ""
            entry["updated_at"] = now

        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def disable_car_reminder(self, user_id: str, account_id: str) -> dict[str, Any]:
        entry = self.get_car_reminder(user_id, account_id)
        if entry is None:
            raise ValueError("未找到对应的收车提醒配置")

        now = _now_iso()
        entry["enabled"] = False
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def update_car_reminder_runtime(
        self,
        user_id: str,
        account_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        entry = self.get_car_reminder(user_id, account_id)
        if entry is None:
            raise ValueError("未找到对应的收车提醒配置")

        entry.update(fields)
        now = _now_iso()
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def iter_enabled_car_reminders(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        users = self.data.setdefault("users", {})
        for user_id in list(users.keys()):
            user = self.ensure_user(user_id)
            notify = user.get("notify", {}) or {}
            for entry in self.list_car_reminders(user_id):
                if not entry.get("enabled"):
                    continue
                account = self.get_account_by_id(user_id, str(entry.get("account_id") or ""))
                if not account:
                    continue
                items.append(
                    {
                        "user_id": str(user_id),
                        "notify_group": notify.get("group"),
                        "job": dict(entry),
                        "account": dict(account),
                    }
                )
        return items

    def get_hangup_reminder(
        self,
        user_id: str,
        account_id: str,
    ) -> dict[str, Any] | None:
        account_id = str(account_id or "").strip()
        if not account_id:
            return None
        for item in self._get_hangup_reminder_entries(user_id):
            if str(item.get("account_id") or "") == account_id:
                return item
        return None

    def list_hangup_reminders(self, user_id: str) -> list[dict[str, Any]]:
        entries = self._get_hangup_reminder_entries(user_id)
        return sorted(
            entries,
            key=lambda item: (
                not bool(item.get("enabled")),
                item.get("created_at", ""),
                item.get("account_id", ""),
            ),
        )

    def upsert_hangup_reminder(
        self,
        user_id: str,
        account_id: str,
        interval_minutes: int,
        enabled: bool = True,
    ) -> dict[str, Any]:
        if interval_minutes <= 0:
            raise ValueError("interval_minutes 必须大于 0")

        account_id = str(account_id or "").strip()
        if not account_id:
            raise ValueError("account_id 不能为空")

        entries = self._get_hangup_reminder_entries(user_id)
        now = _now_iso()
        entry = self.get_hangup_reminder(user_id, account_id)
        if entry is None:
            entry = {
                "job_type": "hangup_full",
                "account_id": account_id,
                "enabled": bool(enabled),
                "interval_minutes": int(interval_minutes),
                "last_checked_at": "",
                "last_notified_at": "",
                "last_status": "idle",
                "last_remaining_seconds": 0,
                "last_elapsed_seconds": 0,
                "last_error_message": "",
                "last_error_at": "",
                "created_at": now,
                "updated_at": now,
            }
            entries.append(entry)
        else:
            entry["enabled"] = bool(enabled)
            entry["interval_minutes"] = int(interval_minutes)
            if enabled:
                entry["last_checked_at"] = ""
                entry["last_notified_at"] = ""
                entry["last_status"] = "idle"
                entry["last_remaining_seconds"] = 0
                entry["last_elapsed_seconds"] = 0
                entry["last_error_message"] = ""
                entry["last_error_at"] = ""
            entry["updated_at"] = now

        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def disable_hangup_reminder(self, user_id: str, account_id: str) -> dict[str, Any]:
        entry = self.get_hangup_reminder(user_id, account_id)
        if entry is None:
            raise ValueError("未找到对应的挂机提醒配置")

        now = _now_iso()
        entry["enabled"] = False
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def update_hangup_reminder_runtime(
        self,
        user_id: str,
        account_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        entry = self.get_hangup_reminder(user_id, account_id)
        if entry is None:
            raise ValueError("未找到对应的挂机提醒配置")

        entry.update(fields)
        now = _now_iso()
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def iter_enabled_hangup_reminders(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        users = self.data.setdefault("users", {})
        for user_id in list(users.keys()):
            user = self.ensure_user(user_id)
            notify = user.get("notify", {}) or {}
            for entry in self.list_hangup_reminders(user_id):
                if not entry.get("enabled"):
                    continue
                account = self.get_account_by_id(user_id, str(entry.get("account_id") or ""))
                if not account:
                    continue
                items.append(
                    {
                        "user_id": str(user_id),
                        "notify_group": notify.get("group"),
                        "job": dict(entry),
                        "account": dict(account),
                    }
                )
        return items

    def get_helper_member_reminder(
        self,
        user_id: str,
        account_id: str,
    ) -> dict[str, Any] | None:
        account_id = str(account_id or "").strip()
        if not account_id:
            return None
        for item in self._get_helper_member_reminder_entries(user_id):
            if str(item.get("account_id") or "") == account_id:
                return item
        return None

    def list_helper_member_reminders(self, user_id: str) -> list[dict[str, Any]]:
        entries = self._get_helper_member_reminder_entries(user_id)
        return sorted(
            entries,
            key=lambda item: (
                not bool(item.get("enabled")),
                item.get("created_at", ""),
                item.get("account_id", ""),
            ),
        )

    def upsert_helper_member_reminder(
        self,
        user_id: str,
        account_id: str,
        interval_minutes: int,
        member_ids: list[str] | tuple[str, ...] | set[str] | str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        if interval_minutes <= 0:
            raise ValueError("interval_minutes 必须大于 0")

        account_id = str(account_id or "").strip()
        normalized_member_ids = _normalize_member_ids(member_ids)
        if not account_id:
            raise ValueError("account_id 不能为空")
        if not normalized_member_ids:
            raise ValueError("member_ids 不能为空")

        entries = self._get_helper_member_reminder_entries(user_id)
        now = _now_iso()
        entry = self.get_helper_member_reminder(user_id, account_id)
        if entry is None:
            entry = {
                "job_type": "car_helper_member_watch",
                "account_id": account_id,
                "enabled": bool(enabled),
                "interval_minutes": int(interval_minutes),
                "member_ids": normalized_member_ids,
                "window_mode": "car_send_window",
                "last_checked_at": "",
                "last_notified_at": "",
                "last_status": "idle",
                "last_signature": "",
                "last_pending_count": 0,
                "last_pending_members": [],
                "last_error_message": "",
                "last_error_at": "",
                "created_at": now,
                "updated_at": now,
            }
            entries.append(entry)
        else:
            entry["enabled"] = bool(enabled)
            entry["interval_minutes"] = int(interval_minutes)
            entry["member_ids"] = normalized_member_ids
            entry["window_mode"] = "car_send_window"
            if enabled:
                entry["last_checked_at"] = ""
                entry["last_notified_at"] = ""
                entry["last_status"] = "idle"
                entry["last_signature"] = ""
                entry["last_pending_count"] = 0
                entry["last_pending_members"] = []
                entry["last_error_message"] = ""
                entry["last_error_at"] = ""
            entry["updated_at"] = now

        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def disable_helper_member_reminder(self, user_id: str, account_id: str) -> dict[str, Any]:
        entry = self.get_helper_member_reminder(user_id, account_id)
        if entry is None:
            raise ValueError("未找到对应的护卫成员提醒配置")

        now = _now_iso()
        entry["enabled"] = False
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def update_helper_member_reminder_runtime(
        self,
        user_id: str,
        account_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        entry = self.get_helper_member_reminder(user_id, account_id)
        if entry is None:
            raise ValueError("未找到对应的护卫成员提醒配置")

        if "member_ids" in fields:
            fields["member_ids"] = _normalize_member_ids(fields.get("member_ids"))

        entry.update(fields)
        now = _now_iso()
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def iter_enabled_helper_member_reminders(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        users = self.data.setdefault("users", {})
        for user_id in list(users.keys()):
            user = self.ensure_user(user_id)
            notify = user.get("notify", {}) or {}
            for entry in self.list_helper_member_reminders(user_id):
                if not entry.get("enabled"):
                    continue
                account = self.get_account_by_id(user_id, str(entry.get("account_id") or ""))
                if not account:
                    continue
                items.append(
                    {
                        "user_id": str(user_id),
                        "notify_group": notify.get("group"),
                        "job": dict(entry),
                        "account": dict(account),
                    }
                )
        return items

    def get_daily_task(
        self,
        user_id: str,
        account_id: str,
    ) -> dict[str, Any] | None:
        account_id = str(account_id or "").strip()
        if not account_id:
            return None
        for item in self._get_daily_task_entries(user_id):
            if str(item.get("account_id") or "") == account_id:
                return item
        return None

    def list_daily_tasks(self, user_id: str) -> list[dict[str, Any]]:
        entries = self._get_daily_task_entries(user_id)
        return sorted(
            entries,
            key=lambda item: (
                not bool(item.get("enabled")),
                item.get("schedule_time", ""),
                item.get("created_at", ""),
                item.get("account_id", ""),
            ),
        )

    def upsert_daily_task(
        self,
        user_id: str,
        account_id: str,
        schedule_time: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        account_id = str(account_id or "").strip()
        schedule_time = str(schedule_time or "").strip()
        if not account_id:
            raise ValueError("account_id 不能为空")
        if not schedule_time:
            raise ValueError("schedule_time 不能为空")

        entries = self._get_daily_task_entries(user_id)
        now = _now_iso()
        entry = self.get_daily_task(user_id, account_id)
        if entry is None:
            entry = {
                "job_type": "daily_task",
                "account_id": account_id,
                "enabled": bool(enabled),
                "schedule_time": schedule_time,
                "last_run_date": "",
                "last_run_at": "",
                "last_status": "idle",
                "last_total_count": 0,
                "last_success_count": 0,
                "last_skipped_count": 0,
                "last_failed_count": 0,
                "last_error_message": "",
                "last_error_at": "",
                "last_notified_at": "",
                "created_at": now,
                "updated_at": now,
            }
            entries.append(entry)
        else:
            entry["enabled"] = bool(enabled)
            entry["schedule_time"] = schedule_time
            if enabled:
                entry["last_run_date"] = ""
                entry["last_run_at"] = ""
                entry["last_status"] = "idle"
                entry["last_total_count"] = 0
                entry["last_success_count"] = 0
                entry["last_skipped_count"] = 0
                entry["last_failed_count"] = 0
                entry["last_error_message"] = ""
                entry["last_error_at"] = ""
                entry["last_notified_at"] = ""
            entry["updated_at"] = now

        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def disable_daily_task(self, user_id: str, account_id: str) -> dict[str, Any]:
        entry = self.get_daily_task(user_id, account_id)
        if entry is None:
            raise ValueError("未找到对应的定时日常配置")

        now = _now_iso()
        entry["enabled"] = False
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def update_daily_task_runtime(
        self,
        user_id: str,
        account_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        entry = self.get_daily_task(user_id, account_id)
        if entry is None:
            raise ValueError("未找到对应的定时日常配置")

        entry.update(fields)
        now = _now_iso()
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def iter_enabled_daily_tasks(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        users = self.data.setdefault("users", {})
        for user_id in list(users.keys()):
            user = self.ensure_user(user_id)
            notify = user.get("notify", {}) or {}
            for entry in self.list_daily_tasks(user_id):
                if not entry.get("enabled"):
                    continue
                account = self.get_account_by_id(user_id, str(entry.get("account_id") or ""))
                if not account:
                    continue
                items.append(
                    {
                        "user_id": str(user_id),
                        "notify_group": notify.get("group"),
                        "job": dict(entry),
                        "account": dict(account),
                    }
                )
        return items

    def get_action_task(
        self,
        user_id: str,
        account_id: str,
        category: str,
        action: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        job_id = _build_action_job_id(account_id, category, action, options)
        return self.get_action_task_by_job_id(user_id, job_id)

    def get_action_task_by_job_id(
        self,
        user_id: str,
        job_id: str,
    ) -> dict[str, Any] | None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return None
        for item in self._get_action_task_entries(user_id):
            if str(item.get("job_id") or "") == normalized_job_id:
                return item
        return None

    def list_action_tasks(
        self,
        user_id: str,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        entries = self._get_action_task_entries(user_id)
        normalized_category = (
            _normalize_action_category(category) if category is not None else ""
        )
        filtered = [
            item
            for item in entries
            if not normalized_category
            or _normalize_action_category(str(item.get("category") or ""))
            == normalized_category
        ]
        return sorted(
            filtered,
            key=lambda item: (
                not bool(item.get("enabled")),
                item.get("schedule_time", ""),
                str(item.get("category") or ""),
                item.get("label", ""),
                item.get("created_at", ""),
            ),
        )

    def upsert_action_task(
        self,
        user_id: str,
        account_id: str,
        category: str,
        action: str,
        label: str,
        schedule_time: str,
        options: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        normalized_category = _normalize_action_category(category)
        normalized_action = str(action or "").strip()
        normalized_label = _normalize_alias(label)
        normalized_schedule_time = str(schedule_time or "").strip()
        normalized_options = _normalize_action_options(options)

        if normalized_category not in {"resource", "dungeon"}:
            raise ValueError("category 不支持")
        if not str(account_id or "").strip():
            raise ValueError("account_id 不能为空")
        if not normalized_action:
            raise ValueError("action 不能为空")
        if not normalized_schedule_time:
            raise ValueError("schedule_time 不能为空")
        if not normalized_label:
            raise ValueError("label 不能为空")

        job_id = _build_action_job_id(
            account_id=account_id,
            category=normalized_category,
            action=normalized_action,
            options=normalized_options,
        )
        entries = self._get_action_task_entries(user_id)
        now = _now_iso()
        entry = self.get_action_task_by_job_id(user_id, job_id)
        if entry is None:
            entry = {
                "job_type": "scheduled_action",
                "job_id": job_id,
                "account_id": str(account_id or "").strip(),
                "category": normalized_category,
                "action": normalized_action,
                "label": normalized_label,
                "options": normalized_options,
                "timeout_ms": int(timeout_ms or 0),
                "enabled": bool(enabled),
                "schedule_time": normalized_schedule_time,
                "last_run_date": "",
                "last_run_at": "",
                "last_status": "idle",
                "last_error_message": "",
                "last_error_at": "",
                "last_notified_at": "",
                "last_result_message": "",
                "created_at": now,
                "updated_at": now,
            }
            entries.append(entry)
        else:
            entry["label"] = normalized_label
            entry["schedule_time"] = normalized_schedule_time
            entry["options"] = normalized_options
            entry["timeout_ms"] = int(timeout_ms or 0)
            entry["enabled"] = bool(enabled)
            if enabled:
                entry["last_run_date"] = ""
                entry["last_run_at"] = ""
                entry["last_status"] = "idle"
                entry["last_error_message"] = ""
                entry["last_error_at"] = ""
                entry["last_notified_at"] = ""
                entry["last_result_message"] = ""
            entry["updated_at"] = now

        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def disable_action_task(
        self,
        user_id: str,
        account_id: str,
        category: str,
        action: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = self.get_action_task(
            user_id=user_id,
            account_id=account_id,
            category=category,
            action=action,
            options=options,
        )
        if entry is None:
            raise ValueError("未找到对应的定时执行配置")

        now = _now_iso()
        entry["enabled"] = False
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def update_action_task_runtime(
        self,
        user_id: str,
        job_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        entry = self.get_action_task_by_job_id(user_id, job_id)
        if entry is None:
            raise ValueError("未找到对应的定时执行配置")

        entry.update(fields)
        now = _now_iso()
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def iter_enabled_action_tasks(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        users = self.data.setdefault("users", {})
        for user_id in list(users.keys()):
            user = self.ensure_user(user_id)
            notify = user.get("notify", {}) or {}
            for entry in self.list_action_tasks(user_id):
                if not entry.get("enabled"):
                    continue
                account = self.get_account_by_id(user_id, str(entry.get("account_id") or ""))
                if not account:
                    continue
                items.append(
                    {
                        "user_id": str(user_id),
                        "notify_group": notify.get("group"),
                        "job": dict(entry),
                        "account": dict(account),
                    }
                )
        return items

    def get_activity_reminder(
        self,
        user_id: str,
        activity_key: str,
    ) -> dict[str, Any] | None:
        normalized_key = _normalize_activity_key(activity_key)
        if normalized_key not in {"dream", "bosstower"}:
            return None
        for item in self._get_activity_reminder_entries(user_id):
            if _normalize_activity_key(str(item.get("activity_key") or "")) == normalized_key:
                return item
        return None

    def list_activity_reminders(self, user_id: str) -> list[dict[str, Any]]:
        entries = self._get_activity_reminder_entries(user_id)
        return sorted(
            entries,
            key=lambda item: (
                not bool(item.get("enabled")),
                item.get("notify_time", ""),
                str(item.get("activity_key") or ""),
                item.get("created_at", ""),
            ),
        )

    def upsert_activity_reminder(
        self,
        user_id: str,
        activity_key: str,
        notify_time: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        normalized_key = _normalize_activity_key(activity_key)
        notify_time = str(notify_time or "").strip()
        if normalized_key not in {"dream", "bosstower"}:
            raise ValueError("activity_key 不支持")
        if not notify_time:
            raise ValueError("notify_time 不能为空")

        entries = self._get_activity_reminder_entries(user_id)
        now = _now_iso()
        entry = self.get_activity_reminder(user_id, normalized_key)
        if entry is None:
            entry = {
                "job_type": "activity_open",
                "activity_key": normalized_key,
                "enabled": bool(enabled),
                "notify_time": notify_time,
                "last_checked_at": "",
                "last_notified_date": "",
                "last_notified_at": "",
                "last_status": "idle",
                "last_error_message": "",
                "last_error_at": "",
                "created_at": now,
                "updated_at": now,
            }
            entries.append(entry)
        else:
            entry["enabled"] = bool(enabled)
            entry["notify_time"] = notify_time
            if enabled:
                entry["last_checked_at"] = ""
                entry["last_notified_date"] = ""
                entry["last_notified_at"] = ""
                entry["last_status"] = "idle"
                entry["last_error_message"] = ""
                entry["last_error_at"] = ""
            entry["updated_at"] = now

        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def disable_activity_reminder(
        self,
        user_id: str,
        activity_key: str,
    ) -> dict[str, Any]:
        normalized_key = _normalize_activity_key(activity_key)
        entry = self.get_activity_reminder(user_id, normalized_key)
        if entry is None:
            raise ValueError("未找到对应的活动开放提醒配置")

        now = _now_iso()
        entry["enabled"] = False
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def update_activity_reminder_runtime(
        self,
        user_id: str,
        activity_key: str,
        **fields: Any,
    ) -> dict[str, Any]:
        normalized_key = _normalize_activity_key(activity_key)
        entry = self.get_activity_reminder(user_id, normalized_key)
        if entry is None:
            raise ValueError("未找到对应的活动开放提醒配置")

        entry.update(fields)
        now = _now_iso()
        entry["updated_at"] = now
        self.ensure_user(user_id)["updated_at"] = now
        self._save()
        return entry

    def iter_enabled_activity_reminders(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        users = self.data.setdefault("users", {})
        for user_id in list(users.keys()):
            user = self.ensure_user(user_id)
            notify = user.get("notify", {}) or {}
            for entry in self.list_activity_reminders(user_id):
                if not entry.get("enabled"):
                    continue
                items.append(
                    {
                        "user_id": str(user_id),
                        "notify_group": notify.get("group"),
                        "job": dict(entry),
                    }
                )
        return items

    def get_daily_settings(self, user_id: str, account_id: str) -> dict[str, int]:
        account = self.get_account_by_id(user_id, str(account_id or "").strip())
        if not account:
            raise ValueError(f"未找到账号: {account_id}")
        return dict(self._ensure_account_shape(account).get("daily_settings") or {})

    def update_daily_settings(
        self,
        user_id: str,
        account_id: str,
        **fields: Any,
    ) -> dict[str, int]:
        user = self.ensure_user(user_id)
        account = self.get_account_by_id(user_id, str(account_id or "").strip())
        if not account:
            raise ValueError(f"未找到账号: {account_id}")

        normalized_fields = _normalize_daily_settings_updates(fields)
        merged = {
            **self.get_daily_settings(user_id, account_id),
            **normalized_fields,
        }
        normalized = _normalize_daily_settings(merged)
        now = _now_iso()
        account["daily_settings"] = normalized
        account["updated_at"] = now
        user["updated_at"] = now
        self._save()
        return dict(normalized)

    def reset_daily_settings(self, user_id: str, account_id: str) -> dict[str, int]:
        return self.update_daily_settings(
            user_id,
            account_id,
            **_default_daily_settings(),
        )

    def list_accounts(self, user_id: str) -> list[dict[str, Any]]:
        user = self.ensure_user(user_id)
        accounts = user.setdefault("accounts", [])
        for account in accounts:
            self._ensure_account_shape(account)
        return sorted(
            accounts,
            key=lambda item: (item.get("account_id") != user.get("default_account_id"), item.get("created_at", "")),
        )

    def get_default_account(self, user_id: str) -> dict[str, Any] | None:
        user = self.ensure_user(user_id)
        default_account_id = user.get("default_account_id", "")
        if not default_account_id:
            return None
        return self.get_account_by_id(user_id, default_account_id)

    def get_account_by_id(self, user_id: str, account_id: str) -> dict[str, Any] | None:
        for account in self.ensure_user(user_id).setdefault("accounts", []):
            if account.get("account_id") == account_id:
                return self._ensure_account_shape(account)
        return None

    def find_account_by_alias(self, user_id: str, alias: str) -> dict[str, Any] | None:
        normalized = _normalize_alias(alias).lower()
        if not normalized:
            return None
        for account in self.ensure_user(user_id).setdefault("accounts", []):
            if _normalize_alias(account.get("alias", "")).lower() == normalized:
                return self._ensure_account_shape(account)
        return None

    def resolve_account(self, user_id: str, selector: str | None = None) -> dict[str, Any] | None:
        user = self.ensure_user(user_id)
        if selector is None or not selector.strip():
            return self.get_default_account(user_id)

        selector = selector.strip()
        account = self.get_account_by_id(user_id, selector)
        if account:
            return account

        accounts = user.setdefault("accounts", [])
        prefix_matched = [
            self._ensure_account_shape(item)
            for item in accounts
            if str(item.get("account_id", "")).startswith(selector)
        ]
        if len(prefix_matched) == 1:
            return prefix_matched[0]

        normalized = selector.lower()
        for item in accounts:
            alias = _normalize_alias(item.get("alias", "")).lower()
            if alias == normalized:
                return self._ensure_account_shape(item)
        return None

    def save_account(self, user_id: str, alias: str, token: str, summary: dict[str, Any]) -> dict[str, Any]:
        return self.save_account_with_options(
            user_id=user_id,
            alias=alias,
            token=token,
            summary=summary,
            import_method="manual",
        )

    def save_account_with_options(
        self,
        user_id: str,
        alias: str,
        token: str,
        summary: dict[str, Any],
        import_method: str = "manual",
        source_url: str | None = None,
    ) -> dict[str, Any]:
        user = self.ensure_user(user_id)
        accounts = user.setdefault("accounts", [])
        normalized_alias = _normalize_alias(alias)
        normalized_token = (token or "").strip()
        normalized_source_url = str(source_url or "").strip() or None

        if not normalized_alias:
            raise ValueError("账号别名不能为空")
        if not normalized_token:
            raise ValueError("token 不能为空")

        account_id = _build_account_id(normalized_token)
        now = _now_iso()
        existing = None
        for item in accounts:
            if item.get("account_id") == account_id:
                existing = item
                break

        for item in accounts:
            if item.get("account_id") == account_id:
                continue
            if _normalize_alias(item.get("alias", "")).lower() == normalized_alias.lower():
                raise ValueError(f"别名已存在: {normalized_alias}")

        payload = {
            "account_id": account_id,
            "alias": normalized_alias,
            "token": normalized_token,
            "token_preview": _mask_token(normalized_token),
            "import_method": import_method or "manual",
            "source_url": normalized_source_url,
            "role_id": summary.get("roleId"),
            "role_name": summary.get("roleName"),
            "server_name": summary.get("serverName"),
            "level": summary.get("level"),
            "vip_level": summary.get("vipLevel"),
            "last_token_checked_at": "",
            "last_token_refreshed_at": "",
            "last_token_refresh_error": "",
            "last_token_refresh_error_at": "",
            "daily_settings": _normalize_daily_settings(
                existing.get("daily_settings") if existing else None
            ),
            "updated_at": now,
        }

        created = existing is None
        if existing is None:
            payload["created_at"] = now
            accounts.append(payload)
            account = payload
        else:
            existing.update(payload)
            account = existing

        if not user.get("default_account_id"):
            user["default_account_id"] = account_id

        user["updated_at"] = now
        self._save()
        return {
            "created": created,
            "account": account,
            "is_default": user.get("default_account_id") == account_id,
        }

    def update_account_credentials(
        self,
        user_id: str,
        account_id: str,
        token: str,
        summary: dict[str, Any] | None = None,
        source_url: str | None = None,
        import_method: str | None = None,
        refreshed_at: str | None = None,
    ) -> dict[str, Any]:
        user = self.ensure_user(user_id)
        account = self.get_account_by_id(user_id, account_id)
        if not account:
            raise ValueError(f"未找到账号: {account_id}")

        normalized_token = str(token or "").strip()
        if not normalized_token:
            raise ValueError("token 不能为空")

        summary = summary or {}
        now = refreshed_at or _now_iso()
        account["token"] = normalized_token
        account["token_preview"] = _mask_token(normalized_token)
        if source_url is not None:
            account["source_url"] = str(source_url or "").strip() or None
        if import_method:
            account["import_method"] = str(import_method).strip() or account.get(
                "import_method", "manual"
            )
        if summary:
            account["role_id"] = summary.get("roleId")
            account["role_name"] = summary.get("roleName")
            account["server_name"] = summary.get("serverName")
            account["level"] = summary.get("level")
            account["vip_level"] = summary.get("vipLevel")
        self._ensure_account_shape(account)
        account["last_token_refreshed_at"] = now
        account["last_token_refresh_error"] = ""
        account["last_token_refresh_error_at"] = ""
        account["updated_at"] = now
        user["updated_at"] = now
        self._save()
        return account

    def update_account_runtime(
        self,
        user_id: str,
        account_id: str,
        **updates: Any,
    ) -> dict[str, Any]:
        user = self.ensure_user(user_id)
        account = self.get_account_by_id(user_id, account_id)
        if not account:
            raise ValueError(f"未找到账号: {account_id}")

        now = _now_iso()
        for key, value in updates.items():
            account[key] = value
        account["updated_at"] = now
        user["updated_at"] = now
        self._save()
        return account

    def set_default_account(self, user_id: str, selector: str) -> dict[str, Any]:
        user = self.ensure_user(user_id)
        account = self.resolve_account(user_id, selector)
        if not account:
            raise ValueError(f"未找到账号: {selector}")

        user["default_account_id"] = account["account_id"]
        user["updated_at"] = _now_iso()
        self._save()
        return account

    def rename_account(self, user_id: str, selector: str, new_alias: str) -> dict[str, Any]:
        account = self.resolve_account(user_id, selector)
        if not account:
            raise ValueError(f"未找到账号: {selector}")

        normalized_alias = _normalize_alias(new_alias)
        if not normalized_alias:
            raise ValueError("新别名不能为空")

        conflict = self.find_account_by_alias(user_id, normalized_alias)
        if conflict and conflict.get("account_id") != account.get("account_id"):
            raise ValueError(f"别名已存在: {normalized_alias}")

        account["alias"] = normalized_alias
        account["updated_at"] = _now_iso()
        self.ensure_user(user_id)["updated_at"] = _now_iso()
        self._save()
        return account

    def delete_account(self, user_id: str, selector: str) -> dict[str, Any]:
        user = self.ensure_user(user_id)
        account = self.resolve_account(user_id, selector)
        if not account:
            raise ValueError(f"未找到账号: {selector}")

        accounts = user.setdefault("accounts", [])
        remaining = [item for item in accounts if item.get("account_id") != account.get("account_id")]
        user["accounts"] = remaining
        schedules = user.setdefault("schedules", {})
        schedules["car_reminders"] = [
            item
            for item in schedules.setdefault("car_reminders", [])
            if item.get("account_id") != account.get("account_id")
        ]
        schedules["hangup_reminders"] = [
            item
            for item in schedules.setdefault("hangup_reminders", [])
            if item.get("account_id") != account.get("account_id")
        ]
        schedules["helper_member_reminders"] = [
            item
            for item in schedules.setdefault("helper_member_reminders", [])
            if item.get("account_id") != account.get("account_id")
        ]
        schedules["daily_tasks"] = [
            item
            for item in schedules.setdefault("daily_tasks", [])
            if item.get("account_id") != account.get("account_id")
        ]
        schedules["action_tasks"] = [
            item
            for item in schedules.setdefault("action_tasks", [])
            if item.get("account_id") != account.get("account_id")
        ]

        if user.get("default_account_id") == account.get("account_id"):
            user["default_account_id"] = remaining[0]["account_id"] if remaining else ""

        user["updated_at"] = _now_iso()
        self._save()
        return account
