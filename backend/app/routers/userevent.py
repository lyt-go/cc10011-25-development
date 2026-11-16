"""用户事件流接口：事件归因的输入数据源，只读并支持按事件名称检索。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.schemas import PageResult
from app.store import store

router = APIRouter(prefix="/api/userevent", tags=["用户事件流"])

STREAM_MODULE = "userevent"


@router.get("", response_model=PageResult[dict])
def list_events(
    event_name: str | None = Query(default=None, description="按事件名称过滤"),
    event_id: str | None = Query(default=None, description="按唯一事件编号过滤"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """读取用户事件流；归因去重前的原始事件都在这里，含重复进入不同分区的记录。"""
    rows = store.rows(STREAM_MODULE)
    if event_name:
        rows = [row for row in rows if row.get("事件名称") == event_name]
    if event_id:
        rows = [row for row in rows if row.get("事件编号") == event_id]
    total = len(rows)
    start = max(page - 1, 0) * size
    return PageResult(items=rows[start:start + size], total=total, page=page, size=size)
