// @vitest-environment jsdom
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import DocumentItemBindings from './DocumentItemBindings.vue'

const { apiRequest } = vi.hoisted(() => ({ apiRequest: vi.fn() }))
vi.mock('../api', () => ({ apiRequest, fetchChecklist: (caseId: string, snapshotId: string, token: unknown) =>
  apiRequest(`/cases/${caseId}/snapshots/${snapshotId}/checklist`, token) }))
vi.mock('../auth', () => ({ accessToken: async () => 'token' }))
const document = { id: 'doc-a', original_name: '水果报价.xlsx', role: 'quote', supplier_handle: 'vendor-a',
  supplier_name: '供应商甲', model_name: '苹果', parse_status: 'complete', source_sha256: 'a'.repeat(64) }
const block = { id: 'title', document_id: 'doc-a', ordinal: 0, source_text: '采购品目：苹果', sheet: '报价', cell: 'B3',
  page_idx: null, bbox: null, page_width_pt: null, page_height_pt: null, parser_version: 'xlsx-v1' }
const blocks = [block, { ...block, id: 'qty', ordinal: 1, source_text: '10 箱', cell: 'B4' }]
const otherEntry = { document_id: 'doc-other', source_sha256: 'b'.repeat(64), parser_version: 'xlsx-v1',
  item_id: 'clothes', selector: { kind: 'whole' }, anchor: { evidence_refs: ['other-title'], quote: '服装' },
  identifier_class: 'unknown', supplier_sku: null, sku_anchor: null, single_item_confirmed: true,
  supplier: 'vendor-b', evidence_refs: ['other-title'], source_fingerprint: 'fp', anchor_fingerprint: 'fp' }
function version(entries = [otherEntry]) { return { id: 'binding-v1', version: 1, snapshot_id: 'snap-a',
  checklist_version_id: 'check-v1', digest: 'c'.repeat(64), confirmed_by: 'editor', reason: '核对', confirmed_at: '2026-10-10T12:00:00Z',
  payload: { schema_version: 1, case_id: 'case-a', snapshot_id: 'snap-a', checklist_version_id: 'check-v1', checklist_digest: 'd'.repeat(64), entries } } }
const checklist = { case_id: 'case-a', snapshot_id: 'snap-a', revision: 2, is_current_snapshot: true,
  checklist_revision: 3, state: 'confirmed', execution_available: true, issues: [], draft: null,
  confirmed_version: { id: 'check-v1', definition: { items: [{ item_id: 'apple', name: '苹果' }, { item_id: 'clothes', name: '服装' }], criteria: [] } } }
