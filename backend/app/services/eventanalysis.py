"""事件分析业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.attribution import SNAPSHOT_FIELDS, attribution_service
from app.store import store

MODULE = "eventanalysis"
REQUIRED_FIELDS = ["模型编号", "事件名称", "分组维度"]
STATUS_ORDER = ["草稿", "模型评审中", "已启用", "已归档"]
ACTION_RULES = {"提交模型评审": "模型评审中", "启用模型": "已启用", "归档模型": "已归档"}
NEGATIVE_ACTIONS = []


class EventanalysisService:
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
            rows = [row for row in rows if keyword in str(row.get("模型编号", ""))]
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
            return None, f"分析模型 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于事件分析可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        # 样本准入前置闸门：启用模型前必须固化七项归因配置，且数值口径合法。
        if action == "启用模型":
            snapshot, reason = attribution_service.build_snapshot(entry_id)
            if snapshot is None:
                return None, f"启用被样本准入规则拦截：{reason}"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"分析模型已{action}"

    def attribution_overview(self, entry_id: int) -> dict[str, Any]:
        """事件分析读取归因结果的统一入口：只暴露当前结果版本，保证与画像、看板同版本。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"分析模型 {entry_id} 不存在或已归档", "结果": [], "运行记录": []}
        results = attribution_service.current_results(entry_id)
        runs = attribution_service.list_runs(entry_id)
        return {
            "ok": True,
            "模型id": entry_id,
            "模型编号": entry.get("模型编号"),
            "固化配置字段": SNAPSHOT_FIELDS,
            "结果": results,
            "结果条数": len(results),
            "运行记录": runs,
        }
