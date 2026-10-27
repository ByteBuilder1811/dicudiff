import type { ReactNode } from 'react'
import type { Category } from '../types'

export const CAT_STYLE: Record<Category, { bg: string; fg: string; dot: string; label: string }> = {
  MODIFIED: { bg: 'bg-amber-50', fg: 'text-amber-800', dot: 'bg-amber-500', label: 'Modified' },
  ADDED: { bg: 'bg-emerald-50', fg: 'text-emerald-800', dot: 'bg-emerald-500', label: 'Added' },
  REMOVED: { bg: 'bg-red-50', fg: 'text-red-800', dot: 'bg-red-500', label: 'Removed' },
  NEEDS_REVIEW: { bg: 'bg-violet-50', fg: 'text-violet-800', dot: 'bg-violet-500', label: 'Needs review' },
  POSSIBLY_RELOCATED: { bg: 'bg-sky-50', fg: 'text-sky-800', dot: 'bg-sky-500', label: 'Relocated?' },
  UNCHANGED: { bg: 'bg-zinc-100', fg: 'text-zinc-600', dot: 'bg-zinc-400', label: 'Unchanged' },
}

export function CategoryBadge({ c }: { c: Category }) {
  const s = CAT_STYLE[c]
  return <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${s.bg} ${s.fg}`}><span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />{s.label}</span>
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-line bg-white shadow-[0_1px_0_rgba(23,32,27,0.03)] ${className}`}>{children}</section>
}

export function Button({ children, variant = 'primary', ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' }) {
  const v = { primary: 'bg-brand text-white hover:bg-[#126a40]', ghost: 'border border-line bg-white text-ink hover:bg-canvas', danger: 'border border-red-200 text-red-700 hover:bg-red-50' }[variant]
  return <button {...p} className={`inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${v} ${p.className ?? ''}`}>{children}</button>
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start justify-between gap-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      <span>{message}</span>{onRetry && <button onClick={onRetry} className="font-medium underline">Retry</button>}
    </div>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return <div role="status" className="flex items-center gap-2 text-sm text-muted"><span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-brand" />{label}…</div>
}

export function Empty({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return <div className="flex flex-col items-center gap-2 px-6 py-12 text-center"><p className="font-medium">{title}</p>{hint && <p className="max-w-sm text-sm text-muted">{hint}</p>}{action}</div>
}

export const fmtBytes = (n: number) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`)
export const loc = (p: number | null, s: string | null) => (p == null ? '—' : `p.${p}${s ? ` · §${s}` : ''}`)
