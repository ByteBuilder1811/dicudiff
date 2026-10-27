import type { Answer, Change, Comparison, DocumentOut, Health, Paged } from '../types'

export class ApiError extends Error {
  code: string
  status: number
  constructor(code: string, message: string, status: number) { super(message); this.code = code; this.status = status }
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response
  try { res = await fetch(url, init) } catch { throw new ApiError('network', 'Cannot reach the DocuDiff backend. Is it running on port 8000?', 0) }
  if (!res.ok) {
    let d: { code?: string; message?: string } = {}
    try { d = (await res.json()).detail ?? {} } catch { /* non-JSON error */ }
    // A dev/reverse proxy answers 502/503/504 (HTML or empty body) when the backend is down: report it as unreachable, not as a bare status code.
    if (!d.code && [502, 503, 504].includes(res.status)) throw new ApiError('network', 'Cannot reach the DocuDiff backend. Is it running on port 8000?', res.status)
    throw new ApiError(d.code ?? 'error', d.message ?? `Request failed (${res.status})`, res.status)
  }
  return res.json()
}
const json = (method: string, body?: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })

export const api = {
  health: () => req<Health>('/api/health'),
  stats: () => req<{ documents: number; comparisons: number; completed: number; running: number; failed: number; changes_by_category: Record<string, number> }>('/api/stats'),
  upload: (file: File, label: string) => {
    const f = new FormData(); f.append('file', file); if (label) f.append('label', label)
    return req<{ document: DocumentOut; deduplicated: boolean }>('/api/documents/upload', { method: 'POST', body: f })
  },
  documents: () => req<Paged<DocumentOut>>('/api/documents?limit=100'),
  deleteDocument: (id: string) => req(`/api/documents/${id}`, { method: 'DELETE' }),
  createComparison: (a: string, b: string, la: string, lb: string, semantic: boolean) =>
    req<Comparison>('/api/comparisons', json('POST', { doc_a_id: a, doc_b_id: b, label_a: la, label_b: lb, options: { semantic } })),
  comparisons: () => req<Paged<Comparison>>('/api/comparisons?limit=50'),
  comparison: (id: string) => req<Comparison>(`/api/comparisons/${id}`),
  retry: (id: string) => req<Comparison>(`/api/comparisons/${id}/retry`, json('POST')),
  deleteComparison: (id: string) => req(`/api/comparisons/${id}`, { method: 'DELETE' }),
  changes: (id: string, p: { category?: string; q?: string; risk_only?: boolean; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams()
    Object.entries(p).forEach(([k, v]) => v !== undefined && v !== '' && v !== false && qs.set(k, String(v)))
    return req<Paged<Change> & { counts: Record<string, number> }>(`/api/comparisons/${id}/changes?${qs}`)
  },
  review: (cid: string, chid: string, decision: 'confirm' | 'reject') => req<Change>(`/api/comparisons/${cid}/changes/${chid}/review`, json('POST', { decision })),
  explain: (cid: string, chid: string) => req<Change>(`/api/comparisons/${cid}/changes/${chid}/explain`, json('POST')),
  ask: (cid: string, question: string, use_llm: boolean) => req<Answer>(`/api/comparisons/${cid}/questions`, json('POST', { question, use_llm })),
  exportReport: (cid: string, format: 'md' | 'html' | 'pdf', include_unchanged: boolean) =>
    req<{ id: string; download_url: string }>(`/api/comparisons/${cid}/export`, json('POST', { format, include_unchanged })),
  commonRemovals: (a: string, b: string) => req<CommonRemovals>('/api/common-removals', json('POST', { comparison_a_id: a, comparison_b_id: b })),
}

export interface CommonRemovals {
  direction: string; confirmed: number; needs_review: number
  pair_a: { comparison_id: string; removed_total: number }; pair_b: { comparison_id: string; removed_total: number }
  items: { status: string; match_method: string; similarity: number; evidence_a: Ev; evidence_b: Ev }[]
}
interface Ev { text: string; page: number | null; section: string | null; comparison_id: string }
