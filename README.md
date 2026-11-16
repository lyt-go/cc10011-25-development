# 实时指标计算与监控大盘平台

面向实时指标计算引擎、多维度下钻分析、告警阈值管理、指标异常检测、数据服务开放与可视化看板配置的数据分析与应用工程平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 实时指标 | `realtimemetric` | 指标定义 | 指标编号、指标名称、计算窗口秒 |
| 监控大盘 | `dashboard` | 大盘配置 | 大盘编号、大盘名称、包含指标组 |
| 多维下钻 | `drilldown` | 下钻路径 | 路径编号、起始维度、下钻层级序列 |
| 阈值管理 | `threshold` | 阈值规则 | 规则编号、关联指标、阈值下限 |
| 异常检测 | `anomalydetect` | 检测模型 | 模型编号、监控指标、检测算法 |
| 数据服务 | `dataservice` | 服务接口 | 服务编号、服务名称、接口路径 |
| 告警历史 | `alarmhistory` | 告警记录 | 记录编号、告警规则、触发时间 |
| 同比环比 | `comparison` | 对比配置 | 配置编号、目标指标、对比类型 |
| 聚合配置 | `aggregate` | 聚合规则 | 规则编号、源数据粒度、目标粒度 |
| 筛选预设 | `filterpreset` | 预设模板 | 预设编号、预设名称、维度条件组 |
| 用户洞察 | `userinsight` | 洞察报告 | 报告编号、分析主题、目标人群 |
| 事件分析 | `eventanalysis` | 分析模型 | 模型编号、事件名称、分组维度、归因窗口、置信水平、最小样本量 |
| 事件归因 | `services/attribution.py` | 归因结果数据集 | 结果版本、唯一事件编号、样本量、置信区间、样本判定 |
| 用户事件流 | `userevent` | 用户事件 | 事件编号、用户编号、首入分区、度量值 |
| 分群画像 | `segmentprofile` | 画像定义 | 画像编号、分群名称、筛选规则、归因结果版本 |
| 趋势预测 | `trendforecast` | 预测方案 | 方案编号、预测指标、算法模型 |
| 可视化模板 | `viztemplate` | 图表模板 | 模板编号、图表类型、数据绑定 |
| 订阅推送 | `subscription` | 订阅配置 | 订阅编号、订阅内容、接收人 |
| 维度映射 | `dimensionmap` | 映射规则 | 规则编号、原始维度名、标准维度名 |
| 数据质量分 | `qualityscore` | 评分规则 | 规则编号、数据集名称、完整性权重 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.tsx`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 事件归因去重与样本准入

事件分析模型的归因窗口会重叠，同一用户事件可能落入多个窗口/分组。归因链路
（`backend/app/services/attribution.py`）保证同一事件只进入一次结果、未达样本门槛的结果不下发：

1. **启用前固化配置**：模型启用前校验并保存事件名称、分组维度、度量指标、时间粒度、
   归因窗口、置信水平、最小样本量七项；缺项或数值非法则拦截启用。
2. **按唯一事件编号去重**：同一 `事件编号` 在「首入分区」与「窗口结束分区」冲突时，以
   首入分区为唯一锚点，全局只保留第一条；重复记录只进运行台账，不进结果数据集。
   两种做法的权衡见 `docs/adr-001-事件归属冲突-首入分区去重.md`。
3. **样本准入 + 置信区间**：按归因窗口归并后逐分组计算样本量与正态近似置信区间；
   只有样本量 ≥ 最小样本量的分组才写入 `attributionresult`，未达标分组不保存、不下发，
   并在运行记录里留下可解释空态与判定明细。
4. **不可变版本 + 幂等**：结果按版本只追加、不覆盖已可用数据集；同一模型快照（配置哈希）
   重复提交只返回已发布版本。事件分析、分群画像、监控大盘统一经
   `attribution_service.current_results()` 读取同一个当前结果版本。
5. **重试与恢复**：样本不足、窗口重叠冲突、配置无效、中断都标记为可重试；重试从上一完整
   版本之后继续。分群画像沿「归因提交 → 分群画像服务」调用链幂等回写（`segmentsync` 台账）。

### 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/userevent` | 用户事件流（归因输入，支持按事件名称/编号过滤） |
| POST | `/api/eventanalysis/{id}/attribution/run` | 运行归因，返回运行记录与空态说明 |
| POST | `/api/eventanalysis/attribution/runs/{run_id}/retry` | 失败/空态后重试 |
| GET | `/api/eventanalysis/{id}/attribution/results` | 事件分析侧读当前结果版本 |
| GET | `/api/eventanalysis/attribution/runs` | 归因运行历史（样本判定、置信区间可追溯） |
| GET | `/api/segmentprofile/attribution/feed` | 分群画像消费的结果与同步台账 |
| GET | `/api/dashboard/attribution/panel` | 看板读取同一结果版本 |

### 测试

```bash
cd backend
.venv/bin/python -m app.tests.test_attribution   # 标准库 unittest，无需额外依赖
```

领域：数据工程
子方向：数据分析与数据应用
