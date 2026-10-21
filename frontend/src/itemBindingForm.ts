import type { ChecklistItem, DocumentRecord, EvidenceRecord } from './api'
import type { BindingSelector, ExpandedItemBindingEntry, ItemBindingEntry } from './itemBindings'

export interface BindingDraft {
  itemId: string; kind: BindingSelector['kind']; sheet: string; startRow: string; endRow: string
  startColumn: string; endColumn: string; startPage: string; endPage: string
  anchorId: string; anchorQuote: string; identifierClass: ItemBindingEntry['identifier_class']
  sku: string; skuAnchorId: string; skuQuote: string; singleItem: boolean
}
export function newBindingDraft(): BindingDraft {
  return { itemId: '', kind: 'whole', sheet: '', startRow: '1', endRow: '1', startColumn: 'A', endColumn: 'B',
    startPage: '1', endPage: '1', anchorId: '', anchorQuote: '', identifierClass: 'unknown',
    sku: '', skuAnchorId: '', skuQuote: '', singleItem: false }
}
function integer(value: string, max: number): number {
  if (!/^\d+$/.test(value.trim())) throw new Error('范围必须使用正整数。')
  const parsed = Number(value)
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > max) throw new Error('范围超出允许上限。')
  return parsed
}
export function columnNumber(value: string): number {
  const label = value.trim().toUpperCase()
  if (!/^[A-Z]{1,3}$/.test(label)) throw new Error('列号请使用 A、B 等字母。')
  const result = [...label].reduce((number, letter) => number * 26 + letter.charCodeAt(0) - 64, 0)
  if (result > 16384) throw new Error('列号超出 XLSX 上限。')
  return result
}
function columnLabel(value: number): string {
  let label = ''; let number = value
  while (number > 0) { number--; label = String.fromCharCode(65 + number % 26) + label; number = Math.floor(number / 26) }
  return label
}
export function draftSelector(draft: BindingDraft): BindingSelector {
  if (draft.kind === 'whole') return { kind: 'whole' }
  if (draft.kind === 'pdf') {
    const start = integer(draft.startPage, 30); const end = integer(draft.endPage, 30)
    if (start > end) throw new Error('起始页不能大于结束页。')
    return { kind: 'pdf', start_page: start, end_page: end }
  }
  const startRow = integer(draft.startRow, 1048576); const endRow = integer(draft.endRow, 1048576)
  const startColumn = columnNumber(draft.startColumn); const endColumn = columnNumber(draft.endColumn)
  if (!draft.sheet.trim() || draft.sheet.length > 255 || startRow > endRow || startColumn > endColumn) throw new Error('请核对工作表与起止范围。')
  return { kind: 'xlsx', sheet: draft.sheet, start_row: startRow, end_row: endRow, start_column: startColumn, end_column: endColumn }
}
export function evidenceInSelector(block: EvidenceRecord, selector: BindingSelector): boolean {
  if (selector.kind === 'whole') return true
  if (selector.kind === 'pdf') return block.page_idx !== null && !block.sheet
    && block.page_idx + 1 >= selector.start_page && block.page_idx + 1 <= selector.end_page
  if (block.sheet !== selector.sheet || !block.cell) return false
  const match = /^([A-Z]{1,3})(\d+)$/i.exec(block.cell)
  if (!match) return false
  const row = Number(match[2]); const column = columnNumber(match[1]!)
  return row >= selector.start_row && row <= selector.end_row && column >= selector.start_column && column <= selector.end_column
}
export function rangeLabel(selector: BindingSelector): string {
  if (selector.kind === 'whole') return '整份文件（只包含这个商品）'
  if (selector.kind === 'pdf') return `PDF 第 ${selector.start_page}–${selector.end_page} 页`
  return `${selector.sheet}!${columnLabel(selector.start_column)}${selector.start_row}:${columnLabel(selector.end_column)}${selector.end_row}`
}
function context(id: string, quote: string, document: DocumentRecord, blocks: EvidenceRecord[]) {
  const block = blocks.find(entry => entry.id === id && entry.document_id === document.id)
  if (!block || !quote.trim() || quote.length > 2000 || !block.source_text.includes(quote)) throw new Error('请从同一文件选择真实原文，摘录须与原文一致且不超过 2000 字。')
  return { evidence_refs: [id], quote }
}
export function buildBindingEntry(draft: BindingDraft, document: DocumentRecord, items: ChecklistItem[],
  blocks: EvidenceRecord[], parser: string | null): ItemBindingEntry {
  const item = items.find(entry => entry.item_id === draft.itemId)
  if (!item) throw new Error('请选择已确认审查清单中的采购商品。')
  if (!parser || parser.length > 1000 || !/^[a-f0-9]{64}$/.test(document.source_sha256)) throw new Error('当前资料的解析信息不可用，请刷新。')
  const selector = draftSelector(draft)
  if (selector.kind === 'whole' && !draft.singleItem) throw new Error('请勾选确认：整份文件只描述这个采购商品。')
  if (selector.kind !== 'whole' && selector.kind !== (document.original_name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'xlsx')) throw new Error('范围类型与文件不一致。')
  const anchor = context(draft.anchorId, draft.anchorQuote, document, blocks)
  if (draft.identifierClass === 'category' && document.model_name && document.model_name !== item.name) throw new Error('上传时填写的信息与商品名称不同，请重新核对它是名称还是具体型号。')
  const sku = draft.sku.trim() || null
  if (sku && /[\x00-\x1f]/.test(sku)) throw new Error('具体型号或货号不能包含控制字符（如换行或制表符）。')
  if (sku && sku.length > 120) throw new Error('具体型号或货号不能超过 120 字。')
  const skuAnchor = sku ? context(draft.skuAnchorId, draft.skuQuote, document, blocks) : null
  if (sku && !skuAnchor!.quote.includes(sku)) throw new Error('型号依据的引用内容必须包含所填写的具体型号或货号。')
  if (draft.identifierClass === 'sku' && (!sku || (document.model_name && document.model_name !== sku))) throw new Error('请填写与上传信息一致的具体型号或货号，并选择文件中的原文依据。')
  return { document_id: document.id, source_sha256: document.source_sha256, parser_version: parser, item_id: item.item_id,
    selector, anchor, identifier_class: draft.identifierClass, supplier_sku: sku, sku_anchor: skuAnchor,
    single_item_confirmed: selector.kind === 'whole' }
}
export function bindingRequestEntry(entry: ItemBindingEntry | ExpandedItemBindingEntry): ItemBindingEntry {
  return { document_id: entry.document_id, source_sha256: entry.source_sha256, parser_version: entry.parser_version,
    item_id: entry.item_id, selector: { ...entry.selector }, anchor: { evidence_refs: [...entry.anchor.evidence_refs], quote: entry.anchor.quote },
    identifier_class: entry.identifier_class, supplier_sku: entry.supplier_sku,
    sku_anchor: entry.sku_anchor ? { evidence_refs: [...entry.sku_anchor.evidence_refs], quote: entry.sku_anchor.quote } : null,
    single_item_confirmed: entry.single_item_confirmed }
}
export function draftFromEntry(entry: ItemBindingEntry): BindingDraft {
  const result = { ...newBindingDraft(), itemId: entry.item_id, kind: entry.selector.kind,
    anchorId: entry.anchor.evidence_refs.length === 1 ? entry.anchor.evidence_refs[0]! : '', anchorQuote: entry.anchor.quote,
    identifierClass: entry.identifier_class, sku: entry.supplier_sku ?? '',
    skuAnchorId: entry.sku_anchor?.evidence_refs.length === 1 ? entry.sku_anchor.evidence_refs[0]! : '',
    skuQuote: entry.sku_anchor?.quote ?? '', singleItem: entry.single_item_confirmed }
  if (entry.selector.kind === 'xlsx') Object.assign(result, { sheet: entry.selector.sheet,
    startRow: String(entry.selector.start_row), endRow: String(entry.selector.end_row),
    startColumn: columnLabel(entry.selector.start_column), endColumn: columnLabel(entry.selector.end_column) })
  if (entry.selector.kind === 'pdf') Object.assign(result, { startPage: String(entry.selector.start_page), endPage: String(entry.selector.end_page) })
  return result
}