function read(path: string, _token?: unknown, init?: RequestInit): Promise<unknown> {
  if (init?.method === 'PUT') return Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', state: 'confirmed', binding_revision: 2,
    current_version: version(JSON.parse(init.body as string).entries), historical_version: null })
  if (path.endsWith('/checklist')) return Promise.resolve(checklist)
  if (path.endsWith('/item-bindings')) return Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', state: 'confirmed', binding_revision: 1,
    current_version: version(), historical_version: null })
  if (path.includes('/documents/doc-a/evidence?')) return Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', document_id: 'doc-a',
    parse_status: 'complete', parser_version: 'xlsx-v1', coverage_issues: [], evidence: blocks, next_ordinal: 1 })
  throw new Error(`Unexpected path ${path}`)
}
const wrappers: ReturnType<typeof mount>[] = []
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView
const originalShowModal = HTMLDialogElement.prototype.showModal
const originalClose = HTMLDialogElement.prototype.close
const showModal = vi.fn(function (this: HTMLDialogElement) {
  this.open = true
  this.querySelector<HTMLElement>('[autofocus]')?.focus()
})
const closeDialog = vi.fn(function (this: HTMLDialogElement) { this.open = false })
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = showModal
  HTMLDialogElement.prototype.close = closeDialog
})
async function editor(props: Record<string, unknown> = {}, request = read) {
  apiRequest.mockImplementation(request)
  const wrapper = mount(DocumentItemBindings, { props: { caseId: 'case-a', snapshotId: 'snap-a', revision: 2,
    document: document as never, canWrite: true, historical: false, refreshKey: 0, ...props },
    attachTo: window.document.body,
    global: { stubs: { EvidencePanel: { name: 'EvidencePanel', props: ['target'], template: '<div class="original-preview">原件预览</div>' } } } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
async function stage(wrapper: ReturnType<typeof mount>) {
  await wrapper.get('[data-test="binding-item"]').setValue('apple')
  await wrapper.get('[data-test="binding-anchor"]').setValue('title')
  await wrapper.get('[data-test="binding-identifier"]').setValue('category')
  await wrapper.get('[data-test="binding-single"]').setValue(true)
  await wrapper.get('[data-test="binding-entry-form"]').trigger('submit')
}
function writes() { return apiRequest.mock.calls.filter(([, , init]) => init?.method === 'PUT') }
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  apiRequest.mockReset(); showModal.mockClear(); closeDialog.mockClear(); vi.restoreAllMocks()
  HTMLElement.prototype.scrollIntoView = originalScrollIntoView
  HTMLDialogElement.prototype.showModal = originalShowModal
  HTMLDialogElement.prototype.close = originalClose
  window.document.body.innerHTML = ''
})

test('opens a named modal before data arrives instead of expanding below the document list', async () => {
  const wrapper = await editor({}, () => new Promise(() => {}))
  const modal = wrapper.get('dialog')
  expect(showModal).toHaveBeenCalledOnce()
  expect((modal.element as HTMLDialogElement).open).toBe(true)
  expect(modal.attributes('aria-modal')).toBe('true')
  expect(modal.find(`#${modal.attributes('aria-labelledby')}`).text()).toBe('关联采购品目')
  expect(modal.get('.binding-dialog-header').text()).toContain('水果报价.xlsx')
  expect(modal.text()).toContain('正在读取清单、对应关系和原文')
})

test('keeps final confirmation outside the scrolling form', async () => {
  const wrapper = await editor()
  await stage(wrapper)
  expect(wrapper.find('.binding-dialog-footer [data-test="binding-save"]').exists()).toBe(true)
  expect(wrapper.find('.binding-dialog-footer [data-test="binding-confirm"]').exists()).toBe(true)
  expect(wrapper.get('.binding-dialog-body').find('[data-test="binding-save"]').exists()).toBe(false)
  expect(wrapper.get('.binding-dialog-body').find('[data-test="binding-entry-form"]').exists()).toBe(true)
})

test.each(['button', 'escape'])('closing by %s restores focus to the document entry', async method => {
  const opener = window.document.createElement('button')
  window.document.body.append(opener); opener.focus()
  const wrapper = await editor()
  expect(window.document.activeElement).toBe(wrapper.get('[data-test="binding-close"]').element)
  if (method === 'button') await wrapper.get('[data-test="binding-close"]').trigger('click')
  else await wrapper.get('dialog').trigger('cancel')
  expect(wrapper.emitted('close')).toHaveLength(1)
  expect((wrapper.get('dialog').element as HTMLDialogElement).open).toBe(false)
  expect(window.document.activeElement).toBe(opener)
})

test('saving prevents both Escape and button dismissal', async () => {
  const wrapper = await editor()
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  let receipt!: (value: unknown) => void
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? new Promise(resolve => { receipt = resolve }) : read(path, token, init))
  await wrapper.get('[data-test="binding-save"]').trigger('click')
  await wrapper.get('dialog').trigger('cancel')
  expect(wrapper.get('[data-test="binding-close"]').attributes('disabled')).toBeDefined()
  expect(wrapper.emitted('close')).toBeUndefined()
  expect((wrapper.get('dialog').element as HTMLDialogElement).open).toBe(true)
  receipt(await read('', undefined, writes()[0]![2]))
  await flushPromises()
  await wrapper.get('dialog').trigger('cancel')
  expect(wrapper.emitted('close')).toHaveLength(1)
})

test('unmounting an open editor closes its native modal', async () => {
  const wrapper = await editor()
  const modal = wrapper.get('dialog').element as HTMLDialogElement
  wrapper.unmount(); wrappers.pop()
  expect(modal.open).toBe(false)
  expect(closeDialog).toHaveBeenCalledOnce()
})

