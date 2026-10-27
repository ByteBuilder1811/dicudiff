import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../services/api'
import { CATEGORIES, type Category, type Change, type Comparison, type Health } from '../types'
import AskPanel from '../components/AskPanel'
import ChangeRow from '../components/ChangeRow'
import { Button, CAT_STYLE, Card, Empty, ErrorBox, Spinner } from '../components/ui'

const PAGE = 50

export default function Workspace() {
  const { id = '' } = useParams()
  const [comp, setComp] = useState<Comparison | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [items, setItems] = useState<Change[]>([]); const [total, setTotal] = useState(0); const [counts, setCounts] = useState<Record<string, number>>({})
  const [active, setActive] = useState<Category[]>(['MODIFIED', 'ADDED', 'REMOVED', 'NEEDS_REVIEW', 'POSSIBLY_RELOCATED'])
  const [q, setQ] = useState(''); const [risk, setRisk] = useState(false); const [limit, setLimit] = useState(PAGE)
  const [loading, setLoading] = useState(false); const [err, setErr] = useState(''); const [tab, setTab] = useState<'changes' | 'ask'>('changes')
  const [exp, setExp] = useState(''); const [expErr, setExpErr] = useState('')

  const loadComp = useCallback(() => api.comparison(id).then(setComp).catch((e) => setErr(e.message)), [id])
  useEffect(() => { loadComp(); api.health().then(setHealth).catch(() => undefined) }, [loadComp])
  useEffect(() => {
    if (!comp || comp.status === 'completed' || comp.status === 'failed') return
    const t = setInterval(loadComp, 1000); return () => clearInterval(t)
  }, [comp, loadComp])

  const done = comp?.status === 'completed'
  const loadChanges = useCallback(() => {
    if (!done) return
    if (active.length === 0) { setItems([]); setTotal(0); return }  // empty category filter must show nothing (API treats a missing filter as "all")
    setLoading(true)
    api.changes(id, { category: active.join(','), q: q.trim(), risk_only: risk, limit }).then((r) => { setItems(r.items); setTotal(r.total); setCounts(r.counts); setErr('') })
      .catch((e) => setErr(e.message)).finally(() => setLoading(false))
  }, [id, done, active, q, risk, limit])
  useEffect(() => { const t = setTimeout(loadChanges, q ? 250 : 0); return () => clearTimeout(t) }, [loadChanges, q])

  const toggle = (c: Category) => { setLimit(PAGE); setActive((a) => (a.includes(c) ? a.filter((x) => x !== c) : [...a, c])) }
  const doExport = async (fmt: 'md' | 'html' | 'pdf') => {
    setExp(fmt); setExpErr('')
    try { const r = await api.exportReport(id, fmt, false)
      // The download is awaited after an async request, so window.open() would run outside the click's user activation and can be
      // popup-blocked (Safari/Firefox). The endpoint answers with Content-Disposition: attachment, so a same-tab anchor download is safe.
      const a = document.createElement('a'); a.href = r.download_url; a.download = ''; document.body.appendChild(a); a.click(); a.remove() } catch (e) { setExpErr((e as Error).message) }
    setExp('')
  }
  const showChange = (cid: string) => {
    setTab('changes')  // on narrow screens the changes list is hidden while the Ask tab is open
    setTimeout(() => document.getElementById(`change-${cid}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 60)
  }
  const retry = async () => { try { setComp(await api.retry(id)) } catch (e) { setErr((e as Error).message) } }

  if (!comp) return err ? <ErrorBox message={err} onRetry={() => { setErr(''); loadComp() }} /> : <Spinner label="Loading comparison" />
  const llmReady = !!health?.llm.reachable && !!health.llm.model_installed
  const s = comp.stats

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0"><Link to="/" className="text-xs text-muted hover:text-ink">← Dashboard</Link>
          <h1 className="truncate text-xl font-semibold tracking-tight">{comp.doc_a_name} <span className="text-muted">→</span> {comp.doc_b_name}</h1>
          <p className="font-mono text-xs text-muted">{comp.label_a} → {comp.label_b}{s.duration_s != null && ` · processed in ${s.duration_s}s`}</p></div>
        {done && <div className="flex items-center gap-2"><span className="text-xs text-muted">Export</span>
          {(['html', 'md', 'pdf'] as const).map((f) => <Button key={f} variant="ghost" disabled={!!exp} onClick={() => doExport(f)}>{exp === f ? '…' : f.toUpperCase()}</Button>)}</div>}
      </div>
      {expErr && <ErrorBox message={expErr} />}

      {(comp.status === 'queued' || comp.status === 'running') && (
        <Card className="p-6"><p className="mb-3 text-sm font-medium">Processing — {comp.stage}</p>
          <div className="h-2 overflow-hidden rounded bg-zinc-100" role="progressbar" aria-valuenow={Math.round(comp.progress * 100)}><div className="h-full bg-brand transition-all" style={{ width: `${Math.max(5, comp.progress * 100)}%` }} /></div></Card>)}
      {comp.status === 'failed' && <ErrorBox message={comp.error ?? 'Comparison failed.'} onRetry={comp.stage === 'invalid_input' ? undefined : retry} />}

      {done && <>
        {comp.limitations.length > 0 && <details className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900"><summary className="cursor-pointer font-medium">{comp.limitations.length} limitation(s) apply to this comparison</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">{comp.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></details>}

        <div role="tablist" className="flex gap-1 border-b border-line lg:hidden">{(['changes', 'ask'] as const).map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`px-4 py-2 text-sm ${tab === t ? 'border-b-2 border-brand font-medium text-brand' : 'text-muted'}`}>{t === 'ask' ? 'Ask AI' : 'Changes'}</button>)}</div>
        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
          <div className={`space-y-4 ${tab === 'ask' ? 'hidden lg:block' : ''}`}>
            <Card className="space-y-3 p-4">
              <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by category">
                {CATEGORIES.map((c) => <button key={c} aria-pressed={active.includes(c)} onClick={() => toggle(c)}
                  className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium transition ${active.includes(c) ? `${CAT_STYLE[c].bg} ${CAT_STYLE[c].fg} border-current/20` : 'border-line text-muted'}`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${CAT_STYLE[c].dot}`} />{CAT_STYLE[c].label}<span className="font-mono">{counts[c] ?? 0}</span></button>)}
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <input type="search" value={q} onChange={(e) => { setQ(e.target.value); setLimit(PAGE) }} placeholder="Search within changes…" aria-label="Search changes" className="min-w-52 flex-1 rounded-lg border border-line px-3 py-1.5 text-sm" />
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={risk} onChange={(e) => { setRisk(e.target.checked); setLimit(PAGE) }} className="accent-[#167a4a]" />High-risk only <span className="text-xs text-muted">(numbers, dates, negations…)</span></label>
              </div>
            </Card>
            {err && <ErrorBox message={err} onRetry={loadChanges} />}
            {loading && items.length === 0 && <Spinner label="Loading changes" />}
            {!loading && items.length === 0 && !err && <Card><Empty title="No changes match these filters" hint={active.length === 0 ? 'Select at least one category.' : 'Try clearing the search or enabling more categories.'} /></Card>}
            <div className="space-y-4">{items.map((c) => <ChangeRow key={c.id} c={c} comp={comp} llmReady={llmReady} onReload={() => { loadChanges(); loadComp() }} onUpdate={(n) => setItems((xs) => xs.map((x) => (x.id === n.id ? n : x)))} />)}</div>
            {items.length < total && <div className="text-center"><Button variant="ghost" onClick={() => setLimit((l) => l + PAGE)}>Show more ({total - items.length} remaining)</Button></div>}
          </div>
          <aside className={`${tab === 'changes' ? 'hidden lg:block' : ''} lg:sticky lg:top-20`}>
            <Card className="h-[calc(100vh-7rem)] min-h-[420px] overflow-hidden">
              <div className="flex items-center justify-between border-b border-line px-4 py-2.5"><span className="font-medium">Ask the documents</span>
                <span className="font-mono text-[11px] text-muted">{llmReady ? health?.llm.configured_model : 'retrieval only'}</span></div>
              <div className="h-[calc(100%-2.75rem)]"><AskPanel comparisonId={id} llmReady={llmReady} onShowChange={showChange} /></div>
            </Card>
          </aside>
        </div>
      </>}
    </div>
  )
}
