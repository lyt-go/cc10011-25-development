import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/dashboard'
const columns = [
  "大盘编号",
  "大盘名称",
  "包含指标组",
  "布局模板",
  "刷新频率秒",
  "对比周期",
  "分享范围",
  "负责人"
]
const actions = [
  "提交大盘评审",
  "发布大盘",
  "下线大盘"
]
const statuses = [
  "草稿",
  "大盘评审中",
  "已发布",
  "已下线"
]
const stats = [
  {
    "label": "大盘配置总数",
    "value": 0
  },
  {
    "label": "已发布大盘",
    "value": 0
  },
  {
    "label": "评审中大盘",
    "value": 0
  }
]

type Row = Record<string, string | number | null>
type PanelResult = Record<string, string | number | string[]>

export default function DashboardView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [panel, setPanel] = useState<PanelResult[] | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('大盘配置列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '监控大盘列表读取失败')
    }
  }, [filters])

  const loadAttributionPanel = useCallback(async () => {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/attribution/panel`)
      if (!response.ok) {
        throw new Error('归因结果面板读取失败')
      }
      const payload = await response.json()
      setPanel(payload.结果 ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '监控大盘归因面板读取失败')
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('监控大盘动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '监控大盘操作失败')
    }
  }

  return (
    <section className="page" data-module="dashboard">
      <header className="page-head">
        <div>
          <h2>监控大盘管理</h2>
          <p className="page-desc">配置实时监控大盘，组合指标卡片和图表形成业务全景视图。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('大盘配置登记入口尚未接入审批流')}>
            登记大盘配置
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出监控大盘清单
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

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
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
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无监控大盘数据，可先登记大盘配置</td>
            </tr>
          )}
        </tbody>
      </table>

      <section className="sub-panel">
        <div className="sub-panel-head">
          <h3>事件归因结果面板（与事件分析、分群画像读取同一结果版本）</h3>
          <button className="btn" type="button" onClick={() => void loadAttributionPanel()}>刷新归因面板</button>
        </div>
        {panel === null ? (
          <p className="empty-state">点击「刷新归因面板」加载已发布的事件归因结果（仅含通过样本准入的分组）。</p>
        ) : panel.length === 0 ? (
          <p className="empty-state">当前没有已发布的归因结果：上游分组未达最小样本量或置信口径，看板不展示未达标数据。</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>结果版本</th><th>模型编号</th><th>归因窗口</th><th>维度取值</th>
                <th>样本量</th><th>均值</th><th>置信区间</th><th>置信水平</th>
              </tr>
            </thead>
            <tbody>
              {panel.map((r, idx) => (
                <tr key={idx}>
                  <td>v{String(r.结果版本)}</td>
                  <td>{String(r.模型编号 ?? '—')}</td>
                  <td>{String(r.归因窗口起点 ?? '—')} ~ {String(r.归因窗口结束 ?? '—')}</td>
                  <td>{String(r.维度取值 ?? '—')}</td>
                  <td>{String(r.样本量 ?? '—')}</td>
                  <td>{String(r.均值 ?? '—')}</td>
                  <td>[{String(r.置信区间下限 ?? '—')}, {String(r.置信区间上限 ?? '—')}]</td>
                  <td>{String(r.置信水平 ?? '—')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <footer className="page-foot">
        <span>共 {total} 条监控大盘记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