test('Tab and Shift+Tab cycle between the last and first modal controls', async () => {
  const wrapper = await editor()
  const first = wrapper.get('[data-test="binding-close"]').element as HTMLElement
  const last = wrapper.get('.binding-dialog-footer button').element as HTMLElement
  for (const element of [first, last]) vi.spyOn(element, 'getClientRects').mockReturnValue([new DOMRect()] as unknown as DOMRectList)
  first.focus()
  await wrapper.get('[data-test="binding-close"]').trigger('keydown', { key: 'Tab', shiftKey: true })
  expect(window.document.activeElement).toBe(last)
  await wrapper.get('.binding-dialog-footer button').trigger('keydown', { key: 'Tab' })
  expect(window.document.activeElement).toBe(first)
})

test('staging is local; explicit confirmation preserves other documents and sends all revision guards', async () => {
  const wrapper = await editor()
  await stage(wrapper)
  expect(writes()).toHaveLength(0)
  expect(wrapper.get('[data-test="binding-save"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click')
  await flushPromises()
  expect(writes()).toHaveLength(1)
  const request = writes()[0]![2]
  const body = JSON.parse(request.body)
  expect(body).toMatchObject({ expected_revision: 2, expected_checklist_revision: 3,
    expected_binding_revision: 1, checklist_version_id: 'check-v1', confirmed: true })
  expect(body.entries).toHaveLength(2)
  expect(body.entries[0]).toMatchObject({ document_id: 'doc-other', item_id: 'clothes' })
  expect(body.entries[0]).not.toHaveProperty('supplier')
  expect(body.entries[1]).toMatchObject({ document_id: 'doc-a', item_id: 'apple', anchor: { evidence_refs: ['title'], quote: '采购品目：苹果' } })
  expect(request.headers['Idempotency-Key']).toBeTruthy()
  expect(wrapper.text()).toContain('请重新发起审查')
  expect(wrapper.emitted('confirmed')).toEqual([[{ caseId: 'case-a', snapshotId: 'snap-a' }]])
})

test.each([{ canWrite: false }, { historical: true }])('read only scope cannot stage or save: %j', async props => {
  const wrapper = await editor(props)
  expect(wrapper.find('[data-test="binding-entry-form"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-save"]').exists()).toBe(false)
  expect(writes()).toHaveLength(0)
})

test('unconfirmed checklist blocks attribution even when an old version exists', async () => {
  const wrapper = await editor()
  apiRequest.mockImplementation((path, token, init) => path.endsWith('/checklist')
    ? Promise.resolve({ ...checklist, state: 'draft' }) : read(path, token, init))
  await wrapper.setProps({ refreshKey: 1 }); await flushPromises()
  expect(wrapper.find('[data-test="binding-entry-form"]').exists()).toBe(false)
  expect(wrapper.text()).toContain('先保存并确认当前审查清单')
})

test('stale bindings remain historical and are never included silently in a new request', async () => {
  const wrapper = await editor()
  apiRequest.mockImplementation((path, token, init) => !init && path.endsWith('/item-bindings')
    ? Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', state: 'stale', binding_revision: 4, current_version: null, historical_version: version() })
    : read(path, token, init))
  await wrapper.setProps({ refreshKey: 1 }); await flushPromises()
  expect(wrapper.text()).toContain('历史对应关系')
  await stage(wrapper)
  await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(JSON.parse(writes()[0]![2].body).entries).toHaveLength(1)
})

test('range preview and original preview preserve the local entry form', async () => {
  const scroll = vi.fn()
  HTMLElement.prototype.scrollIntoView = scroll
  const wrapper = await editor()
  await wrapper.get('[data-test="binding-kind"]').setValue('xlsx')
  await wrapper.get('[data-test="binding-sheet"]').setValue('报价')
  await wrapper.get('[data-test="binding-start-row"]').setValue('4')
  await wrapper.get('[data-test="binding-end-row"]').setValue('4')
  await wrapper.get('[data-test="binding-start-column"]').setValue('A')
  await wrapper.get('[data-test="binding-end-column"]').setValue('C')
  expect(wrapper.get('[data-test="binding-range-preview"]').text()).toContain('10 箱')
  expect(wrapper.get('[data-test="binding-range-preview"]').text()).not.toContain('采购品目：苹果')
  await wrapper.get('[data-test="binding-anchor"]').setValue('title')
  await wrapper.get('[data-test="binding-original"]').trigger('click'); await flushPromises()
  expect(wrapper.find('.original-preview').exists()).toBe(true)
  expect((wrapper.get('[data-test="binding-start-row"]').element as HTMLInputElement).value).toBe('4')
  expect(scroll).toHaveBeenCalledWith({ block: 'start' })
  wrapper.findComponent({ name: 'EvidencePanel' }).vm.$emit('settled')
  await flushPromises()
  expect(scroll).toHaveBeenCalledTimes(2)
  expect((wrapper.get('[data-test="binding-start-row"]').element as HTMLInputElement).value).toBe('4')
})

test('unknown mutation outcome retries the same frozen body and idempotency key', async () => {
  const wrapper = await editor()
  let attempts = 0
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT' && ++attempts === 1
    ? Promise.reject(new TypeError('Failed to fetch')) : read(path, token, init))
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('保存结果暂时无法确认')
  await wrapper.get('[data-test="binding-retry"]').trigger('click'); await flushPromises()
  expect(writes()).toHaveLength(2)
  expect(writes()[0]![2]).toEqual(writes()[1]![2])
})

