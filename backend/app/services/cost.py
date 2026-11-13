"""存储成本优化业务规则：状态流转、字段校验与筛选口径都收在这里。

状态机：草稿 --开始分析--> 分析中 --生成报告--> 已生成 --执行优化--> 已执行。
任何跳级或回退都不会改动原记录，只返回缺口说明；相同请求重复到达只推进一次。
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime
from typing import Any

from app.services.audit import AuditService
from app.store import store

MODULE = "cost"
REQUIRED_FIELDS = ["报告编号", "分析范围", "存储总量"]
STATUS_ORDER = ["草稿", "分析中", "已生成", "已执行"]
# 动作 -> (要求的来源状态, 推进到的目标状态)
TRANSITIONS: dict[str, tuple[str, str]] = {
    "开始分析": ("草稿", "分析中"),
    "生成报告": ("分析中", "已生成"),
    "执行优化": ("已生成", "已执行"),
}
# 列表/导出允许的中文字段筛选（兼容既有筛选字段）
FILTERABLE_FIELDS = ["报告编号", "分析范围", "存储总量", "冷数据占比", "目标分层", "执行状态"]
# 导出必须带出的分析产物字段
EXPORT_FIELDS = [
    "报告编号", "分析范围", "存储总量", "冷数据占比", "冷数据口径", "目标分层",
    "优化建议", "预计节省", "执行结论", "执行状态", "生成时间",
]

# 状态推进与审计写入共用一把锁：列表、导出、审计始终读到同一快照。
_LOCK = threading.Lock()
# 幂等表：request_id -> 首次处理结果（报告快照, 提示语）。重复到达只返回首次结果。
_PROCESSED: dict[str, tuple[dict[str, Any], str]] = {}

_audit = AuditService()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    """输出前规范化：执行状态以流转状态为唯一事实源，分析产物字段缺省补齐。"""
    data = copy.deepcopy(row)
    data["执行状态"] = str(row.get("status") or STATUS_ORDER[0])
    for field in ("冷数据口径", "目标分层", "执行结论"):
        if not str(data.get(field) or "").strip():
            data[field] = "—"
    return data


def _apply_filters(
    rows: list[dict[str, Any]],
    *,
    keyword: str | None,
    status: str | None,
    field_filters: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """列表与导出共用同一套过滤口径，保证两边来自同一快照。"""
    result = rows
    if keyword:
        result = [row for row in result if keyword in str(row.get("报告编号", ""))]
    if status:
        result = [row for row in result if row.get("status") == status]
    for field, value in (field_filters or {}).items():
        if field not in FILTERABLE_FIELDS or not str(value).strip():
            continue
        needle = str(value).strip()
        if field == "执行状态":
            result = [row for row in result if str(row.get("status", "")) == needle]
        else:
            result = [row for row in result if needle in str(row.get(field, ""))]
    return result


def _gap_message(entry: dict[str, Any], action: str) -> str:
    """跳级或回退时说明缺口：当前在哪、该动作要求什么、还差哪些步骤。"""
    report = entry.get("报告编号", entry.get("id"))
    current = str(entry.get("status") or STATUS_ORDER[0])
    source, target = TRANSITIONS[action]
    current_idx = STATUS_ORDER.index(current) if current in STATUS_ORDER else 0
    source_idx = STATUS_ORDER.index(source)
    target_idx = STATUS_ORDER.index(target)
    if current_idx >= target_idx:
        return (
            f"成本分析报告 {report} 当前已处于「{current}」，「{action}」只会推进到「{target}」，"
            f"属于回退或重复操作，原记录保持不变"
        )
    missing = [act for act, (src, _) in TRANSITIONS.items() if current_idx <= STATUS_ORDER.index(src) < source_idx]
    gap = " → ".join(missing) if missing else "无"
    return (
        f"成本分析报告 {report} 当前处于「{current}」，「{action}」要求报告先达到「{source}」，"
        f"请先完成：{gap}；原记录保持不变"
    )


class CostService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        field_filters: dict[str, str] | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with _LOCK:
            rows = _apply_filters(store.rows(MODULE), keyword=keyword, status=status, field_filters=field_filters)
            snapshot = [_normalize(row) for row in rows]
        total = len(snapshot)
        start = max(page - 1, 0) * size
        return snapshot[start:start + size], total

    def export_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        field_filters: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """导出当前筛选范围的全部报告，与列表读取同一快照、同一过滤口径。"""
        with _LOCK:
            rows = _apply_filters(store.rows(MODULE), keyword=keyword, status=status, field_filters=field_filters)
            snapshot = [_normalize(row) for row in rows]
        items = [{field: row.get(field, "—") for field in EXPORT_FIELDS} for row in snapshot]
        return items, len(items)

    def get_entry(self, entry_ref: str) -> dict[str, Any] | None:
        """详情兼容旧报告编号：既接受数字 id，也接受 COST-0001 这类报告编号。"""
        with _LOCK:
            row: dict[str, Any] | None = None
            try:
                row = store.find(MODULE, int(entry_ref))
            except (TypeError, ValueError):
                for candidate in store.rows(MODULE):
                    if str(candidate.get("报告编号", "")) == str(entry_ref):
                        row = candidate
                        break
            return _normalize(row) if row is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with _LOCK:
            rows = store.rows(MODULE)
            entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            for field in ("冷数据占比", "冷数据口径", "目标分层", "优化建议", "预计节省", "生成时间"):
                if str(values.get(field) or "").strip():
                    entry[field] = values.get(field)
            entry["status"] = STATUS_ORDER[0]
            entry["执行状态"] = STATUS_ORDER[0]
            entry["执行结论"] = "待分析"
            entry["pending"] = True
            entry["abnormal"] = False
            rows.append(entry)
            return _normalize(entry), []

    def run_action(
        self,
        entry_ref: str,
        action: str,
        *,
        operator: str = "未指定操作人",
        request_id: str | None = None,
        expected_status: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """推进报告状态。返回 (报告快照, 提示语, 是否成功)。

        - 相同 request_id 重复到达：直接返回首次结果，不重复推进、不重复写审计；
        - 状态前置不满足（跳级/回退/并发被抢先）：原记录不变，返回缺口或冲突说明。
        """
        with _LOCK:
            if request_id and request_id in _PROCESSED:
                snapshot, message = _PROCESSED[request_id]
                return copy.deepcopy(snapshot), f"{message}（相同请求重复到达，未重复推进）", True

            entry: dict[str, Any] | None = None
            try:
                entry = store.find(MODULE, int(entry_ref))
            except (TypeError, ValueError):
                for candidate in store.rows(MODULE):
                    if str(candidate.get("报告编号", "")) == str(entry_ref):
                        entry = candidate
                        break
            if entry is None:
                return None, f"成本分析报告 {entry_ref} 不存在或已归档", False
            if action not in TRANSITIONS:
                allowed = "、".join(TRANSITIONS)
                return None, f"动作「{action}」不属于存储成本优化可执行范围（允许：{allowed}）", False

            current = str(entry.get("status") or STATUS_ORDER[0])
            source, target = TRANSITIONS[action]

            # 并发冲突：调用方基于过期快照提交，或两人同时操作同一报告。
            if expected_status and expected_status != current:
                last_by = entry.get("最近操作人", "其他操作者")
                last_at = entry.get("最近操作时间", "稍早")
                return None, (
                    f"报告已被{last_by}于{last_at}推进到「{current}」，"
                    f"你基于「{expected_status}」的本次提交未生效，请刷新后重试"
                ), False
            if current != source:
                return None, _gap_message(entry, action), False

            before = current
            entry["status"] = target
            entry["执行状态"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = False
            entry["最近操作人"] = operator
            entry["最近操作时间"] = _now()
            if action == "生成报告":
                # 分析完成：补齐分析产物（目标分层、冷数据口径、执行结论）。
                entry.setdefault("冷数据口径", "90天未访问")
                if not str(entry.get("目标分层") or "").strip():
                    entry["目标分层"] = "冷层（低频存储）"
                entry["执行结论"] = "建议执行优化"
            elif action == "执行优化":
                entry["执行结论"] = "优化已执行"

            # 动作结果同步写入审计清单：与状态推进在同一锁内完成，属于同一快照。
            _audit.record_change(
                target=str(entry.get("报告编号", entry.get("id"))),
                action=action,
                before=before,
                after=target,
                operator=operator,
            )

            snapshot = _normalize(entry)
            message = f"成本分析报告已{action}：{before} → {target}"
            if request_id:
                _PROCESSED[request_id] = (copy.deepcopy(snapshot), message)
            return snapshot, message, True
