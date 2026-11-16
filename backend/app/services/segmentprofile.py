"""分群画像业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.attribution import RESULT_MODULE, SYNC_MODULE, VERSION_MODULE, attribution_service
from app.store import store

MODULE = "segmentprofile"
REQUIRED_FIELDS = ["画像编号", "分群名称", "筛选规则"]
STATUS_ORDER = ["草稿", "画像评审中", "已发布", "已下线"]
ACTION_RULES = {"提交画像评审": "画像评审中", "发布画像": "已发布", "下线画像": "已下线"}
NEGATIVE_ACTIONS = ["下线画像"]


class SegmentprofileService:
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
            rows = [row for row in rows if keyword in str(row.get("画像编号", ""))]
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
            return None, f"画像定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于分群画像可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"画像定义已{action}"

    # ------------------------------------------------------ 事件归因结果消费（下游画像）
    def consume_attribution(self, version: int, snapshot_hash: str) -> dict[str, Any]:
        """沿「归因提交 -> 分群画像服务」这条现有调用链同步消费结果数据集。

        只处理通过样本准入、已经发布的结果行；按（结果版本, 维度取值）幂等，
        同一模型快照重复提交/重试不会产生重复画像或重复台账。
        """
        version_rows = [r for r in store.rows(VERSION_MODULE) if int(r.get("当前结果版本", 0)) == version]
        if not version_rows:
            return {"ok": False, "版本": version, "同步分组数": 0, "说明": "结果版本不存在或未发布，画像不更新"}
        pointer = version_rows[0]
        results = [
            row for row in store.rows(RESULT_MODULE)
            if int(row.get("结果版本", 0)) == version and row.get("快照哈希") == snapshot_hash
        ]
        ledger = store.rows(SYNC_MODULE)
        existing_keys = {
            (int(r.get("结果版本", 0)), str(r.get("维度取值"))) for r in ledger
        }
        now = datetime.utcnow().isoformat(timespec="seconds")
        written = 0
        for row in results:
            key = (version, str(row.get("维度取值")))
            if key in existing_keys:
                continue
            group_name = f"{pointer['模型编号']}-{row.get('维度取值')}归因分群"
            ledger.append({
                "id": max((int(r.get("id", 0)) for r in ledger), default=0) + 1,
                "同步编号": f"SYNC-{version}-{row.get('维度取值')}",
                "模型编号": pointer["模型编号"],
                "快照哈希": snapshot_hash,
                "结果版本": version,
                "分组维度": row.get("分组维度"),
                "维度取值": row.get("维度取值"),
                "样本量": row.get("样本量"),
                "置信区间下限": row.get("置信区间下限"),
                "置信区间上限": row.get("置信区间上限"),
                "样本判定": row.get("样本判定"),
                "同步时间": now,
            })
            self._upsert_profile(group_name, row, pointer, now)
            existing_keys.add(key)
            written += 1
        return {
            "ok": True,
            "版本": version,
            "同步分组数": written,
            "结果分组数": len(results),
            "说明": "已同步" if written else "该版本此前已同步，本次幂等跳过",
        }

    def _upsert_profile(self, group_name: str, result: dict[str, Any], pointer: dict[str, Any], now: str) -> None:
        """回写分群画像主表：画像编号稳定，重复同步只更新画像快照，不新增重复行。"""
        rows = store.rows(MODULE)
        profile = next((r for r in rows if r.get("分群名称") == group_name), None)
        if profile is None:
            # 用稳定的字符和生成编号后缀，避免内置 hash 跨进程随机化；去重仍以分群名称为准。
            stable_suffix = sum(ord(ch) for ch in group_name) % 10000
            rows.append({
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "status": "已发布",
                "pending": False,
                "abnormal": False,
                "画像编号": f"SEGM-A{result.get('结果版本')}-{stable_suffix:04d}",
                "分群名称": group_name,
                "筛选规则": f"由事件归因结果 v{result.get('结果版本')} 自动回写（{pointer['模型编号']}）",
                "画像维度": result.get("分组维度"),
                "对比基准": "全量事件均值",
                "更新周期": "随归因结果版本同步",
                "覆盖对象数": result.get("样本量"),
                "画像快照": (
                    f"版本v{result.get('结果版本')} | 均值{result.get('均值')} | "
                    f"CI[{result.get('置信区间下限')}, {result.get('置信区间上限')}] | "
                    f"置信水平{result.get('置信水平')}"
                ),
                "归因结果版本": result.get("结果版本"),
                "快照哈希": result.get("快照哈希"),
                "最近同步时间": now,
            })
            return
        profile["覆盖对象数"] = result.get("样本量")
        profile["画像快照"] = (
            f"版本v{result.get('结果版本')} | 均值{result.get('均值')} | "
            f"CI[{result.get('置信区间下限')}, {result.get('置信区间上限')}] | "
            f"置信水平{result.get('置信水平')}"
        )
        profile["归因结果版本"] = result.get("结果版本")
        profile["最近同步时间"] = now

    def attribution_feed(self) -> dict[str, Any]:
        """分群画像侧查看自己消费的归因结果数据集与同步台账，读的是同一个结果版本。"""
        return {
            "ok": True,
            "当前结果": attribution_service.current_results(),
            "同步台账": sorted(store.rows(SYNC_MODULE), key=lambda r: int(r.get("id", 0)), reverse=True),
        }
