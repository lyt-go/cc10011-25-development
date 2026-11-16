"""事件归因去重与样本准入。

读取用户事件流（store 表 ``userevent``）与事件分析模型配置，按归因窗口归并事件、
按唯一事件编号去重，做样本准入并计算带置信区间的事件归因结果，再以不可变版本写入
``attributionresult`` 数据集。事件分析、分群画像、监控看板通过 ``attributionversion``
读取同一个结果版本。

设计要点见 docs/adr-001-事件归属冲突-首入分区去重.md。
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

EVENT_MODULE = "eventanalysis"
STREAM_MODULE = "userevent"
RESULT_MODULE = "attributionresult"
RUN_MODULE = "attributionrun"
VERSION_MODULE = "attributionversion"
SYNC_MODULE = "segmentsync"

# 模型启用前必须固化的七项配置：少任何一项都不允许生成归因结果。
SNAPSHOT_FIELDS = [
    "事件名称",
    "分组维度",
    "度量指标",
    "时间粒度",
    "归因窗口",
    "置信水平",
    "最小样本量",
]

# 运行状态：只有「成功发布」会产生新的结果版本；其余状态都不写结果数据集，可重试。
STATUS_PUBLISHED = "成功发布"
STATUS_EMPTY_SAMPLE = "样本不足空态"
STATUS_WINDOW_CONFLICT = "窗口重叠冲突"
STATUS_INVALID_CONFIG = "配置无效"
STATUS_INTERRUPTED = "已中断"
RETRYABLE_STATUSES = {
    STATUS_EMPTY_SAMPLE,
    STATUS_WINDOW_CONFLICT,
    STATUS_INVALID_CONFIG,
    STATUS_INTERRUPTED,
}


def _next_id(module: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(module)), default=0) + 1


def _parse_int(value: Any, label: str) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是整数，当前值：{value!r}") from exc
    if number <= 0:
        raise ValueError(f"{label}必须大于 0，当前值：{number}")
    return number


def _parse_confidence(value: Any) -> float:
    try:
        level = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"置信水平必须是 0~1 之间的小数，当前值：{value!r}") from exc
    if not 0 < level < 1:
        raise ValueError(f"置信水平必须落在 (0,1) 开区间，当前值：{level}")
    return level


def _parse_day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _inverse_normal(p: float) -> float:
    """标准正态分布分位数：用 erfinv 的二分实现，避免引入 scipy 等重依赖。

    置信水平 0.95 时返回约 1.96，与常见 z 表一致。
    """
    target = 2 * p - 1.0

    def erf(x: float) -> float:
        return math.erf(x)

    lo, hi = -8.0, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if erf(mid) < target:
            lo = mid
        else:
            hi = mid
    # erf(x) = 2Φ(x·√2) − 1，二分求出的是 x，标准正态分位数需乘以 √2。
    return (lo + hi) / 2 * math.sqrt(2.0)


def _confidence_interval(values: list[float], level: float) -> dict[str, float]:
    n = len(values)
    mean = sum(values) / n
    if n == 1:
        # 单样本无法估计标准误：区间退化为点估计，但仍保留判定口径以便追溯。
        return {
            "均值": mean,
            "标准差": 0.0,
            "标准误": 0.0,
            "z分位": _inverse_normal(1 - (1 - level) / 2),
            "置信区间下限": mean,
            "置信区间上限": mean,
        }
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = math.sqrt(variance)
    z = _inverse_normal(1 - (1 - level) / 2)
    margin = z * std / math.sqrt(n)
    return {
        "均值": mean,
        "标准差": std,
        "标准误": std / math.sqrt(n),
        "z分位": z,
        "置信区间下限": mean - margin,
        "置信区间上限": mean + margin,
    }


class AttributionService:
    """事件归因主流程，所有读写都落在 app.store 的内存表里，方便测试与重置。"""

    # ------------------------------------------------------------------ 配置与快照
    def build_snapshot(self, model_id: int) -> tuple[dict[str, Any] | None, str]:
        """模型启用前固化七项配置；缺项或非法时返回可读原因，不产生任何结果。"""
        model = store.find(EVENT_MODULE, model_id)
        if model is None:
            return None, f"分析模型 {model_id} 不存在或已归档"
        snapshot = {field: model.get(field) for field in SNAPSHOT_FIELDS}
        missing = [field for field in SNAPSHOT_FIELDS if not str(snapshot[field] or "").strip()]
        if missing:
            return None, f"模型启用配置不完整，缺少：{'、'.join(missing)}"
        try:
            snapshot["_归因窗口天"] = _parse_int(snapshot["归因窗口"], "归因窗口")
            snapshot["_置信水平值"] = _parse_confidence(snapshot["置信水平"])
            snapshot["_最小样本量值"] = _parse_int(snapshot["最小样本量"], "最小样本量")
        except ValueError as exc:
            return None, str(exc)
        snapshot["_快照哈希"] = self.snapshot_hash(model_id, snapshot)
        return snapshot, ""

    @staticmethod
    def snapshot_hash(model_id: int, snapshot: dict[str, Any]) -> str:
        """同一模型快照重复提交时只留一份结果：哈希覆盖七项配置与模型主键。"""
        payload = {"模型id": model_id}
        payload.update({field: str(snapshot.get(field) or "") for field in SNAPSHOT_FIELDS})
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def find_published_version(self, model_id: int, snapshot_hash: str) -> dict[str, Any] | None:
        for row in store.rows(VERSION_MODULE):
            if int(row.get("模型id", 0)) == model_id and row.get("快照哈希") == snapshot_hash:
                return row
        return None

    # ------------------------------------------------------------------ 事件去重
    def dedupe_stream(self, event_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """按模型事件名称过滤事件流，并按唯一事件编号归并。

        归属冲突处理（方案 A，详见 ADR）：同一事件编号在「首次进入分区」与「归因窗口
        结束分区」之间冲突时，以首入分区为唯一锚点，全局只保留第一条；后到分区的重复
        记录计入 duplicates，绝不进入第二个分组。
        """
        stream = [row for row in store.rows(STREAM_MODULE) if row.get("事件名称") == event_name]
        chosen: dict[str, dict[str, Any]] = {}
        duplicates: list[dict[str, Any]] = []
        undatable: list[dict[str, Any]] = []
        for row in sorted(stream, key=lambda r: int(r.get("id", 0))):
            event_id = str(row.get("事件编号") or "").strip()
            if not event_id:
                undatable.append(row)
                continue
            day = _parse_day(row.get("首入分区")) or _parse_day(row.get("事件时间"))
            if day is None:
                undatable.append(row)
                continue
            if event_id in chosen:
                duplicates.append(row)
                continue
            chosen[event_id] = {**row, "_归属分区": day}
        return list(chosen.values()), duplicates, undatable

    @staticmethod
    def _assign_window(event_day: date, min_day: date, width: int) -> date:
        """把事件归到最早一个覆盖它的窗口起点，保证重叠窗口里同一事件只落一次。"""
        earliest_start = event_day - timedelta(days=width - 1)
        return max(min_day, earliest_start)

    # ------------------------------------------------------------------ 主入口
    def run_attribution(
        self,
        model_id: int,
        *,
        simulate_interrupt: bool = False,
    ) -> dict[str, Any]:
        """执行一次归因。返回运行记录；失败/空态都可凭 run_id 调 retry 重跑。"""
        model = store.find(EVENT_MODULE, model_id)
        model_code = str(model.get("模型编号")) if model else f"EVEN-{model_id}"
        run_id = _next_id(RUN_MODULE)

        def write_run(status: str, retryable: bool, note: str, **extra: Any) -> dict[str, Any]:
            record = {
                "id": run_id,
                "运行编号": f"RUN-{run_id:04d}",
                "模型id": model_id,
                "模型编号": model_code,
                "状态": status,
                "可重试": retryable,
                "空态说明": note,
                "快照哈希": extra.get("snapshot_hash", ""),
                "快照": extra.get("snapshot", {}),
                "输入事件数": extra.get("input_count", 0),
                "去重事件数": extra.get("unique_count", 0),
                "丢弃重复数": extra.get("dup_count", 0),
                "窗口数": extra.get("window_count", 0),
                "结果版本": extra.get("version"),
                "分组判定": extra.get("judgments", []),
                "创建时间": extra.get("created_at", datetime.utcnow().isoformat(timespec="seconds")),
                "更新时间": datetime.utcnow().isoformat(timespec="seconds"),
            }
            store.rows(RUN_MODULE).append(record)
            return record

        snapshot, error = self.build_snapshot(model_id)
        if snapshot is None:
            return write_run(STATUS_INVALID_CONFIG, True, f"未生成归因结果：{error}")

        snapshot_hash = snapshot["_快照哈希"]
        # 幂等：同一模型快照重复提交直接返回已发布版本，不再产生第二份结果。
        existing = self.find_published_version(model_id, snapshot_hash)
        if existing is not None:
            note = f"快照 {snapshot_hash} 已发布为结果版本 v{existing['当前结果版本']}，重复提交已幂等忽略"
            return write_run(
                STATUS_PUBLISHED,
                False,
                note,
                snapshot_hash=snapshot_hash,
                snapshot=self._public_snapshot(snapshot),
                version=existing["当前结果版本"],
                judgments=[{"说明": note}],
            )

        events, duplicates, undatable = self.dedupe_stream(snapshot["事件名称"])
        input_count = len(events) + len(duplicates) + len(undatable)
        if undatable:
            return write_run(
                STATUS_WINDOW_CONFLICT,
                True,
                "存在缺少首入分区且无法解析事件时间的事件，重叠窗口归属无法判定，请补齐分区后重试",
                snapshot_hash=snapshot_hash,
                snapshot=self._public_snapshot(snapshot),
                input_count=input_count,
                unique_count=len(events),
                dup_count=len(duplicates),
            )
        if not events:
            return write_run(
                STATUS_EMPTY_SAMPLE,
                True,
                f"事件流中没有名称为「{snapshot['事件名称']}」的事件，无法归并；请先接入用户事件流后重试",
                snapshot_hash=snapshot_hash,
                snapshot=self._public_snapshot(snapshot),
                input_count=input_count,
                dup_count=len(duplicates),
            )

        width = snapshot["_归因窗口天"]
        level = snapshot["_置信水平值"]
        min_sample = snapshot["_最小样本量值"]
        min_day = min(e["_归属分区"] for e in events)

        groups: dict[tuple[date, str], list[dict[str, Any]]] = {}
        for event in events:
            window_start = self._assign_window(event["_归属分区"], min_day, width)
            key = (window_start, str(event.get("维度取值") or "未分组"))
            groups.setdefault(key, []).append(event)

        judgments: list[dict[str, Any]] = []
        staged_rows: list[dict[str, Any]] = []
        for (window_start, group_value), group_events in sorted(groups.items()):
            values: list[float] = []
            for event in group_events:
                try:
                    values.append(float(event.get("度量值") or 0.0))
                except (TypeError, ValueError):
                    values.append(0.0)
            stats = _confidence_interval(values, level)
            n = len(group_events)
            passed = n >= min_sample
            window_end = window_start + timedelta(days=width)
            judgment = {
                "归因窗口起点": window_start.isoformat(),
                "归因窗口结束": window_end.isoformat(),
                "分组维度": snapshot["分组维度"],
                "维度取值": group_value,
                "样本量": n,
                "最小样本量": min_sample,
                "置信水平": level,
                "样本判定": "通过" if passed else "未达样本门槛",
                "判定说明": (
                    f"样本量 {n} ≥ 最小样本量 {min_sample}，允许发布"
                    if passed
                    else f"样本量 {n} < 最小样本量 {min_sample}，该分组结果不保存、不下发"
                ),
                "置信区间下限": round(stats["置信区间下限"], 6),
                "置信区间上限": round(stats["置信区间上限"], 6),
            }
            judgments.append(judgment)
            if not passed:
                continue
            staged_rows.append({
                "结果版本": None,  # 提交时统一填版本号，暂存行不可被其他读取方看到。
                "模型id": model_id,
                "模型编号": model_code,
                "快照哈希": snapshot_hash,
                "归因窗口起点": window_start.isoformat(),
                "归因窗口结束": window_end.isoformat(),
                "时间粒度": snapshot["时间粒度"],
                "分组维度": snapshot["分组维度"],
                "维度取值": group_value,
                "样本量": n,
                "唯一事件编号": sorted(str(e.get("事件编号")) for e in group_events),
                "度量指标": snapshot["度量指标"],
                "均值": round(stats["均值"], 6),
                "标准差": round(stats["标准差"], 6),
                "标准误": round(stats["标准误"], 6),
                "z分位": round(stats["z分位"], 6),
                "置信水平": level,
                "置信区间下限": round(stats["置信区间下限"], 6),
                "置信区间上限": round(stats["置信区间上限"], 6),
                "样本判定": "通过",
                "判定说明": judgment["判定说明"],
                "发布时间": None,
            })

        window_count = len({key[0] for key in groups})
        # 测试钩子：暂存行已在内存算好、但在提交版本前中断，验证暂存行不落库、
        # 已可用数据集不被覆盖，恢复时从上一完整版本之后继续。
        if simulate_interrupt and staged_rows:
            return write_run(
                STATUS_INTERRUPTED,
                True,
                f"归因计算在结果提交前中断（已暂存 {len(staged_rows)} 个达标分组但未写入）；可从上次完整版本之后重试",
                snapshot_hash=snapshot_hash,
                snapshot=self._public_snapshot(snapshot),
                input_count=input_count,
                unique_count=len(events),
                dup_count=len(duplicates),
                window_count=window_count,
                judgments=judgments,
            )
        if not staged_rows:
            return write_run(
                STATUS_EMPTY_SAMPLE,
                True,
                f"全部 {len(groups)} 个分组均未达到最小样本量 {min_sample}，本次不保存任何归因结果；积累样本后可重试",
                snapshot_hash=snapshot_hash,
                snapshot=self._public_snapshot(snapshot),
                input_count=input_count,
                unique_count=len(events),
                dup_count=len(duplicates),
                window_count=window_count,
                judgments=judgments,
            )

        version = self._commit_version(model_id, model_code, snapshot_hash, snapshot, staged_rows, len(groups))
        # 另一个业务面：分群画像沿现有调用链同步消费本版本结果（内部幂等）。
        from app.services.segmentprofile import SegmentprofileService

        sync_info = SegmentprofileService().consume_attribution(version, snapshot_hash)

        note = (
            f"已发布结果版本 v{version}：{len(staged_rows)}/{len(groups)} 个分组通过样本准入；"
            f"去重丢弃 {len(duplicates)} 条重复事件；分群画像同步 {sync_info['同步分组数']} 个分组"
        )
        return write_run(
            STATUS_PUBLISHED,
            False,
            note,
            snapshot_hash=snapshot_hash,
            snapshot=self._public_snapshot(snapshot),
            input_count=input_count,
            unique_count=len(events),
            dup_count=len(duplicates),
            window_count=window_count,
            version=version,
            judgments=judgments,
        )

    # ------------------------------------------------------------------ 版本提交
    def _commit_version(
        self,
        model_id: int,
        model_code: str,
        snapshot_hash: str,
        snapshot: dict[str, Any],
        staged_rows: list[dict[str, Any]],
        group_total: int,
    ) -> int:
        """不可变提交：新版本只追加，不覆盖已可用数据集；中断的暂存行永远进不来。"""
        version = max((int(r.get("当前结果版本", 0)) for r in store.rows(VERSION_MODULE)), default=0) + 1
        published_at = datetime.utcnow().isoformat(timespec="seconds")
        for row in staged_rows:
            row["id"] = _next_id(RESULT_MODULE)
            row["结果版本"] = version
            row["发布时间"] = published_at
            store.rows(RESULT_MODULE).append(row)
        pointer = self.find_published_version(model_id, snapshot_hash)
        if pointer is None:
            store.rows(VERSION_MODULE).append({
                "id": _next_id(VERSION_MODULE),
                "模型id": model_id,
                "模型编号": model_code,
                "快照哈希": snapshot_hash,
                "当前结果版本": version,
                "结果条数": len(staged_rows),
                "分组总数": group_total,
                "发布时间": published_at,
                "快照": self._public_snapshot(snapshot),
            })
        else:  # 理论上不会发生（幂等已在前面拦截），保留兜底避免双版本。
            pointer["当前结果版本"] = version
            pointer["结果条数"] = len(staged_rows)
            pointer["发布时间"] = published_at
        return version

    def retry_run(self, run_id: int) -> dict[str, Any]:
        """样本不足或窗口重叠失败后重试：从上次完整版本之后继续，不覆盖旧数据集。"""
        record = next((r for r in store.rows(RUN_MODULE) if int(r.get("id", 0)) == run_id), None)
        if record is None:
            raise ValueError(f"运行记录 {run_id} 不存在")
        if record["状态"] not in RETRYABLE_STATUSES:
            raise ValueError(f"运行 {record['运行编号']} 状态为「{record['状态']}」，无需重试")
        return self.run_attribution(int(record["模型id"]))

    # ------------------------------------------------------------------ 统一读取
    def current_results(self, model_id: int | None = None) -> list[dict[str, Any]]:
        """事件分析、分群画像、看板共用的读取口径：只返回各模型当前版本指针指向的结果。"""
        pointers = store.rows(VERSION_MODULE)
        if model_id is not None:
            pointers = [r for r in pointers if int(r.get("模型id", 0)) == model_id]
        versions = {int(r["当前结果版本"]) for r in pointers}
        return [row for row in store.rows(RESULT_MODULE) if int(row.get("结果版本", 0)) in versions]

    def list_runs(self, model_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(RUN_MODULE)
        if model_id is not None:
            rows = [r for r in rows if int(r.get("模型id", 0)) == model_id]
        return sorted(rows, key=lambda r: int(r.get("id", 0)), reverse=True)

    @staticmethod
    def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {field: snapshot.get(field) for field in SNAPSHOT_FIELDS}


attribution_service = AttributionService()
