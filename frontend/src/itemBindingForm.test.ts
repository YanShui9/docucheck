import { expect, test } from 'vitest'
import { buildBindingEntry, bindingRequestEntry, evidenceInSelector, rangeLabel } from './itemBindingForm'
import type { BindingDraft } from './itemBindingForm'
import type { EvidenceRecord } from './api'

const document = { id: 'doc', original_name: '报价.xlsx', role: 'quote' as const,
  supplier_handle: 'vendor', supplier_name: '供应商', model_name: '苹果',
  parse_status: 'complete', source_sha256: 'a'.repeat(64) }
const evidence: EvidenceRecord[] = [
  { id: 'title', document_id: 'doc', ordinal: 0, source_text: '品目：苹果', sheet: '报价', cell: 'B3',
    page_idx: null, bbox: null, page_width_pt: null, page_height_pt: null, parser_version: 'xlsx-v1' },
  { id: 'qty', document_id: 'doc', ordinal: 1, source_text: '10 箱', sheet: '报价', cell: 'B4',
    page_idx: null, bbox: null, page_width_pt: null, page_height_pt: null, parser_version: 'xlsx-v1' },
]
function draft(): BindingDraft { return { itemId: 'apple', kind: 'xlsx', sheet: '报价', startRow: '4', endRow: '4',
  startColumn: 'A', endColumn: 'C', startPage: '1', endPage: '1', anchorId: 'title', anchorQuote: '苹果',
  identifierClass: 'category', sku: '', skuAnchorId: '', skuQuote: '', singleItem: false } }

test('range anchor may be outside numeric scope without becoming a numeric quote', () => {
  const entry = buildBindingEntry(draft(), document, [{ item_id: 'apple', name: '苹果' }], evidence, 'xlsx-v1')
  expect(entry.selector).toEqual({ kind: 'xlsx', sheet: '报价', start_row: 4, end_row: 4, start_column: 1, end_column: 3 })
  expect(entry.anchor).toEqual({ evidence_refs: ['title'], quote: '苹果' })
  expect(evidenceInSelector(evidence[0]!, entry.selector)).toBe(false)
  expect(evidenceInSelector(evidence[1]!, entry.selector)).toBe(true)
  expect(rangeLabel(entry.selector)).toBe('报价!A4:C4')
})

test.each([
  { startRow: '0' }, { startRow: '5', endRow: '4' }, { endRow: '1048577' },
  { startColumn: 'XFE' }, { startColumn: 'C', endColumn: 'B' }, { startRow: '1.5' },
  { kind: 'pdf', startPage: '2', endPage: '1' }, { kind: 'pdf', endPage: '31' },
])('invalid or incompatible range is rejected before confirmation: %j', changes => {
  expect(() => buildBindingEntry({ ...draft(), ...changes } as BindingDraft, document,
    [{ item_id: 'apple', name: '苹果' }], evidence, 'xlsx-v1')).toThrow()
})

test('whole file requires a separate single item declaration', () => {
  const input = { ...draft(), kind: 'whole' as const }
  expect(() => buildBindingEntry(input, document, [{ item_id: 'apple', name: '苹果' }], evidence, 'xlsx-v1')).toThrow('单一品目')
  expect(buildBindingEntry({ ...input, singleItem: true }, document,
    [{ item_id: 'apple', name: '苹果' }], evidence, 'xlsx-v1').single_item_confirmed).toBe(true)
})

test('invented anchor, cross document evidence and unclassified SKU are not accepted', () => {
  for (const change of [{ anchorQuote: '20 箱' }, { anchorId: 'missing' }, { identifierClass: 'sku' as const }]) {
    expect(() => buildBindingEntry({ ...draft(), ...change }, document,
      [{ item_id: 'apple', name: '苹果' }], evidence, 'xlsx-v1')).toThrow()
  }
  expect(() => buildBindingEntry(draft(), document, [{ item_id: 'apple', name: '苹果' }],
    evidence.map(block => ({ ...block, document_id: 'other' })), 'xlsx-v1')).toThrow()
})

test('expanded output fields never enter the confirmation request', () => {
  const entry = buildBindingEntry(draft(), document, [{ item_id: 'apple', name: '苹果' }], evidence, 'xlsx-v1')
  expect(bindingRequestEntry({ ...entry, supplier: 'vendor', evidence_refs: ['qty'],
    source_fingerprint: 'fp', anchor_fingerprint: 'fp' })).toEqual(entry)
})

test('PDF preview uses human page numbers and never leaks another page or spreadsheet', () => {
  const selector = { kind: 'pdf' as const, start_page: 2, end_page: 3 }
  expect(evidenceInSelector({ ...evidence[0]!, sheet: null, cell: null, page_idx: 1 }, selector)).toBe(true)
  expect(evidenceInSelector({ ...evidence[0]!, sheet: null, cell: null, page_idx: 0 }, selector)).toBe(false)
  expect(evidenceInSelector(evidence[0]!, selector)).toBe(false)
})

test('SKU with internal control characters is rejected even when present in the source', () => {
  const input = { ...draft(), identifierClass: 'unknown' as const, sku: 'SKU\t10', skuAnchorId: 'sku', skuQuote: 'SKU\t10' }
  expect(() => buildBindingEntry(input, document, [{ item_id: 'apple', name: '苹果' }],
    [...evidence, { ...evidence[0]!, id: 'sku', source_text: 'SKU\t10' }], 'xlsx-v1')).toThrow('控制字符')
})
