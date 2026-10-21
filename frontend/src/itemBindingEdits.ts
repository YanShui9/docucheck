import type { ItemBindingEntry } from './itemBindings'
import { bindingRequestEntry } from './itemBindingForm'

export interface BindingEditRow {
  key: string
  original: ItemBindingEntry | null
  entry: ItemBindingEntry | null
}
export function createBindingRows(entries: readonly ItemBindingEntry[]): BindingEditRow[] {
  return entries.map((entry, index) => ({ key: `saved-${index}`,
    original: bindingRequestEntry(entry), entry: bindingRequestEntry(entry) }))
}
export function bindingRowState(row: BindingEditRow): 'unchanged' | 'added' | 'modified' | 'removed' {
  if (!row.entry) return 'removed'
  if (!row.original) return 'added'
  return JSON.stringify(row.original) === JSON.stringify(row.entry) ? 'unchanged' : 'modified'
}
export function bindingRowsPayload(rows: readonly BindingEditRow[]): ItemBindingEntry[] {
  return rows.flatMap(row => row.entry ? [bindingRequestEntry(row.entry)] : [])
}
