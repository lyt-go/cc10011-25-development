"""监控大盘业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.attribution import attribution_service
from app.store import store

MODULE = "dashboard"
REQUIRED_FIELDS = ["大盘编号", "大盘名称", "包含指标组"]
STATUS_ORDER = ["草稿", "大盘评审中", "已发布", "已下线"]
ACTION_RULES = {"提交大盘评审": "大盘评审中", "发布大盘": "已发布", "下线大盘": "已下线"}
NEGATIVE_ACTIONS = ["下线大盘"]


class DashboardService:
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
            rows = [row for row in rows if keyword in str(row.get("大盘编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

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
            return None, f"大盘配置 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于监控大盘可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"大盘配置已{action}"

    def attribution_panel(self, model_id: int | None = None) -> dict[str, Any]:
        """看板读取事件归因结果：直接走统一的当前结果版本口径，与事件分析、分群画像同源。"""
        results = attribution_service.current_results(model_id)
        return {
            "ok": True,
            "读取口径": "attributionversion 当前结果版本",
            "结果版本集合": sorted({int(r["结果版本"]) for r in results}),
            "结果条数": len(results),
            "结果": results,
        }
