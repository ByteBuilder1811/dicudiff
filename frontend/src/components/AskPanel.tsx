import { useRef, useState } from 'react'
import { api } from '../services/api'
import type { Answer, Source } from '../types'
import { Button, ErrorBox, loc } from './ui'

const SUGGEST = ['What are the differences between these two versions?', 'Which clauses were removed?', 'Did any threshold or number change?', 'Where is identity verification mentioned?']

function SourceCard({ s, hl, turn, onShow }: { s: Source; hl: boolean; turn: number; onShow: (changeId: string) => void }) {
  const body = s.type === 'passage' ? s.text : `${s.category}: ${s.old?.text ?? '(none)'} → ${s.new?.text ?? '(none)'}`
  return (
    <li id={`src-${turn}-${s.id}`} className={`rounded-lg border p-2.5 text-xs transition [overflow-wrap:anywhere] ${hl ? 'border-brand bg-brand-soft' : 'border-line bg-white'}`}>
      <p className="mb-1 font-mono text-[11px] text-muted"><b className="text-ink">[{s.id}]</b> {s.type === 'passage' ? `${s.version} · ${loc(s.page ?? null, s.section ?? null)}` : 'deterministic change record'}
        {s.scores && <> · bm25 {s.scores.bm25} · dense {s.scores.dense ?? '—'}</>}</p>
      <p className="line-clamp-5 whitespace-pre-line">{body}</p>
      {s.change_id && <button className="mt-1 font-medium text-brand underline" onClick={() => onShow(s.change_id!)}>Show in changes</button>}
    </li>)
}

function Cited({ text, onCite }: { text: string; onCite: (id: string) => void }) {
  return <>{text.split(/(\[E\d+\])/g).map((p, i) => /^\[E\d+\]$/.test(p)
    ? <button key={i} onClick={() => onCite(p.slice(1, -1))} className="mx-0.5 rounded bg-brand-soft px-1 font-mono text-[11px] font-medium text-brand hover:bg-brand hover:text-white">{p}</button> : <span key={i}>{p}</span>)}</>
}

export default function AskPanel({ comparisonId, llmReady, onShowChange }: { comparisonId: string; llmReady: boolean; onShowChange: (changeId: string) => void }) {
  const [q, setQ] = useState(''); const [useLlm, setUseLlm] = useState(llmReady)
  const [turns, setTurns] = useState<Answer[]>([]); const [busy, setBusy] = useState(false); const [err, setErr] = useState(''); const [hl, setHl] = useState('')  // `${turn}-${sourceId}`; source ids (E1..) repeat in every answer, so they are namespaced per turn
  const [opened, setOpened] = useState<Record<number, boolean>>({})
  const end = useRef<HTMLDivElement>(null)
  const ask = async (question: string) => {
    if (question.trim().length < 3) return
    setBusy(true); setErr('')
    try { const a = await api.ask(comparisonId, question, useLlm); setTurns((t) => [...t, a]); setQ(''); setTimeout(() => end.current?.scrollIntoView({ behavior: 'smooth' }), 50) }
    catch (e) { setErr((e as Error).message) }
    setBusy(false)
  }
  const cite = (turn: number, id: string) => {
    setHl(`${turn}-${id}`); setOpened((o) => ({ ...o, [turn]: true }))  // evidence list may be collapsed: open it before scrolling
    setTimeout(() => document.getElementById(`src-${turn}-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 60)
  }
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        {turns.length === 0 && <div className="space-y-2"><p className="text-sm text-muted">Ask about these two documents. Answers cite their sources; you can inspect every retrieved passage.</p>
          {SUGGEST.map((s) => <button key={s} onClick={() => ask(s)} className="block w-full rounded-lg border border-line px-3 py-2 text-left text-sm hover:border-brand">{s}</button>)}</div>}
        {turns.map((t, i) => (
          <div key={i} className="space-y-2">
            <p className="ml-6 rounded-lg bg-canvas px-3 py-2 text-sm font-medium">{t.question}</p>
            <div className={`rounded-lg border p-3 text-sm ${t.sufficient_evidence ? 'border-line' : 'border-amber-300 bg-amber-50'}`}>
              <p className="mb-2 font-mono text-[11px] text-muted">{t.mode === 'llm' ? 'AI-generated answer (verify against sources)' : 'Extractive answer · no language model used'} · retrieval: {t.retrieval.mode_used}</p>
              <p className="whitespace-pre-line leading-relaxed [overflow-wrap:anywhere]"><Cited text={t.answer} onCite={(id) => cite(i, id)} /></p>
              {t.llm_error && <p className="mt-2 rounded bg-zinc-100 p-2 text-xs text-muted">Local LLM unavailable: {t.llm_error}</p>}
              {t.citation_warning && <p className="mt-2 rounded bg-amber-100 p-2 text-xs text-amber-900">{t.citation_warning}</p>}
              {t.retrieval.notes.map((n) => <p key={n} className="mt-1 text-xs text-muted">{n}</p>)}
            </div>
            {t.sources.length > 0 && <details open={opened[i] ?? !t.sufficient_evidence} onToggle={(e) => { const isOpen = e.currentTarget.open; setOpened((o) => (o[i] === isOpen ? o : { ...o, [i]: isOpen })) }}><summary className="cursor-pointer text-xs font-medium text-muted">Retrieved evidence ({t.sources.length})</summary>
              <ul className="mt-2 space-y-2">{t.sources.map((s) => <SourceCard key={s.id} s={s} hl={hl === `${i}-${s.id}`} turn={i} onShow={onShowChange} />)}</ul></details>}
          </div>))}
        {busy && <p className="text-sm text-muted">Searching documents{useLlm ? ' and asking the local model' : ''}…</p>}
        <div ref={end} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); ask(q) }} className="space-y-2 border-t border-line p-3">
        {err && <ErrorBox message={err} />}
        <textarea value={q} onChange={(e) => setQ(e.target.value)} rows={2} maxLength={500} placeholder="Ask a question about the changes…" aria-label="Question"
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(q) } }} className="w-full resize-none rounded-lg border border-line px-3 py-2 text-sm" />
        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-xs text-muted"><input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} className="accent-[#167a4a]" />Use local LLM{!llmReady && ' (unavailable)'}</label>
          <Button disabled={busy || q.trim().length < 3}>Ask</Button>
        </div>
      </form>
    </div>)
}
