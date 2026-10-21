import { expect, test } from 'vitest'
import { bindingRowState, bindingRowsPayload, createBindingRows } from './itemBindingEdits'
import type { ItemBindingEntry } from './itemBindings'

const entry: ItemBindingEntry = { document_id: 'doc-a', source_sha256: 'a'.repeat(64), parser_version: 'xlsx-v1',
  item_id: 'apple', selector: { kind: 'whole' }, anchor: { evidence_refs: ['title'], quote: '苹果' },
  identifier_class: 'category', supplier_sku: null, sku_anchor: null, single_item_confirmed: true }

test('editing an association never mutates its confirmed value or the server response', () => {
  const row = createBindingRows([entry])[0]!
  row.entry!.anchor.quote = '苹果商品'
  row.entry!.anchor.evidence_refs.push('new-title')
  expect(row.original!.anchor).toEqual({ evidence_refs: ['title'], quote: '苹果' })
  expect(entry.anchor).toEqual({ evidence_refs: ['title'], quote: '苹果' })
  expect(bindingRowState(row)).toBe('modified')
  row.entry = row.original
  expect(bindingRowState(row)).toBe('unchanged')
})

test('pending cancellation is retained for display but excluded from the saved payload', () => {
  const rows = createBindingRows([entry, { ...entry, document_id: 'other', item_id: 'shirt' }])
  rows[0]!.entry = null
  expect(bindingRowState(rows[0]!)).toBe('removed')
  expect(bindingRowsPayload(rows)).toEqual([{ ...entry, document_id: 'other', item_id: 'shirt' }])
  rows[0]!.entry = rows[0]!.original
  expect(bindingRowsPayload(rows)).toEqual([entry, { ...entry, document_id: 'other', item_id: 'shirt' }])
})

test('new associations have an explicit pending state and retain source and identity fields', () => {
  const rows = [{ key: 'new', original: null, entry }]
  expect(bindingRowState(rows[0]!)).toBe('added')
  expect(bindingRowsPayload(rows)).toEqual([entry])
})
