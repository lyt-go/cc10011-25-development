"""事件归因去重与样本准入的回归测试。

只用标准库：通过内存 ASGI 调用直接驱动 FastAPI，不依赖 httpx/pytest，
克隆后 `python -m app.tests.test_attribution` 即可跑。
"""
from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from app.main import app
from app.services.attribution import (
    RESULT_MODULE,
    RUN_MODULE,
    SYNC_MODULE,
    VERSION_MODULE,
    _inverse_normal,
    attribution_service as svc,
)
from app.store import store


class AsgiResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        return json.loads(self._body.decode("utf-8"))


def call_route(method: str, path: str, payload: dict[str, Any] | None = None) -> AsgiResponse:
    """最小 ASGI 测试客户端：一次 http 请求 -> 聚合响应体。"""
    if "?" in path:
        route, query = path.split("?", 1)
    else:
        route, query = path, ""
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    received: dict[str, Any] = {"status": 200, "chunks": []}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            received["status"] = message["status"]
        elif message["type"] == "http.response.body":
            received["chunks"].append(message.get("body", b""))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": route,
        "raw_path": route.encode("utf-8"),
        "query_string": query.encode("utf-8"),
        "headers": [(b"content-type", b"application/json")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    return AsgiResponse(received["status"], b"".join(received["chunks"]))


class AttributionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例从种子数据的干净状态开始。
        store.__init__()  # type: ignore[misc]

    # ------------------------------------------------------------ 统计口径
    def test_inverse_normal_matches_z_table(self) -> None:
        self.assertAlmostEqual(_inverse_normal(0.975), 1.959964, places=3)
        self.assertAlmostEqual(_inverse_normal(0.95), 1.644854, places=3)

    # ------------------------------------------------------------ 去重与窗口
    def test_overlapping_windows_dedupe_by_unique_event_id(self) -> None:
        record = svc.run_attribution(3)
        self.assertEqual(record["状态"], "成功发布")
        # 8 条原始事件里 EVT-1002、EVT-1005 各重复一次 -> 去重后 6 条，丢弃 2 条。
        self.assertEqual(record["输入事件数"], 8)
        self.assertEqual(record["去重事件数"], 6)
        self.assertEqual(record["丢弃重复数"], 2)
        # 通过门槛的分组里，唯一事件编号不重复。
        for row in store.rows(RESULT_MODULE):
            ids = row["唯一事件编号"]
            self.assertEqual(len(ids), len(set(ids)))

    def test_first_entry_partition_wins_conflict(self) -> None:
        events, duplicates, undatable = svc.dedupe_stream("下单支付")
        self.assertEqual(undatable, [])
        by_id = {e["事件编号"]: e for e in events}
        # 首入分区方案：EVT-1002 锚定 09-01，而不是重复记录的 09-02。
        self.assertEqual(by_id["EVT-1002"]["_归属分区"].isoformat(), "2026-09-01")
        self.assertEqual(by_id["EVT-1005"]["_归属分区"].isoformat(), "2026-09-04")
        dup_ids = sorted(d["事件编号"] for d in duplicates)
        self.assertEqual(dup_ids, ["EVT-1002", "EVT-1005"])

    # ------------------------------------------------------------ 样本准入
    def test_sub_threshold_group_is_not_saved(self) -> None:
        record = svc.run_attribution(3)
        judgments = {j["维度取值"] + j["归因窗口起点"]: j for j in record["分组判定"]}
        # 单样本分组被判定未达标。
        single = [j for j in record["分组判定"] if j["样本量"] == 1]
        self.assertTrue(single)
        self.assertTrue(all(j["样本判定"] == "未达样本门槛" for j in single))
        # 数据集只保存通过的分组。
        self.assertTrue(all(r["样本判定"] == "通过" for r in store.rows(RESULT_MODULE)))
        self.assertEqual(len(store.rows(RESULT_MODULE)), 1)

    def test_all_groups_below_threshold_gives_explainable_empty_state(self) -> None:
        record = svc.run_attribution(2)  # 最小样本量 10
        self.assertEqual(record["状态"], "样本不足空态")
        self.assertTrue(record["可重试"])
        self.assertIn("最小样本量", record["空态说明"])
        self.assertIsNone(record["结果版本"])
        self.assertEqual(store.rows(RESULT_MODULE), [])
        # 判定明细仍可追溯。
        self.assertTrue(record["分组判定"])

    def test_invalid_config_is_blocked_and_retryable(self) -> None:
        record = svc.run_attribution(1)  # 七项配置是占位文本
        self.assertEqual(record["状态"], "配置无效")
        self.assertTrue(record["可重试"])
        self.assertIn("归因窗口", record["空态说明"])
        self.assertEqual(store.rows(RESULT_MODULE), [])

    def test_empty_event_stream_empty_state(self) -> None:
        store.find("eventanalysis", 2)["事件名称"] = "不存在的事件XYZ"
        record = svc.run_attribution(2)
        self.assertEqual(record["状态"], "样本不足空态")
        self.assertIn("没有名称为", record["空态说明"])

    # ------------------------------------------------------------ 幂等与版本
    def test_same_snapshot_submitted_twice_keeps_single_result(self) -> None:
        first = svc.run_attribution(3)
        second = svc.run_attribution(3)
        self.assertEqual(first["结果版本"], second["结果版本"])
        self.assertEqual(len(store.rows(VERSION_MODULE)), 1)
        # 结果数据集只有一份（按快照去重，不追加第二份）。
        self.assertEqual(len(store.rows(RESULT_MODULE)), 1)

    def test_new_snapshot_appends_version_without_overwriting(self) -> None:
        first = svc.run_attribution(3)
        model = store.find("eventanalysis", 3)
        model["最小样本量"] = "1"  # 改配置 -> 新快照
        second = svc.run_attribution(3)
        self.assertEqual(second["结果版本"], first["结果版本"] + 1)
        # 旧版本数据集仍然可用、未被覆盖。
        v1 = [r for r in store.rows(RESULT_MODULE) if r["结果版本"] == first["结果版本"]]
        self.assertTrue(v1)
        self.assertEqual(len(store.rows(VERSION_MODULE)), 2)

    # ------------------------------------------------------------ 中断恢复
    def test_interrupted_run_does_not_touch_available_dataset(self) -> None:
        svc.run_attribution(3)  # v1 可用
        before = len(store.rows(RESULT_MODULE))
        # 构造一个尚未发布过的新快照，再模拟中断。
        model = store.find("eventanalysis", 3)
        model["归因窗口"] = "5"
        interrupted = svc.run_attribution(3, simulate_interrupt=True)
        self.assertEqual(interrupted["状态"], "已中断")
        self.assertEqual(len(store.rows(RESULT_MODULE)), before)
        # 重试：从上一完整版本之后继续，产生 v2，v1 保留。
        recovered = svc.retry_run(interrupted["id"])
        self.assertEqual(recovered["状态"], "成功发布")
        self.assertEqual(recovered["结果版本"], 2)
        self.assertTrue(any(r["结果版本"] == 1 for r in store.rows(RESULT_MODULE)))
        self.assertTrue(any(r["结果版本"] == 2 for r in store.rows(RESULT_MODULE)))

    def test_retry_non_retryable_is_rejected(self) -> None:
        published = svc.run_attribution(3)
        with self.assertRaises(ValueError):
            svc.retry_run(published["id"])

    # ------------------------------------------------------------ 下游画像同步
    def test_segment_profile_consumes_same_version_idempotently(self) -> None:
        record = svc.run_attribution(3)
        version = record["结果版本"]
        # 画像同步台账与画像主表已回写。
        self.assertTrue(store.rows(SYNC_MODULE))
        profiles = [p for p in store.rows("segmentprofile") if p.get("归因结果版本") == version]
        self.assertTrue(profiles)
        # 重复消费（模拟重试/重复回写）不产生重复台账与重复画像。
        sync_again = svc  # 直接再触发一次内部消费链路
        from app.services.segmentprofile import SegmentprofileService

        info1 = SegmentprofileService().consume_attribution(version, record["快照哈希"])
        info2 = SegmentprofileService().consume_attribution(version, record["快照哈希"])
        self.assertEqual(info1["同步分组数"], 0)  # 首次在 run 里已同步
        self.assertEqual(info2["同步分组数"], 0)
        ledger = [r for r in store.rows(SYNC_MODULE) if r["结果版本"] == version]
        self.assertEqual(len(ledger), len({r["维度取值"] for r in ledger}))

    def test_three_consumers_read_same_version(self) -> None:
        record = svc.run_attribution(3)
        version = record["结果版本"]
        # 事件分析、分群画像、看板三条读取链路拿到同一批结果版本。
        from app.services.dashboard import DashboardService
        from app.services.eventanalysis import EventanalysisService
        from app.services.segmentprofile import SegmentprofileService

        ea_versions = {r["结果版本"] for r in EventanalysisService().attribution_overview(3)["结果"]}
        seg_versions = {r["结果版本"] for r in SegmentprofileService().attribution_feed()["当前结果"]}
        dash_versions = {r["结果版本"] for r in DashboardService().attribution_panel()["结果"]}
        self.assertEqual(ea_versions, seg_versions, dash_versions)
        self.assertEqual(ea_versions, {version})

    # ------------------------------------------------------------ 启用闸门
    def test_enable_model_requires_complete_snapshot(self) -> None:
        from app.services.eventanalysis import EventanalysisService

        # 模型 1 配置不完整，启用应被拦截且状态不变。
        entry, message = EventanalysisService().run_action(1, "启用模型")
        self.assertIsNone(entry)
        self.assertIn("样本准入", message)
        self.assertEqual(store.find("eventanalysis", 1)["status"], "草稿")
        # 模型 3 配置完整，可以启用。
        entry3, _ = EventanalysisService().run_action(3, "启用模型")
        self.assertIsNotNone(entry3)
        self.assertEqual(entry3["status"], "已启用")


class AttributionHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        store.__init__()  # type: ignore[misc]

    def test_run_retry_and_results_endpoints(self) -> None:
        # 模型 2 样本不足 -> ok=False，带可解释空态。
        resp = call_route("POST", "/api/eventanalysis/2/attribution/run", {})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("最小样本量", body["message"])
        run_id = body["entry"]["id"]

        # 重试接口可调用且仍返回空态。
        retry = call_route("POST", f"/api/eventanalysis/attribution/runs/{run_id}/retry")
        self.assertFalse(retry.json()["ok"])

        # 模型 3 成功发布。
        ok = call_route("POST", "/api/eventanalysis/3/attribution/run", {}).json()
        self.assertTrue(ok["ok"])
        version = ok["entry"]["结果版本"]

        # 三端读取同一版本。
        ea = call_route("GET", "/api/eventanalysis/3/attribution/results").json()
        self.assertEqual({r["结果版本"] for r in ea["结果"]}, {version})
        feed = call_route("GET", "/api/segmentprofile/attribution/feed").json()
        self.assertEqual({r["结果版本"] for r in feed["当前结果"]}, {version})
        panel = call_route("GET", "/api/dashboard/attribution/panel").json()
        self.assertEqual({r["结果版本"] for r in panel["结果"]}, {version})

        # 运行历史可追溯。
        runs = call_route("GET", "/api/eventanalysis/attribution/runs").json()
        self.assertGreaterEqual(runs["total"], 2)

        # 重复提交幂等。
        again = call_route("POST", "/api/eventanalysis/3/attribution/run", {}).json()
        self.assertEqual(again["entry"]["结果版本"], version)

    def test_user_event_stream_endpoint(self) -> None:
        resp = call_route("GET", "/api/userevent?event_name=%E4%B8%8B%E5%8D%95%E6%94%AF%E4%BB%98")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total"], 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