test('version conflict asks for refresh without discarding or claiming the staged entry is confirmed', async () => {
  const wrapper = await editor()
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? Promise.reject(new Error('item_binding_revision_conflict')) : read(path, token, init))
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('请刷新')
  expect(wrapper.emitted('confirmed')).toBeUndefined()
  expect(wrapper.text()).toContain('待确认变更')
})

test.each(['validation_error', 'invalid_request'])('explicit validation refusal remains editable instead of retrying an invalid body: %s', async code => {
  const wrapper = await editor()
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? Promise.reject(new Error(code)) : read(path, token, init))
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[data-test="binding-retry"]').exists()).toBe(false)
  expect(wrapper.get('[data-test="binding-entry-form"] fieldset').attributes('disabled')).toBeUndefined()
  expect(wrapper.text()).not.toContain('保存结果暂时无法确认')
  expect(wrapper.emitted('confirmed')).toBeUndefined()
})

test('an un-staged edit cannot be silently omitted by the confirmation button', async () => {
  const wrapper = await editor(); await stage(wrapper)
  await wrapper.get('[data-test="binding-item"]').setValue('clothes')
  expect(wrapper.get('[data-test="binding-save"]').attributes('disabled')).toBeDefined()
  expect(wrapper.get('[data-test="binding-confirm"]').attributes('disabled')).toBeDefined()
  expect(wrapper.text()).toContain('还有未加入清单的填写')
  expect(writes()).toHaveLength(0)
})

test('temporary upload busy state locks editing without clearing staged entries or an in-flight confirmation', async () => {
  const wrapper = await editor(); await stage(wrapper)
  await wrapper.setProps({ busy: true }); await flushPromises()
  expect(wrapper.text()).toContain('待确认变更')
  expect(wrapper.get('[data-test="binding-entry-form"] fieldset').attributes('disabled')).toBeDefined()
  await wrapper.setProps({ busy: false }); await flushPromises()
  let resolve!: (value: unknown) => void
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? new Promise(yes => { resolve = yes }) : read(path, token, init))
  await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  await wrapper.setProps({ busy: true }); await wrapper.setProps({ busy: false })
  resolve({ case_id: 'case-a', snapshot_id: 'snap-a', state: 'confirmed', binding_revision: 2,
    current_version: version(), historical_version: null }); await flushPromises()
  expect(wrapper.emitted('confirmed')).toHaveLength(1)
})

test('editing and removing one file never alters other confirmed file entries', async () => {
  const wrapper = await editor(); await stage(wrapper)
  await wrapper.findAll('button').find(button => button.text() === '编辑范围')!.trigger('click')
  await wrapper.get('[data-test="binding-kind"]').setValue('xlsx')
  await wrapper.get('[data-test="binding-sheet"]').setValue('报价')
  await wrapper.get('[data-test="binding-start-row"]').setValue('4')
  await wrapper.get('[data-test="binding-end-row"]').setValue('4')
  await wrapper.get('[data-test="binding-entry-form"]').trigger('submit')
  expect(wrapper.get('.binding-entry-list').text()).toContain('报价!A4:B4')
  await wrapper.findAll('button').find(button => button.text() === '移出待确认清单')!.trigger('click')
  expect(wrapper.findAll('.binding-entry-list li')).toHaveLength(0)
  expect(wrapper.text()).toContain('保留其他文件的 1 个当前对应范围')
  expect(writes()).toHaveLength(0)
})

