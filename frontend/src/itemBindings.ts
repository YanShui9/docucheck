import { apiRequest } from './api'
import type { EvidenceContextRecord, EvidenceRecord, TokenProvider } from './api'

export type BindingSelector = { kind: 'whole' }
  | { kind: 'xlsx'; sheet: string; start_row: number; end_row: number; start_column: number; end_column: number }
  | { kind: 'pdf'; start_page: number; end_page: number }

export interface ItemBindingEntry {
  document_id: string
  source_sha256: string
  parser_version: string
  item_id: string
  selector: BindingSelector
  anchor: EvidenceContextRecord
  identifier_class: 'category' | 'sku' | 'unknown'
  supplier_sku: string | null
  sku_anchor: EvidenceContextRecord | null
  single_item_confirmed: boolean
}
export interface ExpandedItemBindingEntry extends ItemBindingEntry {
  supplier: string
  evidence_refs: string[]
  source_fingerprint: string
  anchor_fingerprint: string
}
export interface PdfPageExclusion {
  document_id: string; source_sha256: string; parser_version: string; page: number; other_sku: string
  sku_anchor: EvidenceContextRecord; reason: string
}
export interface ExpandedPdfPageExclusion extends PdfPageExclusion {
  supplier: string; evidence_refs: string[]; source_fingerprint: string; anchor_fingerprint: string
}
export interface ItemBindingVersion {
  id: string; version: number; snapshot_id: string; checklist_version_id: string; digest: string
  confirmed_by: string; reason: string; confirmed_at: string
  payload: { schema_version: 1 | 2; case_id: string; snapshot_id: string; checklist_version_id: string | null;
    checklist_digest: string; entries: ExpandedItemBindingEntry[]; exclusions?: ExpandedPdfPageExclusion[] }
}
export interface ItemBindingView {
  case_id: string; snapshot_id: string; binding_revision: number
  current_version: ItemBindingVersion | null
  historical_version: ItemBindingVersion | null
  state: 'confirmed' | 'stale' | 'empty'
}
export interface ConfirmItemBindings {
  entries: ItemBindingEntry[]; checklist_version_id: string; expected_revision: number
  expected_checklist_revision: number; expected_binding_revision: number; confirmed: true; reason: string
  exclusions?: PdfPageExclusion[]; exclusions_confirmed?: true
}
export function exclusionRequestEntry(entry: PdfPageExclusion): PdfPageExclusion {
  return { document_id: entry.document_id, source_sha256: entry.source_sha256, parser_version: entry.parser_version,
    page: entry.page, other_sku: entry.other_sku, reason: entry.reason,
    sku_anchor: { evidence_refs: [...entry.sku_anchor.evidence_refs], quote: entry.sku_anchor.quote } }
}
export interface DocumentEvidencePage {
  case_id: string; snapshot_id: string; document_id: string; parse_status: string
  parser_version: string | null; coverage_issues: string[]; evidence: EvidenceRecord[]; next_ordinal: number
}
function snapshotPath(caseId: string, snapshotId: string): string {
  return `/cases/${encodeURIComponent(caseId)}/snapshots/${encodeURIComponent(snapshotId)}`
}
export function fetchItemBindings(caseId: string, snapshotId: string, token: TokenProvider): Promise<ItemBindingView> {
  return apiRequest(`${snapshotPath(caseId, snapshotId)}/item-bindings`, token)
}
export function confirmItemBindings(caseId: string, snapshotId: string, body: ConfirmItemBindings,
  idempotencyKey: string, token: TokenProvider): Promise<ItemBindingView> {
  return apiRequest(`${snapshotPath(caseId, snapshotId)}/item-bindings`, token, {
    method: 'PUT', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify(body),
  })
}
export function fetchDocumentEvidence(caseId: string, snapshotId: string, documentId: string,
  afterOrdinal: number, token: TokenProvider): Promise<DocumentEvidencePage> {
  const query = new URLSearchParams({ after_ordinal: String(afterOrdinal), limit: '100' })
  return apiRequest(`${snapshotPath(caseId, snapshotId)}/documents/${encodeURIComponent(documentId)}/evidence?${query}`, token)
}
