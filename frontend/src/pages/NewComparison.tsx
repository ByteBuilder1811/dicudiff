import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../services/api'
import type { DocumentOut } from '../types'
import { Button, Card, ErrorBox, fmtBytes, Spinner } from '../components/ui'

type Slot = { doc?: DocumentOut; busy: boolean; error?: string; dedup?: boolean; label: string }

function DropSlot({ title, slot, onFile, onLabel }: { title: string; slot: Slot; onFile: (f: File) => void; onLabel: (s: string) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const d = slot.doc
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="font-medium">{title}</h2>
        <input aria-label={`${title} label`} value={slot.label} onChange={(e) => onLabel(e.target.value)} maxLength={80}
          className="w-40 rounded-md border border-line px-2 py-1 text-sm" />
      </div>
      <div
        role="button" tabIndex={0} aria-label={`Upload ${title} PDF`}
        onClick={() => input.current?.click()} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) onFile(f) }}
        className={`grid min-h-36 cursor-pointer place-items-center rounded-lg border-2 border-dashed px-4 py-6 text-center transition ${over ? 'border-brand bg-brand-soft' : 'border-line hover:border-brand/60'}`}>
        <input ref={input} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
        {slot.busy ? <Spinner label="Uploading and extracting text" /> : d ? (
          <div className="space-y-1 text-sm">
            <p className="font-medium">{d.original_name}</p>
            <p className="font-mono text-xs text-muted">{d.page_count} pages · {fmtBytes(d.size_bytes)}</p>
            <p className={`text-xs font-medium ${d.status === 'ready' ? 'text-emerald-700' : 'text-red-700'}`}>
              {d.status === 'ready' ? '✓ text extracted' : d.status === 'needs_ocr' ? '⚠ no text layer (scanned)' : '✕ extraction failed'}</p>
            {slot.dedup && <p className="text-xs text-muted">Already uploaded earlier — reusing the stored copy.</p>}
          </div>
        ) : <div className="text-sm text-muted"><p className="font-medium text-ink">Drop a PDF here or click to browse</p><p>Digitally generated PDFs, up to 25 MB</p></div>}
      </div>
      {slot.error && <div className="mt-3"><ErrorBox message={slot.error} /></div>}
      {d && (d.warnings.length > 0 || d.error) && (
        <ul className="mt-3 space-y-1 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">{[d.error, ...d.warnings].filter(Boolean).map((w, i) => <li key={i}>{w}</li>)}</ul>
      )}
    </Card>
  )
}

export default function NewComparison() {
  const nav = useNavigate()
  const [a, setA] = useState<Slot>({ busy: false, label: 'Version A' })
  const [b, setB] = useState<Slot>({ busy: false, label: 'Version B' })
  const [semantic, setSemantic] = useState(true)
  const [err, setErr] = useState('')
  const [starting, setStarting] = useState(false)

  const up = async (f: File, slot: Slot, set: (s: Slot) => void) => {
    set({ ...slot, busy: true, error: undefined })
    try { const r = await api.upload(f, slot.label); set({ ...slot, busy: false, doc: r.document, dedup: r.deduplicated }) }
    catch (e) { set({ ...slot, busy: false, doc: undefined, error: (e as Error).message }) }
  }
  const ready = a.doc?.status === 'ready' && b.doc?.status === 'ready' && a.doc.id !== b.doc.id
  const start = async () => {
    setStarting(true); setErr('')
    try { const c = await api.createComparison(a.doc!.id, b.doc!.id, a.label || 'Version A', b.label || 'Version B', semantic); nav(`/comparisons/${c.id}`) }
    catch (e) { setErr((e as Error).message); setStarting(false) }
  }
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div><h1 className="text-2xl font-semibold tracking-tight">New comparison</h1>
        <p className="text-sm text-muted">Version A is the <b>older</b> document and Version B the <b>newer</b>; changes are reported as A → B.</p></div>
      <div className="grid gap-4 md:grid-cols-2">
        <DropSlot title="Older document" slot={a} onFile={(f) => up(f, a, setA)} onLabel={(label) => setA({ ...a, label })} />
        <DropSlot title="Newer document" slot={b} onFile={(f) => up(f, b, setB)} onLabel={(label) => setB({ ...b, label })} />
      </div>
      {a.doc && b.doc && a.doc.id === b.doc.id && <ErrorBox message="Both slots contain the same file. Choose two different documents." />}
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={semantic} onChange={(e) => setSemantic(e.target.checked)} className="mt-1 accent-[#167a4a]" />
        <span>Run local semantic matching <span className="text-muted">— proposes reworded provisions for human review (needs the embedding model; skipped automatically if unavailable).</span></span></label>
      {err && <ErrorBox message={err} />}
      <Button onClick={start} disabled={!ready || starting}>{starting ? 'Starting…' : 'Compare documents'}</Button>
    </div>
  )
}