test('evidence pagination loads a later anchor and reports parser changes without replacing the saved version', async () => {
  const wrapper = await editor()
  const first = Array.from({ length: 100 }, (_, ordinal) => ({ ...block, id: `part-${ordinal}`, ordinal,
    source_text: `其他说明 ${ordinal}` }))
  let changedParser = false
  apiRequest.mockImplementation((path, token, init) => path.includes('/documents/doc-a/evidence?')
    ? Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', document_id: 'doc-a', parse_status: 'complete',
      parser_version: changedParser ? 'changed-parser' : 'xlsx-v1', coverage_issues: [],
      evidence: path.includes('after_ordinal=99') ? [{ ...block, ordinal: 100 }] : first,
      next_ordinal: path.includes('after_ordinal=99') ? 100 : 99 }) : read(path, token, init))
  await wrapper.setProps({ refreshKey: 1 }); await flushPromises()
  expect(wrapper.find('[data-test="binding-load-more"]').exists()).toBe(true)
  await wrapper.get('[data-test="binding-load-more"]').trigger('click'); await flushPromises()
  expect(wrapper.get('[data-test="binding-anchor"]').text()).toContain('采购品目：苹果')
  await wrapper.setProps({ refreshKey: 2 }); await flushPromises()
  changedParser = true
  await wrapper.get('[data-test="binding-load-more"]').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('解析版本不一致')
  expect(wrapper.text()).toContain('当前对应关系')
  expect(writes()).toHaveLength(0)
})

test('explicit SKU keeps its own original evidence and classification', async () => {
  const wrapper = await editor({ document: { ...document, model_name: 'SKU-10' } })
  apiRequest.mockImplementation((path, token, init) => path.includes('/documents/doc-a/evidence?')
    ? Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', document_id: 'doc-a', parse_status: 'complete',
      parser_version: 'xlsx-v1', coverage_issues: [], evidence: [...blocks, { ...block, id: 'sku', ordinal: 2, source_text: '型号 SKU-10' }], next_ordinal: 2 })
    : read(path, token, init))
  await wrapper.setProps({ refreshKey: 1 }); await flushPromises()
  await wrapper.get('[data-test="binding-item"]').setValue('apple')
  await wrapper.get('[data-test="binding-anchor"]').setValue('title')
  await wrapper.get('[data-test="binding-single"]').setValue(true)
  await wrapper.get('[data-test="binding-identifier"]').setValue('sku')
  await wrapper.get('[data-test="binding-sku"]').setValue('SKU-10')
  await wrapper.get('[data-test="binding-sku-anchor"]').setValue('sku')
  await wrapper.get('[data-test="binding-entry-form"]').trigger('submit')
  await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  const entry = JSON.parse(writes()[0]![2].body).entries[1]
  expect(entry.identifier_class).toBe('sku')
  expect(entry.supplier_sku).toBe('SKU-10')
  expect(entry.sku_anchor).toEqual({ evidence_refs: ['sku'], quote: '型号 SKU-10' })
  expect(entry.anchor.evidence_refs).toEqual(['title'])
})

test('late result from a previous document cannot confirm or replace the new document scope', async () => {
  const wrapper = await editor()
  let resolve!: (value: unknown) => void
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? new Promise(yes => { resolve = yes }) : read(path, token, init))
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  await wrapper.setProps({ document: { ...document, id: 'doc-b' } as never })
  resolve({ state: 'confirmed', current_version: version() }); await flushPromises()
  expect(wrapper.emitted('confirmed')).toBeUndefined()
})

test('closing the panel during a write still notifies the task that the server confirmed a new version', async () => {
  const notifyConfirmed = vi.fn()
  const wrapper = await editor({ notifyConfirmed })
  let resolve!: (value: unknown) => void
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? new Promise(yes => { resolve = yes }) : read(path, token, init))
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  wrapper.unmount()
  resolve({ case_id: 'case-a', snapshot_id: 'snap-a', state: 'confirmed', binding_revision: 2,
    current_version: version(), historical_version: null }); await flushPromises()
  expect(notifyConfirmed).toHaveBeenCalledExactlyOnceWith({ caseId: 'case-a', snapshotId: 'snap-a' })
})

