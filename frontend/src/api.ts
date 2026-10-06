export type TokenProvider = () => Promise<string>

export interface Project {
  id: string
  name: string
  role: 'owner' | 'editor' | 'viewer'
  created_at: string
}

export interface CaseRecord {
  id: string
  project_id: string | null
  revision: number
  created_at: string
  updated_at?: string
}

export interface DocumentRecord {
  id: string
  original_name: string
  role: 'requirement' | 'quote' | 'spec'
  supplier_handle: string | null
  model_name: string | null
  parse_status: string
  source_sha256: string
}

export interface DocumentsResult {
  case_id: string
  revision: number
  snapshot_id: string | null
  documents: DocumentRecord[]
}

export interface SnapshotRecord {
  id: string
  revision: number
  created_at: string
  document_count: number
}

export interface SnapshotsResult {
  case_id: string
  snapshots: SnapshotRecord[]
  next_before_revision: number | null
}

export type FieldKey = 'memory' | 'storage' | 'screen_size' | 'weight' |
  'warranty' | 'quantity' | 'unit_price' | 'delivery_days'

export interface RunSummary {
  id: string
  conversation_id: string
  snapshot_id: string
  revision: number
  mode: 'standard' | 'thorough'
  status: string
  question: string
  answer_text: string | null
  error_code: string | null
  created_at: string
  updated_at: string
}

export interface CaseRunsResult {
  case_id: string
  runs: RunSummary[]
  next_offset: number | null
}

export interface ConversationRecord { id: string; case_id: string; created_at: string }
export interface StoredMessage { id: string; role: 'user' | 'assistant'; content: string; run_id: string | null; created_at: string }
export interface ChatRun {
  id: string; conversation_id: string; message_id: string; snapshot_id: string | null
  revision: number; status: string; answer_text: string | null; error_code: string | null
  workflow: string | null; outcome_unknown: boolean; is_current: boolean
}
export interface AssistantCitation { evidence_id: string; quote: string; file_name: string; location: string }
export interface AssistantResult {
  run_id: string; case_id: string; snapshot_id: string | null; revision: number; is_current: boolean
  result: { kind: 'chat' | 'evidence' | 'changes' | 'review' | 'proposal'; answer: string
    citations: AssistantCitation[]; candidate: unknown; changes: unknown[] } | null
}

export interface CheckResult {
  check_result_id: string
  snapshot_id: string
  supplier: string
  field_key: FieldKey
  processing_status: string
  business_status: string | null
  reason_code: string
  evidence_refs: string[]
  reason_evidence_refs: string[]
  fact_ids: string[]
  rule_version: string
  is_formal: boolean
}

export interface RunChecksResult {
  run_id: string
  case_id: string
  snapshot_id: string
  revision: number
  run_status: string
  is_current: boolean
  checks: CheckResult[]
}

export interface RunFact {
  fact_id: string
  raw_value: string
  raw_unit: string
  evidence_refs: string[]
  role: string
  supplier: string | null
}

export interface FactSet {
  fact_set_id: string
  field_key: FieldKey
  supplier: string
  facts: RunFact[]
}

export interface RunFactsResult {
  run_id: string
  case_id: string
  snapshot_id: string
  revision: number
  is_current: boolean
  available: boolean
  fact_sets: FactSet[]
}

export interface EvidenceRecord {
  id: string
  document_id: string
  ordinal: number
  source_text: string
  page_idx: number | null
  bbox: number[] | null
  page_width_pt: number | null
  page_height_pt: number | null
  sheet: string | null
  cell: string | null
  parser_version: string | null
}

export interface CorrectionRecord {
  correction_id: string
  case_id: string
  source_run_id: string
  from_snapshot_id: string
  snapshot_id: string
  revision: number
  review_status: 'pending_recheck' | 'complete' | 'needs_review'
  old_fact: RunFact
  corrected_candidate: { raw_value: string; raw_unit: string }
  corrected_fact: RunFact | null
  check_result: CheckResult | null
  reason: string
}

export async function apiRequest<T = unknown>(
  path: string,
  tokenProvider: TokenProvider,
  init: RequestInit = {},
): Promise<T> {
  const token = await tokenProvider()
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
    credentials: 'omit',
    cache: 'no-store',
  })
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const code = typeof payload === 'object' && payload !== null &&
      'error' in payload && typeof payload.error === 'object' &&
      payload.error !== null && 'code' in payload.error &&
      typeof payload.error.code === 'string'
      ? payload.error.code : `http_${response.status}`
    throw new Error(code)
  }
  return payload as T
}

export async function uploadDocument(
  caseId: string,
  file: File,
  role: DocumentRecord['role'],
  supplierHandle: string,
  supplierName: string,
  expectedRevision: number,
  tokenProvider: TokenProvider,
  modelName = '',
): Promise<{ document_id: string; revision: number }> {
  const form = new FormData()
  form.set('file', file)
  form.set('role', role)
  form.set('expected_revision', String(expectedRevision))
  if (role !== 'requirement') {
    form.set('supplier_handle', supplierHandle.trim())
    form.set('supplier_name', supplierName.trim())
    if (modelName.trim()) form.set('model_name', modelName.trim())
  }
  return apiRequest(`/cases/${caseId}/documents`, tokenProvider, {
    method: 'POST',
    body: form,
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  })
}

export async function readOriginal(
  caseId: string, snapshotId: string, documentId: string,
  tokenProvider: TokenProvider,
): Promise<Blob> {
  const token = await tokenProvider()
  const response = await fetch(
    `/api/v1/cases/${caseId}/snapshots/${snapshotId}/documents/${documentId}/original`,
    { headers: { Authorization: `Bearer ${token}` }, credentials: 'omit', cache: 'no-store' },
  )
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    const code = typeof payload === 'object' && payload !== null &&
      'error' in payload && typeof payload.error === 'object' &&
      payload.error !== null && 'code' in payload.error &&
      typeof payload.error.code === 'string'
      ? payload.error.code : `http_${response.status}`
    throw new Error(code)
  }
  return response.blob()
}

export async function downloadRunExport(
  runId: string,
  format: 'json' | 'xlsx',
  tokenProvider: TokenProvider,
): Promise<Blob> {
  const token = await tokenProvider()
  const response = await fetch(
    `/api/v1/agent-runs/${encodeURIComponent(runId)}/export?format=${format}`,
    { headers: { Authorization: `Bearer ${token}` }, credentials: 'omit', cache: 'no-store' },
  )
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    const code = typeof payload === 'object' && payload !== null &&
      'error' in payload && typeof payload.error === 'object' &&
      payload.error !== null && 'code' in payload.error && typeof payload.error.code === 'string'
      ? payload.error.code : `http_${response.status}`
    throw new Error(code)
  }
  return response.blob()
}
