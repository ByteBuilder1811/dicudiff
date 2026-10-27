import { useEffect, useState } from 'react'
import { api, type CommonRemovals as CR } from '../services/api'
import type { Comparison } from '../types'
import { Button, Card, Empty, ErrorBox, loc } from '../components/ui'

const name = (c: Comparison) => `${c.doc_a_name} → ${c.doc_b_name} · ${c.created_at.slice(0, 16).replace('T', ' ')} · #${c.id.slice(0, 6)}`  // same file names can be compared more than once

// Defined at module level: a component declared inside CommonRemovals is a new type on every render, which remounts the <select> and drops focus/open state.
function Select({ v, set, label, comps }: { v: string; set: (s: string) => void; label: string; comps: Comparison[] }) {
  return (
    <label className="block text-sm"><span className="mb-1 block font-medium">{label}</span>
      <select value={v} onChange={(e) => set(e.target.value)} className="w-full rounded-lg border border-line bg-white px-3 py-2">
        <option value="">Select a completed comparison…</option>{comps.map((c) => <option key={c.id} value={c.id}>{name(c)}</option>)}</select></label>)
}

export default function CommonRemovals() {
  const [comps, setComps] = useState<Comparison[]>([])
  const [a, setA] = useState(''); const [b, setB] = useState('')
  const [res, setRes] = useState<CR | null>(null); const [err, setErr] = useState(''); const [busy, setBusy] = useState(false)
  useEffect(() => { api.comparisons().then((r) => setComps(r.items.filter((c) => c.status === 'completed'))).catch((e) => setErr(e.message)) }, [])
  const pairName = (id: string) => { const c = comps.find((x) => x.id === id); return c ? `${c.doc_a_name} → ${c.doc_b_name}` : id }
  const run = async () => { setBusy(true); setErr(''); try { setRes(await api.commonRemovals(a, b)) } catch (e) { setErr((e as Error).message); setRes(null) } setBusy(false) }
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div><h1 className="text-2xl font-semibold tracking-tight">Commonly removed provisions</h1>
        <p className="max-w-3xl text-sm text-muted">Finds provisions that were removed in <b>both</b> of two independent comparison pairs (A1 → A2 and B1 → B2). A match requires a REMOVED change in each pair with matching text — absence from two unrelated documents is never enough.</p></div>
      {comps.length < 2 && !err && <Card><Empty title="This workflow needs two completed comparison pairs" hint="Upload and compare a second pair of documents (for example another institution's old → new circular), then return here." /></Card>}
      {comps.length >= 2 && <Card className="grid gap-4 p-5 md:grid-cols-[1fr_1fr_auto] md:items-end">
        <Select v={a} set={setA} label="Pair A (A1 → A2)" comps={comps} /><Select v={b} set={setB} label="Pair B (B1 → B2)" comps={comps} />
        <Button onClick={run} disabled={!a || !b || a === b || busy}>{busy ? 'Analysing…' : 'Find common removals'}</Button></Card>}
      {err && <ErrorBox message={err} />}
      {res && <div className="space-y-4">
        <p className="rounded-lg bg-brand-soft px-4 py-2 text-sm text-brand">{res.direction} · <b>{res.confirmed}</b> confirmed, <b>{res.needs_review}</b> semantic candidates to review</p>
        <p className="text-xs text-muted">Pair A: {pairName(res.pair_a.comparison_id)} · Pair B: {pairName(res.pair_b.comparison_id)}</p>
        {res.items.length === 0 && <Card><Empty title="No provision was removed in both pairs" hint={`Pair A removed ${res.pair_a.removed_total} and pair B removed ${res.pair_b.removed_total} provision(s), but none align.`} /></Card>}
        {res.items.map((it, i) => <Card key={i} className="p-4"><div className="mb-3 flex gap-2 text-xs"><span className="rounded-full bg-zinc-100 px-2 py-0.5 font-mono">{it.match_method} · {it.similarity.toFixed(2)}</span>
          {it.status === 'needs_review' && <span className="rounded-full bg-violet-50 px-2 py-0.5 text-violet-800">needs human review</span>}</div>
          <div className="grid gap-3 md:grid-cols-2">{[['Pair A evidence', it.evidence_a], ['Pair B evidence', it.evidence_b]].map(([t, e]) => {
            const ev = e as typeof it.evidence_a; return <div key={t as string} className="min-w-0 rounded-lg border border-red-100 bg-red-50/50 p-3"><p className="mb-1 font-mono text-[11px] text-muted">{t as string} · removed from old · {loc(ev.page, ev.section)}</p><p className="text-sm [overflow-wrap:anywhere]">{ev.text}</p></div> })}</div></Card>)}
      </div>}
    </div>
  )
}
