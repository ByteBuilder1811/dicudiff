export type Category = 'ADDED' | 'REMOVED' | 'MODIFIED' | 'UNCHANGED' | 'POSSIBLY_RELOCATED' | 'NEEDS_REVIEW'
export const CATEGORIES: Category[] = ['MODIFIED', 'ADDED', 'REMOVED', 'NEEDS_REVIEW', 'POSSIBLY_RELOCATED', 'UNCHANGED']

export interface DocumentOut {
  id: string; original_name: string; label: string | null; status: 'ready' | 'needs_ocr' | 'failed' | 'processing'
  error: string | null; page_count: number; size_bytes: number; sha256: string; warnings: string[]; created_at: string
}
export interface Comparison {
  id: string; doc_a_id: string; doc_b_id: string; label_a: string; label_b: string
  status: 'queued' | 'running' | 'completed' | 'failed'; stage: string; progress: number; error: string | null
  stats: { counts?: Record<Category, number>; critical_changes?: number; pending_review?: number; duration_s?: number }
  limitations: string[]; created_at: string; doc_a_name?: string; doc_b_name?: string
}
export interface Change {
  id: string; seq: number; category: Category; old_text: string | null; new_text: string | null
  old_page: number | null; new_page: number | null; old_section: string | null; new_section: string | null
  match_method: string; confidence: number | null; explanation: string; flags: string[]
  inline_diff: { old: [string, string][]; new: [string, string][] } | null; review_status: string
  ai_explanation: null | { observed_change: string; possible_implications: string; needs_human_review: boolean; caveats: string; model: string }
}
export interface Paged<T> { total: number; limit: number; offset: number; items: T[] }
export interface Source {
  id: string; type: 'change' | 'passage'; version?: string; page?: number; section?: string | null; text?: string
  category?: Category; change_id?: string; scores?: Record<string, number | null>
  old?: { text: string; page: number; section: string | null; version: string } | null
  new?: { text: string; page: number; section: string | null; version: string } | null
}
export interface Answer {
  question: string; answer: string; sources: Source[]; mode: 'llm' | 'extractive'; sufficient_evidence: boolean
  llm_error: string | null; citation_warning: string | null; retrieval: { mode_used: string; notes: string[] }
}
export interface Health {
  status: string
  llm: { reachable: boolean; model_installed: boolean; configured_model: string; hint: string }
  embeddings: { available: boolean; name: string | null; semantic: boolean; reason: string }
  limits: { max_upload_mb: number }
}
