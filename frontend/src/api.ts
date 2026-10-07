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
  supplier_name: string | null
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
export interface MessagesPage {
  conversation_id: string
  total: number
  messages: StoredMessage[]
  next_offset: number | null
}
export interface ChatRun {
  id: string; conversation_id: string; message_id: string; snapshot_id: string | null
  revision: number; status: string; answer_text: string | null; error_code: string | null
  workflow: string | null; outcome_unknown: boolean; is_current: boolean
}
export interface AssistantCitation { evidence_id: string; quote: string; file_name: string; location: string }
export type RuleType = 'numeric' | 'money' | 'enum' | 'presence' | 'duration' | 'manual'

export interface ChecklistRule {
  type: RuleType
  operator?: 'eq' | 'gte' | 'lte' | 'gt' | 'lt' | 'between' | 'in'
  value?: string | null
  lower?: string | null
  upper?: string | null
  unit?: string
  currency?: 'CNY' | 'USD' | 'EUR'
  per_unit?: string
  tax_basis?: 'included' | 'excluded' | 'unspecified'
  values?: string[]
  description?: string
  must_be_present?: boolean
  calendar?: 'natural_days' | 'working_days' | 'months'
  start_event?: string
  instruction?: string
}

export interface EvidenceContextRecord { evidence_refs: string[]; quote: string }

export interface ChecklistCriterion {
  criterion_id: string
  item_id: string
  name: string
  rule: ChecklistRule
  source: { kind: 'user_defined'; reason: string; context?: EvidenceContextRecord | null }
        | { kind: 'document_extracted'; context: EvidenceContextRecord }
  required: boolean
  enabled: boolean
  conditions: string[]
}

export interface ChecklistItem { item_id: string; name: string; specification?: string }

export interface ChecklistDefinitionPayload { items: ChecklistItem[]; criteria: ChecklistCriterion[] }

export interface ChecklistIssue { criterion_id: string; code: string }

export interface ChecklistVersionRecord {
  id: string
  version: number
  snapshot_id: string
  revision: number
  definition: ChecklistDefinitionPayload
  confirmed_by: string
  reason: string
  manual_review_ids: string[]
}

export type ChecklistState = 'empty' | 'draft' | 'needs_resolution' | 'confirmed'
  | 'partially_confirmed' | 'needs_reconfirmation' | 'historical'

export interface ChecklistView {
  case_id: string
  snapshot_id: string
  revision: number
  is_current_snapshot: boolean
  checklist_revision: number
  state: ChecklistState
  draft: ChecklistDefinitionPayload | null
  confirmed_version: ChecklistVersionRecord | null
  previous_version_id: string | null
  issues: ChecklistIssue[]
  execution_available: boolean
  excluded_criterion_ids?: string[]
}

export interface CriterionChange {
  op: 'add' | 'update' | 'remove'
  criterion_id: string
  reason: string
  before: ChecklistCriterion | null
  after: ChecklistCriterion | null
}

export interface AssistantResult {
  run_id: string; case_id: string; snapshot_id: string | null; revision: number; is_current: boolean
  result: { kind: 'chat' | 'evidence' | 'changes' | 'review' | 'proposal'; answer: string
    citations: AssistantCitation[]; candidate: ChecklistDefinitionPayload | null
    changes: CriterionChange[] } | null
}

export interface DynamicCheckResult {
  criterion_id: string
  item_id: string
  item_name: string
  criterion_name: string
  supplier: string
  field_key: string
  processing_status: string
  business_status: string | null
  reason_code: string
  evidence_refs: string[]
  reason_evidence_refs: string[]
  fact_ids: string[]
  rule_version: string
  is_formal: boolean
  checklist_version_id?: string
}

export interface RunDynamicChecksResult {
  run_id: string
  case_id: string
  snapshot_id: string
  revision: number
  run_status: string
  is_current: boolean
  checklist_version_id: string
  payload_digest: string | null
  checks: DynamicCheckResult[]
}

export type AnyRunChecksResult = RunChecksResult | RunDynamicChecksResult

export function isDynamicChecks(result: AnyRunChecksResult): result is RunDynamicChecksResult {
  return typeof (result as RunDynamicChecksResult).checklist_version_id === 'string'
}

export interface DynamicFactRecord {
  fact_id: string
  raw_value: string | boolean
  raw_unit: string
  value: string | boolean
  unit: string
  evidence_refs: string[]
  quote: string
  role: string
  supplier: string | null
  criterion_id?: string
  item_id?: string
  item_name?: string
  criterion_name?: string
  currency?: string | null
  per_unit?: string | null
  tax_basis?: string | null
  calendar?: string | null
  start_event?: string | null
}

export interface DynamicFactSet {
  fact_set_id: string
  supplier: string
  field_key: string
  criterion_id: string
  rule: ChecklistRule
  facts: DynamicFactRecord[]
}

export interface DynamicFactsResult {
  run_id: string
  case_id: string
  snapshot_id: string
  revision: number
  is_current: boolean
  available: boolean
  checklist_version_id: string
  payload_digest: string | null
  fact_sets: DynamicFactSet[]
}

export interface DynamicProgressResult {
  run_id: string
  workflow: string
  run_status: string
  checklist_version_id: string
  validated_batches: number
  planned_batches: number
  coverage: Record<string, boolean>
}

export interface DynamicCorrectionRecord {
  correction_id: string
  run_id: string
  review_status: string
  payload_digest: string
}

