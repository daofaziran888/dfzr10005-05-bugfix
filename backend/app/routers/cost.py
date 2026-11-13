"""存储成本优化接口：维护成本分析报告，覆盖开始分析、生成报告、执行优化等动作。

注意路由顺序：/export 必须注册在 /{entry_id} 之前，否则导出请求会被当成
按报告编号查询详情而报参数错误。
"""
from __future__ import annotations

import csv
import io
import zipfile
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.cost import EXPORT_FIELDS, FILTERABLE_FIELDS, CostService

router = APIRouter(prefix="/api/cost", tags=["存储成本优化"])

service = CostService()

LIST_FIELDS = ["报告编号", "分析范围", "存储总量", "冷数据占比", "目标分层", "优化建议", "预计节省", "执行结论", "执行状态", "生成时间"]
STATUSES = ["草稿", "分析中", "已生成", "已执行"]


def _field_filters(request: Request) -> dict[str, str]:
    """兼容既有筛选字段：查询串里出现的中文字段名都按模糊条件收下。"""
    return {
        key: value
        for key, value in request.query_params.items()
        if key in FILTERABLE_FIELDS and str(value).strip()
    }


def _export_rows(keyword: str | None, status: str | None, field_filters: dict[str, str]):
    return service.export_entries(keyword=keyword, status=status, field_filters=field_filters)


def _render_csv(items: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        writer.writerow({field: item.get(field, "—") for field in EXPORT_FIELDS})
    return buffer.getvalue()


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按报告编号检索"),
    status: str | None = Query(default=None, description="草稿、分析中、已生成、已执行"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按报告编号、状态与中文字段条件过滤成本报告列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, field_filters=_field_filters(request), page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按报告编号检索"),
    status: str | None = Query(default=None, description="草稿、分析中、已生成、已执行"),
    format: str = Query(default="json", description="json 返回结构化清单；csv/zip 打包下载文件"),
) -> Any:
    """导出当前筛选范围的全部成本报告，带出目标分层、冷数据口径与执行结论。

    与列表共用同一过滤口径和同一快照；format=csv/zip 时以附件形式打包下载，
    文件内容同样保留分析范围与冷数据占比。
    """
    items, total = _export_rows(keyword, status, _field_filters(request))
    if format == "csv":
        content = "﻿" + _render_csv(items)
        return Response(
            content=content.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="cost-reports.csv"'},
        )
    if format == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("cost-reports.csv", "﻿" + _render_csv(items))
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="cost-reports.zip"'},
        )
    return {"module": "cost", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: str) -> dict:
    """读取单条成本分析报告明细；兼容数字 id 与 COST-0001 旧报告编号。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"成本分析报告 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条成本分析报告，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="成本分析报告已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: str, payload: EntryPayload) -> ActionResult:
    """对单条成本分析报告执行开始分析、生成报告、执行优化。

    跳级或回退不会改动原记录，只说明缺口；相同 request_id 重复到达只推进一次；
    并发提交时后到者会收到明确的冲突提示。
    """
    values = payload.values
    action = str(values.get("action") or "").strip()
    operator = str(values.get("operator") or "").strip() or "未指定操作人"
    request_id = str(values.get("request_id") or "").strip() or None
    expected_status = values.get("expected_status")
    entry, message, ok = service.run_action(
        entry_id,
        action,
        operator=operator,
        request_id=request_id,
        expected_status=str(expected_status) if expected_status else None,
    )
    return ActionResult(ok=ok, message=message, entry=entry)
