import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/eventanalysis'
const columns = [
  "模型编号",
  "事件名称",
  "分组维度",
  "度量指标",
  "时间粒度",
  "归因窗口",
  "置信水平",
  "最小样本量"
]
const actions = [
  "提交模型评审",
  "启用模型",
  "归档模型"
]
const statuses = [
  "草稿",
  "模型评审中",
  "已启用",
  "已归档"
]
const stats = [
  {
    "label": "分析模型总数",
    "value": 0
  },
  {
    "label": "已启用模型",
    "value": 0
  },
  {
    "label": "评审中模型",
    "value": 0
  }
]

type Row = Record<string, string | number | null>
type Judgment = Record<string, string | number>
type RunRecord = {
  id: number
  运行编号: string
  状态: string
  可重试: boolean
  空态说明: string
  结果版本: number | null
  输入事件数: number
  去重事件数: number
  丢弃重复数: number
  分组判定: Judgment[]
}
type ResultRow = Record<string, string | number | string[]>

export default function EventanalysisView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [activeModel, setActiveModel] = useState<number | null>(null)
  const [results, setResults] = useState<ResultRow[]>([])
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('分析模型列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '事件分析列表读取失败')
    }
  }, [filters])

  const loadRuns = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/attribution/runs`)
      if (response.ok) {
        const payload = await response.json()
        setRuns(payload.items ?? [])
      }
    } catch {
      // 运行历史是辅助信息，读取失败不阻断主列表。
    }
  }, [])

  useEffect(() => {
    void reload()
    void loadRuns()
  }, [reload, loadRuns])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('事件分析动作未生效，请稍后重试')
      }
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message)
      } else {
        setNotice(payload.message)
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '事件分析操作失败')
    }
  }

  async function runAttribution(row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/attribution/run`, {
        method: 'POST',
        body: JSON.stringify({}),
      })
      const payload = await response.json()
      if (payload.ok) {
        setNotice(payload.message)
      } else {
        // 样本不足/窗口冲突等空态：给出可解释说明，仍可在下方运行历史里重试。
        setErrorMessage(payload.message)
      }
      await loadRuns()
      if (activeModel === Number(row.id)) {
        await loadResults(Number(row.id))
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '归因运行失败')
    }
  }

  async function retryRun(runId: number) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/attribution/runs/${runId}/retry`, {
        method: 'POST',
      })
      const payload = await response.json()
      setNotice(payload.ok ? payload.message : '')
      setErrorMessage(payload.ok ? '' : payload.message)
      await loadRuns()
      if (activeModel !== null) {
        await loadResults(activeModel)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '归因重试失败')
    }
  }

  async function loadResults(modelId: number) {
    setActiveModel(modelId)
    try {
      const response = await request(`${ENDPOINT}/${modelId}/attribution/results`)
      const payload = await response.json()
      setResults(payload.ok ? payload.结果 ?? [] : [])
    } catch {
      setResults([])
    }
  }

  const resultColumns = [
    '结果版本', '归因窗口起点', '归因窗口结束', '维度取值', '样本量',
    '均值', '置信区间下限', '置信区间上限', '置信水平', '样本判定'
  ]

  return (
    <section className="page" data-module="eventanalysis">
      <header className="page-head">
        <div>
          <h2>事件分析管理</h2>
          <p className="page-desc">定义事件驱动的分析模型，按归因窗口去重归并事件，只有通过样本准入的结果才会发布给下游画像与看板。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('分析模型登记入口尚未接入审批流')}>
            登记分析模型
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出事件分析清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      {notice && <p className="notice-text">{notice}</p>}
      {errorMessage && <p className="error-text">{errorMessage}</p>}

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
            <th>事件归因</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void runAttribution(row)}>运行归因</button>
                <button className="link" type="button" onClick={() => void loadResults(Number(row.id))}>查看结果</button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无事件分析数据，可先登记分析模型</td>
            </tr>
          )}
        </tbody>
      </table>

      {activeModel !== null && (
        <section className="sub-panel">
          <h3>事件归因结果数据集（模型 id={activeModel}，与分群画像、看板同版本）</h3>
          {results.length === 0 ? (
            <p className="empty-state">
              当前模型没有已发布的归因结果：可能是全部分组未达最小样本量，或尚未运行归因。请先运行，样本积累后可重试。
            </p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>{resultColumns.map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {results.map((r, idx) => (
                  <tr key={idx}>
                    {resultColumns.map((c) => <td key={c}>{String(r[c] ?? '—')}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      <section className="sub-panel">
        <h3>归因运行历史（样本判定与置信区间可追溯，失败可重试）</h3>
        {runs.length === 0 ? (
          <p className="empty-state">暂无归因运行记录，点击「运行归因」生成第一版结果。</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>运行编号</th><th>状态</th><th>结果版本</th><th>输入/去重/丢弃</th>
                <th>样本判定摘要</th><th>空态说明</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>{run.运行编号}</td>
                  <td>{run.状态}</td>
                  <td>{run.结果版本 === null || run.结果版本 === undefined ? '—' : `v${run.结果版本}`}</td>
                  <td>{run.输入事件数} / {run.去重事件数} / {run.丢弃重复数}</td>
                  <td>
                    {(run.分组判定 ?? []).slice(0, 3).map((j, i) => (
                      <span key={i} className="judgment-chip">
                        {String(j.维度取值)} n={String(j.样本量)}（{String(j.样本判定)}）
                      </span>
                    ))}
                  </td>
                  <td>{run.空态说明}</td>
                  <td className="row-actions">
                    {run.可重试 && (
                      <button className="link" type="button" onClick={() => void retryRun(run.id)}>重试</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <footer className="page-foot">
        <span>共 {total} 条事件分析记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
