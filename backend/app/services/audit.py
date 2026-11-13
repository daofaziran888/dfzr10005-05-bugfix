"""变更审计追踪业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "audit"
REQUIRED_FIELDS = ["记录编号", "变更对象", "变更类型"]
STATUS_ORDER = ["已记录", "已复核", "已归档"]
ACTION_RULES = {"标记复核": "已记录", "归档记录": "已复核"}
NEGATIVE_ACTIONS = []


class AuditService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def record_change(
        self,
        *,
        target: str,
        action: str,
        before: str,
        after: str,
        operator: str,
    ) -> dict[str, Any]:
        """把一次业务动作写进审计清单。记录编号沿用 AUDI-xxxx 旧编号规则自增。"""
        rows = store.rows(MODULE)
        seq = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        entry = {
            "id": seq,
            "status": STATUS_ORDER[0],
            "pending": True,
            "abnormal": False,
            "记录编号": f"AUDI-{seq:04d}",
            "变更对象": target,
            "变更类型": action,
            "变更前值": before,
            "变更后值": after,
            "操作人": operator,
            "操作时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "复核状态": STATUS_ORDER[0],
        }
        rows.append(entry)
        return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"变更记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于变更审计追踪可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"变更记录已{action}"
