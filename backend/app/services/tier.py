"""数据分层规划业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "tier"
REQUIRED_FIELDS = ["分层编号", "分层名称", "层级别名"]
STATUS_ORDER = ["草稿", "已定义", "生效中", "已废弃"]
ACTION_RULES = {"定义分层": "草稿", "废弃分层": "已定义", "归档分层": "生效中"}
NEGATIVE_ACTIONS = []


class TierService:
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
            rows = [row for row in rows if keyword in str(row.get("分层编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        paged = rows[start:start + size]
        report_status = self._cost_report_status_by_tier()
        # 联动成本分析报告：按目标分层匹配，把报告最新状态同步到分层定义列表。
        items = []
        for row in paged:
            item = dict(row)
            item["成本报告状态"] = report_status.get(str(row.get("分层名称", "")).strip(), "—")
            items.append(item)
        return items, total

    @staticmethod
    def _cost_report_status_by_tier() -> dict[str, str]:
        """目标分层 -> 成本分析报告最新状态（id 大的报告后写入，覆盖旧值）。"""
        mapping: dict[str, str] = {}
        for row in sorted(store.rows("cost"), key=lambda item: int(item.get("id", 0))):
            tier_name = str(row.get("目标分层") or "").strip()
            if tier_name:
                mapping[tier_name] = str(row.get("status", ""))
        return mapping

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

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
            return None, f"分层定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据分层规划可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"分层定义已{action}"
