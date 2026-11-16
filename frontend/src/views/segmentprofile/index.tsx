import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/segmentprofile'
const columns = [
  "画像编号",
  "分群名称",
  "筛选规则",
  "画像维度",
  "对比基准",
  "更新周期",
  "覆盖对象数",
  "画像快照"
]
const actions = [
  "提交画像评审",
  "发布画像",
  "下线画像"
]
const statuses = [
  "草稿",
  "画像评审中",
  "已发布",
  "已下线"
]
const stats = [
  {
    "label": "画像定义总数",
    "value": 0
  },
  {
    "label": "已发布画像",
    "value": 0
  },
  {
    "label": "评审中画像",
    "value": 0
  }
]

type Row = Record<string, string | number | null>
type FeedResult = Record<string, string | number | string[]>
type SyncLedger = Record<string, string | number>

export default function SegmentprofileView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [feed, setFeed] = useState<{ 当前结果: FeedResult[]; 同步台账: SyncLedger[] } | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('画像定义列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分群画像列表读取失败')
    }
  }, [filters])

  const loadFeed = useCallback(async () => {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/attribution/feed`)
      if (!response.ok) {
        throw new Error('归因结果同步数据读取失败')
      }
      setFeed(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分群画像同步数据读取失败')
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
        throw new Error('分群画像动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分群画像操作失败')
    }
  }

  return (
    <section className="page" data-module="segmentprofile">
      <header className="page-head">
        <div>
          <h2>分群画像管理</h2>
          <p className="page-desc">管理用户分群的画像定义和对比分析，展示各分群的特征差异。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('画像定义登记入口尚未接入审批流')}>
            登记画像定义
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出分群画像清单
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无分群画像数据，可先登记画像定义</td>
            </tr>
          )}
        </tbody>
      </table>

      <section className="sub-panel">
        <div className="sub-panel-head">
          <h3>事件归因结果同步（下游画像消费，与事件分析、看板同一结果版本）</h3>
          <button className="btn" type="button" onClick={() => void loadFeed()}>刷新同步数据</button>
        </div>
        {feed === null ? (
          <p className="empty-state">点击「刷新同步数据」查看分群画像消费的归因结果数据集与同步台账。</p>
        ) : (
          <>
            <h4>当前消费的归因结果（{feed.当前结果.length} 条）</h4>
            {feed.当前结果.length === 0 ? (
              <p className="empty-state">暂无可消费的归因结果：上游分组未达最小样本量或尚未发布，画像不会收到未达标结果。</p>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>结果版本</th><th>模型编号</th><th>维度取值</th><th>样本量</th>
                    <th>均值</th><th>置信区间下限</th><th>置信区间上限</th><th>样本判定</th>
                  </tr>
                </thead>
                <tbody>
                  {feed.当前结果.map((r, idx) => (
                    <tr key={idx}>
                      <td>v{String(r.结果版本)}</td>
                      <td>{String(r.模型编号 ?? '—')}</td>
                      <td>{String(r.维度取值 ?? '—')}</td>
                      <td>{String(r.样本量 ?? '—')}</td>
                      <td>{String(r.均值 ?? '—')}</td>
                      <td>{String(r.置信区间下限 ?? '—')}</td>
                      <td>{String(r.置信区间上限 ?? '—')}</td>
                      <td>{String(r.样本判定 ?? '—')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <h4>同步台账（{feed.同步台账.length} 条，重复回写幂等）</h4>
            {feed.同步台账.length === 0 ? (
              <p className="empty-state">暂无同步台账记录。</p>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>同步编号</th><th>结果版本</th><th>模型编号</th><th>维度取值</th>
                    <th>样本量</th><th>样本判定</th><th>同步时间</th>
                  </tr>
                </thead>
                <tbody>
                  {feed.同步台账.map((r) => (
                    <tr key={String(r.id ?? r.同步编号)}>
                      <td>{String(r.同步编号 ?? '—')}</td>
                      <td>v{String(r.结果版本 ?? '—')}</td>
                      <td>{String(r.模型编号 ?? '—')}</td>
                      <td>{String(r.维度取值 ?? '—')}</td>
                      <td>{String(r.样本量 ?? '—')}</td>
                      <td>{String(r.样本判定 ?? '—')}</td>
                      <td>{String(r.同步时间 ?? '—')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </section>

      <footer className="page-foot">
        <span>共 {total} 条分群画像记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
