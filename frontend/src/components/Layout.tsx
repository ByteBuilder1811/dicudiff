import { useEffect, useState, type ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { api } from '../services/api'
import type { Health } from '../types'

export default function Layout({ children }: { children: ReactNode }) {
  const [h, setH] = useState<Health | null | 'down'>(null)
  useEffect(() => { api.health().then(setH).catch(() => setH('down')) }, [])
  const nav = 'whitespace-nowrap rounded-md px-2 py-1.5 sm:px-3 text-sm text-muted hover:text-ink aria-[current=page]:bg-brand-soft aria-[current=page]:text-brand aria-[current=page]:font-medium'
  const dot = (ok: boolean) => <span className={`h-2 w-2 rounded-full ${ok ? 'bg-emerald-500' : 'bg-zinc-300'}`} />
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-line bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-3 px-3 py-3 sm:gap-6 sm:px-5">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-brand font-mono text-xs text-white">Δ</span><span className="hidden min-[480px]:inline">DocuDiff</span>
          </Link>
          <nav className="flex min-w-0 gap-1 overflow-x-auto" aria-label="Main">
            <NavLink to="/" end className={nav}>Dashboard</NavLink>
            <NavLink to="/new" className={nav}>New comparison</NavLink>
            <NavLink to="/common-removals" className={nav}>Common removals</NavLink>
          </nav>
          <div className="ml-auto hidden items-center gap-4 font-mono text-[11px] text-muted sm:flex">
            {h === 'down' ? <span className="text-red-700">backend offline</span> : h && (<>
              <span className="flex items-center gap-1.5" title={h.embeddings.reason || h.embeddings.name || ''}>{dot(h.embeddings.available)}embeddings</span>
              <span className="flex items-center gap-1.5" title={h.llm.hint || h.llm.configured_model}>{dot(h.llm.reachable && h.llm.model_installed)}llm: {h.llm.configured_model}</span>
            </>)}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-5 py-8">{children}</main>
      <footer className="mx-auto max-w-7xl px-5 pb-10 font-mono text-[11px] text-muted">100% local · no documents leave this machine · deterministic findings are separated from AI interpretation</footer>
    </div>
  )
}
