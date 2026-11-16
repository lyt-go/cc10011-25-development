"""事件分析接口：维护分析模型，覆盖提交模型评审、启用模型、归档模型等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.attribution import attribution_service
from app.services.eventanalysis import EventanalysisService

router = APIRouter(prefix="/api/eventanalysis", tags=["事件分析"])

service = EventanalysisService()

LIST_FIELDS = ["模型编号", "事件名称", "分组维度", "度量指标", "时间粒度", "归因窗口", "置信水平", "最小样本量"]
STATUSES = ["草稿", "模型评审中", "已启用", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按模型编号检索"),
    status: str | None = Query(default=None, description="草稿、模型评审中、已启用、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模型编号与状态过滤事件分析列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 静态路径统一声明在 /{entry_id} 之前，否则 "export"、"attribution" 会被当成 entry_id。
@router.get("/attribution/runs")
def attribution_runs(model_id: int | None = Query(default=None, description="可选，按模型过滤")) -> dict[str, Any]:
    """归因运行历史：保留样本判定、置信区间口径与空态原因，保证可追溯。"""
    items = attribution_service.list_runs(model_id)
    return {"items": items, "total": len(items)}


@router.post("/attribution/runs/{run_id}/retry", response_model=ActionResult)
def retry_attribution(run_id: int) -> ActionResult:
    """样本不足或窗口重叠失败后重试：从上一完整版本之后继续，不覆盖已可用数据集。"""
    try:
        record = attribution_service.retry_run(run_id)
    except ValueError as exc:
        return ActionResult(ok=False, message=str(exc))
    ok = record["状态"] == "成功发布"
    return ActionResult(ok=ok, message=record["空态说明"], entry=record)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出事件分析清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "eventanalysis", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分析模型明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分析模型 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条分析模型，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="分析模型已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条分析模型执行提交模型评审、启用模型、归档模型；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/attribution/run", response_model=ActionResult)
def run_attribution(entry_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """按归因窗口归并用户事件流并生成事件归因结果。

    重叠窗口按唯一事件编号只归并一次；未达最小样本量/置信口径的分组不保存。
    同一模型快照重复提交幂等返回已发布版本。
    """
    if service.get_entry(entry_id) is None:
        return ActionResult(ok=False, message=f"分析模型 {entry_id} 不存在或已归档")
    simulate_interrupt = bool((payload.values if payload else {}).get("simulate_interrupt"))
    record = attribution_service.run_attribution(entry_id, simulate_interrupt=simulate_interrupt)
    ok = record["状态"] == "成功发布"
    return ActionResult(ok=ok, message=record["空态说明"], entry=record)


@router.get("/{entry_id}/attribution/results")
def attribution_results(entry_id: int) -> dict[str, Any]:
    """事件分析侧读取归因结果：与分群画像、看板共用同一个当前结果版本。"""
    return service.attribution_overview(entry_id)
