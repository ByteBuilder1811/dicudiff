import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import type { Comparison } from '../types'
import { CAT_STYLE, Button, Card, Empty, ErrorBox, Spinner } from '../components/ui'

type Stats = Awaited<ReturnType<typeof api.stats>>

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [comps, setComps] = useState<Comparison[] | null>(null)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    Promise.all([api.stats(), api.comparisons()]).then(([s, c]) => { setStats(s); setComps(c.items); setErr('') }).catch((e) => setErr(e.message))
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => {  // poll while anything is processing
    if (!comps?.some((c) => c.status === 'queued' || c.status === 'running')) return
    const t = setInterval(load, 1500); return () => clearInterval(t)
  }, [comps, load])

  const tiles = stats && [
    ['Documents', stats.documents], ['Comparisons', stats.comparisons], ['Processing', stats.running], ['Failed', stats.failed],
  ] as const
  return (
    <div className="space-y-8">
      <div className="relative overflow-hidden rounded-2xl border border-line bg-white px-8 py-10">
        <div className="grid-bg absolute inset-0 opacity-40" aria-hidden />
        <div className="relative max-w-2xl">
          <p className="font-mono text-xs text-brand">LOCAL · EVIDENCE-BACKED · ZERO-COST</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">See exactly what changed between two document versions.</h1>
          <p className="mt-3 text-muted">Upload an older and a newer PDF. DocuDiff aligns every paragraph, classifies each change, and cites the page and clause — no cloud, no API keys.</p>
          <Link to="/new" className="mt-6 inline-block"><Button>Start a comparison →</Button></Link>
        </div>
      </div>

      {err && <ErrorBox message={err} onRetry={load} />}
      {tiles && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {tiles.map(([k, v]) => <Card key={k} className="px-4 py-3"><p className="text-xs text-muted">{k}</p><p className="font-mono text-2xl">{v}</p></Card>)}
        </div>
      )}
      {stats && stats.completed > 0 && (
        <Card className="px-4 py-3">
          <p className="mb-2 text-xs text-muted">All detected changes by category</p>
          <div className="flex flex-wrap gap-x-6 gap-y-1 font-mono text-sm">
            {Object.entries(stats.changes_by_category).filter(([k]) => k !== 'UNCHANGED').map(([k, v]) => (
              <span key={k} className="flex items-center gap-2"><span className={`h-2 w-2 rounded-full ${CAT_STYLE[k as keyof typeof CAT_STYLE].dot}`} />{CAT_STYLE[k as keyof typeof CAT_STYLE].label} {v}</span>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <div className="border-b border-line px-5 py-3 font-medium">Recent comparisons</div>
        {!comps && !err && <div className="p-6"><Spinner /></div>}
        {comps?.length === 0 && <Empty title="No comparisons yet" hint="Upload two versions of a document to see your first evidence-backed diff." action={<Link to="/new"><Button>Upload documents</Button></Link>} />}
        <ul className="divide-y divide-line">
          {comps?.map((c) => (
            <li key={c.id}>
              <Link to={`/comparisons/${c.id}`} className="flex flex-wrap items-center gap-4 px-5 py-3 hover:bg-canvas">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{c.doc_a_name ?? '—'} <span className="text-muted">→</span> {c.doc_b_name ?? '—'}</p>
                  <p className="font-mono text-[11px] text-muted">{new Date(c.created_at + 'Z').toLocaleString()}</p>
                </div>
                {c.status === 'completed' && c.stats.counts && (
                  <p className="font-mono text-xs text-muted">{c.stats.counts.MODIFIED} mod · {c.stats.counts.ADDED} add · {c.stats.counts.REMOVED} rem{(c.stats.pending_review ?? 0) > 0 && <span className="text-violet-700"> · {c.stats.pending_review} to review</span>}</p>
                )}
                <StatusPill c={c} />
              </Link>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

export function StatusPill({ c }: { c: Comparison }) {
  const m = { completed: 'bg-emerald-50 text-emerald-800', failed: 'bg-red-50 text-red-800', running: 'bg-amber-50 text-amber-800', queued: 'bg-zinc-100 text-zinc-600' }[c.status]
  return <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${m}`}>{c.status === 'running' ? `${c.stage} ${Math.round(c.progress * 100)}%` : c.status}</span>
}
