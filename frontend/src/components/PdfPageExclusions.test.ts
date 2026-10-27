// @vitest-environment jsdom
import { afterEach, expect, test } from 'vitest'
import { mount } from '@vue/test-utils'
import PdfPageExclusions from './PdfPageExclusions.vue'
import type { PdfPageExclusion } from '../itemBindings'

const document = { id: 'doc-a', original_name: '手机.pdf', role: 'spec' as const, supplier_handle: 'vendor-a',
  supplier_name: '供应商甲', model_name: 'PHONE-A', parse_status: 'complete', source_sha256: 'a'.repeat(64) }
const block = { id: 'other-title', document_id: 'doc-a', ordinal: 0, source_text: '型号：PHONE-B', page_idx: 1,
  sheet: null, cell: null, bbox: null, page_width_pt: null, page_height_pt: null, parser_version: 'pdf-v1' }
const other: PdfPageExclusion = { document_id: 'other-doc', source_sha256: 'b'.repeat(64), parser_version: 'pdf-v1',
  page: 3, other_sku: 'OTHER', sku_anchor: { evidence_refs: ['other'], quote: '型号：OTHER' }, reason: '其他文件' }
const wrappers: ReturnType<typeof mount>[] = []
function setup(extra: object = {}) {
  const wrapper = mount(PdfPageExclusions, { props: { document, items: [{ item_id: 'phone', name: '手机', specification: 'PHONE-A' }],
    evidence: [block], parser: 'pdf-v1', complete: true, loading: false, writable: true, locked: false, formBlocked: false,
    uncertain: false, current: [], exclusions: [other], historical: [], readonlyHistory: false, ...extra } })
  wrappers.push(wrapper); return wrapper
}
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))
async function fill(wrapper: ReturnType<typeof mount>) {
  await wrapper.get('[data-test="exclusion-add"]').trigger('click')
  await wrapper.get('[data-test="exclusion-page"]').setValue('2')
  await wrapper.get('[data-test="exclusion-sku"]').setValue('PHONE-B')
  await wrapper.get('[data-test="exclusion-anchor"]').setValue('other-title')
  await wrapper.get('[data-test="exclusion-reason"]').setValue('其他型号')
}
test('staging explicitly retains other documents and never sends derived exclusion evidence', async () => {
  const wrapper = setup(); expect(wrapper.emitted('update')).toBeUndefined()
  await fill(wrapper); await wrapper.get('[data-test="exclusion-form"]').trigger('submit')
  const entries = wrapper.emitted('update')![0]![0] as PdfPageExclusion[]
  expect(entries).toEqual([other, { document_id: 'doc-a', source_sha256: document.source_sha256, parser_version: 'pdf-v1',
    page: 2, other_sku: 'PHONE-B', sku_anchor: { evidence_refs: ['other-title'], quote: '型号：PHONE-B' }, reason: '其他型号' }])
  expect(entries[1]).not.toHaveProperty('evidence_refs')
  expect(wrapper.emitted('draftChange')?.at(-1)).toEqual([false])
})
test.each([
  ['exclusion-sku', 'PHONE-A', '目标型号不能排除'],
  ['exclusion-sku', '手机-乙', '字母、数字'],
  ['exclusion-quote', '并非原文 PHONE-B', '真实型号依据'],
  ['exclusion-reason', '', '排除原因'],
])('rejects invalid basic input %s=%s without claiming server validation', async (field, value, message) => {
  const wrapper = setup(); await fill(wrapper)
  await wrapper.get(`[data-test="${field}"]`).setValue(value)
  await wrapper.get('[data-test="exclusion-form"]').trigger('submit')
  expect(wrapper.get('[role="alert"]').text()).toContain(message)
  expect(wrapper.emitted('update')).toBeUndefined()
})
test('an unspecified buyer SKU cannot stage an exclusion', async () => {
  const wrapper = setup({ items: [{ item_id: 'phone', name: '手机', specification: '128GB' }, { item_id: 'fruit', name: '苹果' }] })
  await fill(wrapper); await wrapper.get('[data-test="exclusion-form"]').trigger('submit')
  expect(wrapper.text()).toContain('为每个采购品目明确具体型号')
  expect(wrapper.emitted('update')).toBeUndefined()
})
test('cancelling a draft clears its parent guard and produces no exclusion change', async () => {
  const wrapper = setup(); await fill(wrapper)
  expect(wrapper.emitted('draftChange')?.at(-1)).toEqual([true])
  await wrapper.get('[data-test="exclusion-cancel"]').trigger('click')
  expect(wrapper.emitted('draftChange')?.at(-1)).toEqual([false])
  expect(wrapper.emitted('update')).toBeUndefined()
})
test('a form blocked by another binding draft cannot stage by submitting directly', async () => {
  const wrapper = setup(); await fill(wrapper); await wrapper.setProps({ formBlocked: true })
  await wrapper.get('[data-test="exclusion-form"]').trigger('submit')
  expect(wrapper.emitted('update')).toBeUndefined()
})