export interface DynamicCorrectionAuditRecord {
  correction_id: string
  reason: string
  old_fact: DynamicFactRecord | null
  corrected_fact: DynamicFactRecord | null
  review_status: string
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

export interface MemoryRecord { key: string; value: string; expires_at: string | null }
export interface MemoryProfile { revision: number; records: MemoryRecord[] }

export const MEMORY_PREFERENCE_LABELS: Record<string, Record<string, string>> = {
  answer_length: { concise: '简洁', standard: '标准', detailed: '详细' },
  answer_format: { paragraph: '段落', bullets: '分条', table: '表格' },
  language: { 'zh-CN': '中文', en: 'English' },
}
export const MEMORY_KEY_LABELS: Record<string, string> = {
  answer_length: '答复长度', answer_format: '答复格式', language: '答复语言',
}

export async function fetchMemoryProfile(tokenProvider: TokenProvider): Promise<MemoryProfile> {
  return apiRequest('/me/memories', tokenProvider)
}

export async function saveMemoryPreference(
  key: string,
  body: { value: string; expected_revision: number; confirmed: true; expires_at: string | null },
  tokenProvider: TokenProvider,
): Promise<MemoryProfile> {
  return apiRequest(`/me/memories/${encodeURIComponent(key)}`, tokenProvider, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export async function deleteMemoryPreference(
  key: string,
  expectedRevision: number,
  tokenProvider: TokenProvider,
): Promise<MemoryProfile> {
  return apiRequest(`/me/memories/${encodeURIComponent(key)}?expected_revision=${expectedRevision}`, tokenProvider, {
    method: 'DELETE',
  })
}

export interface ChecklistProposal {
  run_id: string
  case_id: string
  snapshot_id: string
  revision: number
  definition: ChecklistDefinitionPayload | null
  changes: CriterionChange[]
  requires_user_confirmation: boolean
  instruction?: string
}

export async function fetchChecklist(
  caseId: string, snapshotId: string, tokenProvider: TokenProvider,
): Promise<ChecklistView> {
  return apiRequest(`/cases/${caseId}/snapshots/${snapshotId}/checklist`, tokenProvider)
}

export async function saveChecklistDraft(
  caseId: string,
  snapshotId: string,
  definition: ChecklistDefinitionPayload,
  expectedRevision: number,
  expectedChecklistRevision: number,
  reason: string,
  tokenProvider: TokenProvider,
): Promise<ChecklistView> {
  return apiRequest(`/cases/${caseId}/snapshots/${snapshotId}/checklist`, tokenProvider, {
    method: 'PUT',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ ...definition, expected_revision: expectedRevision,
      expected_checklist_revision: expectedChecklistRevision, reason }),
  })
}

export async function confirmChecklist(
  caseId: string,
  snapshotId: string,
  criterionIds: string[],
  expectedRevision: number,
  expectedChecklistRevision: number,
  reason: string,
  tokenProvider: TokenProvider,
): Promise<ChecklistView> {
  return apiRequest(`/cases/${caseId}/snapshots/${snapshotId}/checklist/confirm`, tokenProvider, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ criterion_ids: criterionIds, confirmed: true,
      expected_revision: expectedRevision,
      expected_checklist_revision: expectedChecklistRevision, reason }),
  })
}

export async function fetchChecklistVersion(
  caseId: string, versionId: string, tokenProvider: TokenProvider,
): Promise<ChecklistVersionRecord> {
  return apiRequest(`/cases/${caseId}/checklist-versions/${versionId}`, tokenProvider)
}

export async function previewChecklistRebase(
  caseId: string, snapshotId: string, versionId: string, expectedRevision: number,
  tokenProvider: TokenProvider,
): Promise<{ case_id: string; snapshot_id: string; revision: number;
  source_version_id: string; definition: ChecklistDefinitionPayload }> {
  const query = new URLSearchParams({ version_id: versionId, expected_revision: String(expectedRevision) })
  return apiRequest(`/cases/${caseId}/snapshots/${snapshotId}/checklist/rebase-preview?${query}`, tokenProvider)
}

export async function fetchRunProposal(
  runId: string, tokenProvider: TokenProvider,
): Promise<ChecklistProposal> {
  return apiRequest(`/agent-runs/${runId}/proposal`, tokenProvider)
}

export async function fetchRunProgress(
  runId: string, tokenProvider: TokenProvider,
): Promise<DynamicProgressResult> {
  return apiRequest(`/agent-runs/${runId}/progress`, tokenProvider)
}

export async function fetchDynamicFacts(
  runId: string, tokenProvider: TokenProvider,
): Promise<DynamicFactsResult> {
  return apiRequest(`/agent-runs/${runId}/facts`, tokenProvider)
}

export interface DynamicFactCandidate {
  criterion_id: string
  item_id: string
  supplier: string
  value: string | boolean
  unit: string
  evidence_refs: string[]
  quote: string
  currency?: string | null
  per_unit?: string | null
  tax_basis?: string | null
  calendar?: string | null
  start_event?: string | null
}

export async function submitDynamicFactCorrection(
  runId: string,
  body: { fact_id: string; candidate: DynamicFactCandidate; reason: string;
    expected_revision: number; expected_payload_digest: string },
  tokenProvider: TokenProvider,
): Promise<DynamicCorrectionRecord> {
  return apiRequest(`/agent-runs/${runId}/fact-corrections`, tokenProvider, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify(body),
  })
}