test('a replayed successful receipt is not mistaken for the current head after another editor updates it', async () => {
  const wrapper = await editor()
  let attempts = 0
  const latest = { ...version(), id: 'binding-v3', version: 3 }
  apiRequest.mockImplementation((path, token, init) => {
    if (init?.method === 'PUT') return ++attempts === 1 ? Promise.reject(new TypeError('Failed to fetch'))
      : Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', state: 'confirmed', binding_revision: 2,
        current_version: { ...version(), version: 2 }, historical_version: null })
    if (attempts && path.endsWith('/item-bindings')) return Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a',
      state: 'confirmed', binding_revision: 3, current_version: latest, historical_version: null })
    return read(path, token, init)
  })
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  await wrapper.get('[data-test="binding-retry"]').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('对应版本 3')
  expect(wrapper.text()).not.toContain('对应版本 2')
})

test('successful save followed by a failed current-head read is not retried as an unknown write', async () => {
  const notifyConfirmed = vi.fn()
  const wrapper = await editor({ notifyConfirmed })
  let saved = false
  apiRequest.mockImplementation((path, token, init) => {
    if (init?.method === 'PUT') { saved = true; return read(path, token, init) }
    if (saved && path.endsWith('/item-bindings')) return Promise.reject(new Error('offline'))
    return read(path, token, init)
  })
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('已收到保存成功回执')
  expect(wrapper.find('[data-test="binding-retry"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-entry-form"]').exists()).toBe(false)
  expect(notifyConfirmed).toHaveBeenCalledExactlyOnceWith({ caseId: 'case-a', snapshotId: 'snap-a' })
})

test.each(['forbidden', 'not_found', 'session_expired', 'case_deleting'])('access rejection stops editing and cannot be cleared by a read-only refresh: %s', async code => {
  const wrapper = await editor()
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? Promise.reject(new Error(code)) : read(path, token, init))
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[data-test="binding-entry-form"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-save"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-retry"]').exists()).toBe(false)
  await wrapper.findAll('button').find(button => button.text().startsWith('刷新对应记录'))!.trigger('click')
  await flushPromises()
  expect(wrapper.find('[data-test="binding-entry-form"]').exists()).toBe(false)
  expect(writes()).toHaveLength(1)
})

test('a late evidence page cannot restore the scope after a write is denied', async () => {
  const wrapper = await editor()
  const first = Array.from({ length: 100 }, (_, ordinal) => ({ ...block, id: ordinal ? `part-${ordinal}` : 'title', ordinal }))
  let resolve!: (value: unknown) => void
  apiRequest.mockImplementation((path, token, init) => {
    if (init?.method === 'PUT') return Promise.reject(new Error('forbidden'))
    if (path.includes('/documents/doc-a/evidence?')) return path.includes('after_ordinal=99')
      ? new Promise(yes => { resolve = yes }) : Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', document_id: 'doc-a',
        parse_status: 'complete', parser_version: 'xlsx-v1', coverage_issues: [], evidence: first, next_ordinal: 99 })
    return read(path, token, init)
  })
  await wrapper.setProps({ refreshKey: 1 }); await flushPromises()
  await wrapper.get('[data-test="binding-load-more"]').trigger('click'); await flushPromises()
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  resolve({ case_id: 'case-a', snapshot_id: 'snap-a', document_id: 'doc-a', parse_status: 'complete',
    parser_version: 'xlsx-v1', coverage_issues: [], evidence: [{ ...block, id: 'late', ordinal: 100 }], next_ordinal: 100 })
  await flushPromises()
  expect(wrapper.find('[data-test="binding-entry-form"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-range-preview"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-load-more"]').exists()).toBe(false)
})

test('an evidence access rejection stops editing before any mutation', async () => {
  const wrapper = await editor()
  const first = Array.from({ length: 100 }, (_, ordinal) => ({ ...block, id: `part-${ordinal}`, ordinal }))
  apiRequest.mockImplementation((path, token, init) => path.includes('/documents/doc-a/evidence?')
    ? path.includes('after_ordinal=99') ? Promise.reject(new Error('not_found')) : Promise.resolve({
      case_id: 'case-a', snapshot_id: 'snap-a', document_id: 'doc-a', parse_status: 'complete', parser_version: 'xlsx-v1',
      coverage_issues: [], evidence: first, next_ordinal: 99 }) : read(path, token, init))
  await wrapper.setProps({ refreshKey: 1 }); await flushPromises()
  await wrapper.get('[data-test="binding-load-more"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[data-test="binding-entry-form"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-range-preview"]').exists()).toBe(false)
  expect(writes()).toHaveLength(0)
})
