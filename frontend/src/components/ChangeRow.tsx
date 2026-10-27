import { useState } from 'react'
import { api } from '../services/api'
import type { Change, Comparison } from '../types'
import { Button, CategoryBadge, loc } from './ui'

const FLAG_LABEL: Record<string, string> = {
  date_changed: 'date', percentage_changed: '%', amount_changed: 'amount', number_changed: 'number', legal_reference_changed: 'legal ref', identifier_changed: 'identifier',
  negation_changed: 'negation', obligation_strength_changed: 'shall/may', renumbered: 'renumbered', relocated: 'moved', punctuation_or_case_only: 'punctuation/case',
  semantic_candidate: 'semantic', low_confidence: 'low confidence', text_changed: 'reworded',
}
const RISK = new Set(['date_changed', 'percentage_changed', 'amount_changed', 'number_changed', 'legal_reference_changed', 'identifier_changed', 'negation_changed', 'obligation_strength_changed'])

function Spans({ spans, fallback, kind }: { spans?: [string, string][]; fallback: string | null; kind: 'old' | 'new' }) {
  if (!spans) return <>{fallback}</>
  return <>{spans.map(([op, t], i) => op === 'eq' ? <span key={i}>{t}</span> : kind === 'old' ? <del key={i} className="d">{t}</del> : <ins key={i} className="i">{t}</ins>)}</>
}

function Confidence({ v }: { v: number | null }) {
  if (v == null || v >= 1) return null
  const tone = v >= 0.75 ? 'bg-emerald-500' : v >= 0.6 ? 'bg-amber-500' : 'bg-red-500'
  return <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-muted" title="Match confidence"><span className="h-1.5 w-12 overflow-hidden rounded bg-zinc-200"><span className={`block h-full ${tone}`} style={{ width: `${v * 100}%` }} /></span>{v.toFixed(2)}</span>
}

export default function ChangeRow({ c, comp, llmReady, onUpdate, onReload }: { c: Change; comp: Comparison; llmReady: boolean; onUpdate: (c: Change) => void; onReload: () => void }) {
  const [busy, setBusy] = useState(''); const [err, setErr] = useState('')
  const act = async (name: string, fn: () => Promise<Change>, reload = false) => {
    setBusy(name); setErr('')
    try { const r = await fn(); onUpdate(r); if (reload) onReload() } catch (e) { setErr((e as Error).message) }
    setBusy('')
  }
  return (
    <article id={`change-${c.id}`} className="overflow-hidden rounded-xl border border-line bg-white">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-canvas/60 px-4 py-2">
        <CategoryBadge c={c.category} />
        <span className="font-mono text-[11px] text-muted">{c.match_method}</span>
        <Confidence v={c.confidence} />
        {c.flags.map((f) => <span key={f} className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${RISK.has(f) ? 'bg-red-100 text-red-800' : 'bg-zinc-100 text-zinc-600'}`}>{FLAG_LABEL[f] ?? f}</span>)}
        <span className="ml-auto font-mono text-[11px] text-muted">#{c.seq + 1}</span>
      </header>
      <div className="grid md:grid-cols-2 md:divide-x md:divide-line">
        {(['old', 'new'] as const).map((side) => {
          const text = side === 'old' ? c.old_text : c.new_text
          const where = side === 'old' ? loc(c.old_page, c.old_section) : loc(c.new_page, c.new_section)
          return (
            <div key={side} className={`min-w-0 px-4 py-3 [overflow-wrap:anywhere] ${text == null ? 'bg-zinc-50/70' : ''}`}>
              <p className="mb-1 font-mono text-[11px] uppercase tracking-wide text-muted">{side === 'old' ? comp.label_a : comp.label_b} · {where}</p>
              {text == null ? <p className="text-sm italic text-muted">{side === 'old' ? 'Not present in the old version' : 'Not present in the new version'}</p>
                : <p className="whitespace-pre-line text-sm leading-relaxed">{c.category === 'REMOVED' ? <del className="d">{text}</del> : c.category === 'ADDED' ? <ins className="i">{text}</ins> : <Spans spans={c.inline_diff?.[side]} fallback={text} kind={side} />}</p>}
            </div>
          )
        })}
      </div>
      <footer className="space-y-3 border-t border-line px-4 py-3 text-sm">
        <p className="text-muted [overflow-wrap:anywhere]"><span className="font-medium text-ink">Why this classification: </span>{c.explanation}</p>
        {c.ai_explanation && (
          <div className="rounded-lg border border-violet-200 bg-violet-50/60 p-3">
            <p className="mb-1 flex items-center gap-2 text-xs font-semibold text-violet-800"><span className="rounded bg-violet-200 px-1.5 py-0.5">AI interpretation</span><span className="font-mono font-normal">{c.ai_explanation.model} · unverified</span></p>
            <p><b>Observed:</b> {c.ai_explanation.observed_change}</p>
            {c.ai_explanation.possible_implications && <p><b>Inference:</b> {c.ai_explanation.possible_implications}</p>}
            {c.ai_explanation.caveats && <p className="text-muted"><b>Caveats:</b> {c.ai_explanation.caveats}</p>}
          </div>
        )}
        {err && <p role="alert" className="text-red-700">{err}</p>}
        <div className="flex flex-wrap gap-2">
          {c.review_status === 'pending' && <>
            <Button variant="ghost" disabled={!!busy} onClick={() => act('c', () => api.review(comp.id, c.id, 'confirm'), true)}>✓ Same provision</Button>
            <Button variant="ghost" disabled={!!busy} onClick={() => act('r', () => api.review(comp.id, c.id, 'reject'), true)}>✕ Not the same</Button></>}
          {c.category !== 'UNCHANGED' && !c.ai_explanation && (
            <Button variant="ghost" disabled={!!busy || !llmReady} title={llmReady ? '' : 'Local LLM not available — see header status'} onClick={() => act('e', () => api.explain(comp.id, c.id))}>
              {busy === 'e' ? 'Asking local model…' : '✦ Explain with local AI'}</Button>)}
        </div>
      </footer>
    </article>
  )
}
