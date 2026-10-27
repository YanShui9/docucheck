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
  if (!wrapper.find('[data-test="binding-entry-form"]').exists()) await wrapper.get('[data-test="binding-add"]').trigger('click')
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
  expect(modal.find(`#${modal.attributes('aria-labelledby')}`).text()).toBe('关联采购商品')
  expect(modal.get('.binding-dialog-header').text()).toContain('水果报价.xlsx')
  expect(modal.text()).toContain('正在读取清单、关联记录和原文')
})

const pdfDocument = { ...document, original_name: '手机规格.pdf', model_name: 'PHONE-A' }
const pdfBlocks = [
  { ...block, id: 'pdf-target', sheet: null, cell: null, page_idx: 0, parser_version: 'pdf-v1', source_text: '型号：PHONE-A' },
  { ...block, id: 'pdf-other', ordinal: 1, sheet: null, cell: null, page_idx: 1, parser_version: 'pdf-v1', source_text: '型号：PHONE-B' },
  { ...block, id: 'pdf-body', ordinal: 2, sheet: null, cell: null, page_idx: 1, parser_version: 'pdf-v1', source_text: '容量 128GB' },
]
const pdfExclusion = { document_id: 'doc-a', source_sha256: document.source_sha256, parser_version: 'pdf-v1', page: 2,
  other_sku: 'PHONE-B', sku_anchor: { evidence_refs: ['pdf-other'], quote: '型号：PHONE-B' }, reason: '不属于本次采购型号' }
const otherExclusion = { ...pdfExclusion, document_id: 'doc-other', page: 3,
  sku_anchor: { evidence_refs: ['other-model'], quote: '型号：PHONE-B' }, supplier: 'vendor-b', evidence_refs: ['other-model'], source_fingerprint: 'fp', anchor_fingerprint: 'afp' }
function pdfRead(exclusions: object[] = [], historical = false, source = pdfBlocks) {
  return (path: string, token?: unknown, init?: RequestInit): Promise<unknown> => {
    if (path.endsWith('/checklist')) return Promise.resolve({ ...checklist,
      confirmed_version: { ...checklist.confirmed_version, definition: { items: [{ item_id: 'phone', name: '手机', specification: '型号：PHONE-A' }], criteria: [] } } })
    if (path.endsWith('/item-bindings') && !init?.method) {
      const current = { ...version(), payload: { ...version().payload, schema_version: exclusions.length ? 2 : 1, exclusions } }
      return Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', state: historical ? 'stale' : 'confirmed', binding_revision: 1,
        current_version: historical ? null : current, historical_version: historical ? current : null })
    }
    if (path.includes('/documents/doc-a/evidence?')) return Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', document_id: 'doc-a',
      parse_status: 'complete', parser_version: 'pdf-v1', coverage_issues: [], evidence: source, next_ordinal: source.at(-1)?.ordinal ?? -1 })
    return read(path, token, init)
  }
}
async function fillExclusion(wrapper: ReturnType<typeof mount>) {
  await wrapper.get('[data-test="exclusion-add"]').trigger('click')
  await wrapper.get('[data-test="exclusion-page"]').setValue('2')
  await wrapper.get('[data-test="exclusion-sku"]').setValue('PHONE-B')
  await wrapper.get('[data-test="exclusion-anchor"]').setValue('pdf-other')
  await wrapper.get('[data-test="exclusion-reason"]').setValue('不属于本次采购型号')
}

test('stages a whole PDF page explicitly and saves all documents with strengthened confirmation', async () => {
  const wrapper = await editor({ document: pdfDocument }, pdfRead([otherExclusion]))
  await fillExclusion(wrapper)
  expect(wrapper.get('[data-test="exclusion-page-text"]').text()).toContain('容量 128GB')
  await wrapper.get('[data-test="exclusion-form"]').trigger('submit')
  expect(wrapper.get('[data-test="exclusion-pending"]').text()).toContain('待新增')
  expect(wrapper.get('.binding-dialog-footer').text()).toContain('被排除页面不属于本次任何采购品目，也不含适用于本次采购的共同条款')
  await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  const body = JSON.parse(writes()[0]![2].body)
  expect(body.exclusions_confirmed).toBe(true)
  expect(body.exclusions).toEqual([expect.objectContaining({ document_id: 'doc-other' }), pdfExclusion])
  expect(body.exclusions[0]).not.toHaveProperty('source_fingerprint')
  expect(body.entries).toHaveLength(1)
})

test('cancelling the final current exclusion explicitly saves an empty exclusion array', async () => {
  const wrapper = await editor({ document: pdfDocument }, pdfRead([pdfExclusion]))
  await wrapper.get('[data-test="exclusion-remove"]').trigger('click')
  expect(wrapper.get('[data-test="exclusion-current"]').text()).toContain('PHONE-B')
  expect(wrapper.get('[data-test="exclusion-pending"]').text()).toContain('待取消')
  await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(JSON.parse(writes()[0]![2].body)).toMatchObject({ exclusions: [], exclusions_confirmed: true })
})

test.each(['close', 'refresh'])('protects a child exclusion draft on parent %s', async action => {
  const wrapper = await editor({ document: pdfDocument }, pdfRead())
  await fillExclusion(wrapper)
  await wrapper.get(`[data-test="binding-${action}"]`).trigger('click')
  expect(wrapper.get('[data-test="binding-leave-warning"]').text()).toContain('有尚未保存的内容')
  await wrapper.get('[data-test="binding-keep-editing"]').trigger('click')
  expect((wrapper.get('[data-test="exclusion-sku"]').element as HTMLInputElement).value).toBe('PHONE-B')
})

test('unknown exclusion saves retain exact body and idempotency key and freeze editing', async () => {
  const wrapper = await editor({ document: pdfDocument }, pdfRead())
  await fillExclusion(wrapper); await wrapper.get('[data-test="exclusion-form"]').trigger('submit')
  await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  const base = pdfRead()
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT' ? Promise.reject(new TypeError('offline')) : base(path, token, init))
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(wrapper.get('[data-test="exclusion-undo"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-test="binding-retry"]').trigger('click'); await flushPromises()
  expect(writes()[1]![2]).toEqual(writes()[0]![2])
})

test('stale exclusions are reference only and are never silently staged', async () => {
  const wrapper = await editor({ document: pdfDocument }, pdfRead([pdfExclusion], true))
  expect(wrapper.get('[data-test="exclusion-history"]').text()).toContain('需要重新确认')
  expect(wrapper.find('[data-test="exclusion-current"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="exclusion-pending"]').exists()).toBe(false)
  expect(writes()).toHaveLength(0)
})

test('reconfirming ordinary bindings after schema 2 becomes stale explicitly clears old exclusions', async () => {
  const wrapper = await editor({ document: pdfDocument }, pdfRead([pdfExclusion], true))
  await wrapper.get('[data-test="binding-item"]').setValue('phone')
  await wrapper.get('[data-test="binding-kind"]').setValue('pdf')
  await wrapper.get('[data-test="binding-start-page"]').setValue('1')
  await wrapper.get('[data-test="binding-end-page"]').setValue('1')
  await wrapper.get('[data-test="binding-anchor"]').setValue('pdf-target')
  await wrapper.get('[data-test="binding-identifier"]').setValue('sku')
  await wrapper.get('[data-test="binding-sku"]').setValue('PHONE-A')
  await wrapper.get('[data-test="binding-sku-anchor"]').setValue('pdf-target')
  await wrapper.get('[data-test="binding-entry-form"]').trigger('submit')
  expect(wrapper.get('[data-test="binding-confirm"]').element.parentElement?.textContent).toContain('排除页面')
  await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  const body = JSON.parse(writes()[0]![2]!.body as string)
  expect(body.exclusions).toEqual([])
  expect(body.exclusions_confirmed).toBe(true)
})

test('a mismatched current exclusion version is never labelled currently effective', async () => {
  const base = pdfRead([pdfExclusion])
  const wrapper = await editor({ document: pdfDocument }, async (path, token, init) => {
    const response = await base(path, token, init)
    if (!init?.method && path.endsWith('/item-bindings')) {
      const view = response as { current_version: object }
      return { ...view, current_version: { ...view.current_version, checklist_version_id: 'outdated' } }
    }
    return response
  })
  expect(wrapper.get('[data-test="binding-state"]').text()).toContain('需要刷新核对')
  expect(wrapper.find('[data-test="exclusion-current"]').exists()).toBe(false)
  expect(wrapper.get('[data-test="exclusion-history"]').text()).toContain('PHONE-B')
})

test('does not stage an exclusion before all evidence pagination has been exhausted', async () => {
  const hundred = Array.from({ length: 100 }, (_, ordinal) => ({ ...pdfBlocks[1]!, id: `block-${ordinal}`, ordinal }))
  const wrapper = await editor({ document: pdfDocument }, pdfRead([], false, hundred))
  await wrapper.get('[data-test="exclusion-add"]').trigger('click')
  expect(wrapper.text()).toContain('尚未读取完整原文')
  expect(wrapper.get('[data-test="exclusion-stage"]').attributes('disabled')).toBeDefined()
})

test.each([25000, 25100])('probes the terminal page at capacity without accepting excess blocks (%i)', async total => {
  const base = pdfRead()
  let terminalRead = false
  const wrapper = await editor({ document: pdfDocument }, async (path, token, init) => {
    if (path.includes('/evidence?')) {
      const after = Number(new URL(path, 'http://localhost').searchParams.get('after_ordinal') ?? -1)
      if (after === 24999) terminalRead = true
      const count = Math.min(100, Math.max(0, total - after - 1))
      const source = Array.from({ length: count }, (_, index) => ({ ...pdfBlocks[1]!,
        id: `capacity-${after + index + 1}`, ordinal: after + index + 1 }))
      return { case_id: 'case-a', snapshot_id: 'snap-a', document_id: 'doc-a', parse_status: 'complete',
        parser_version: 'pdf-v1', coverage_issues: [], evidence: source, next_ordinal: source.at(-1)?.ordinal ?? after }
    }
    return base(path, token, init)
  })
  // Start at the final chunk to exercise the capacity boundary without
  // repeatedly rendering 250 growing pages in this component test.
  const state = (wrapper.vm.$ as unknown as { setupState: { evidence: typeof pdfBlocks; formOpen: boolean } }).setupState
  state.formOpen = false
  state.evidence = Array.from({ length: 24900 }, (_, ordinal) => ({ ...pdfBlocks[1]!, id: `capacity-${ordinal}`, ordinal }))
  await wrapper.get('[data-test="exclusion-add"]').trigger('click')
  await wrapper.get('[data-test="exclusion-load-all"]').trigger('click'); await flushPromises()
  expect(terminalRead).toBe(true)
  expect(wrapper.find('[data-test="exclusion-load-all"]').exists()).toBe(total > 25000)
  if (total > 25000) expect(wrapper.text()).toContain('原文数量超过 25000 段')
}, 30000)

test.each([{ canWrite: false }, { busy: true }])('keeps exclusions immutable for %j', async props => {
  const wrapper = await editor({ document: pdfDocument, ...props }, pdfRead([pdfExclusion]))
  expect(wrapper.get('[data-test="exclusion-current"]').text()).toContain('PHONE-B')
  expect(wrapper.find('[data-test="exclusion-remove"]').exists() && wrapper.get('[data-test="exclusion-remove"]').attributes('disabled') === undefined).toBe(false)
  if (props.busy) expect(wrapper.text()).not.toContain('当前为只读记录')
})

test('undoing one of several exclusions restores a clean state regardless of array order', async () => {
  const wrapper = await editor({ document: pdfDocument }, pdfRead([pdfExclusion, otherExclusion]))
  await wrapper.get('[data-test="exclusion-remove"]').trigger('click')
  await wrapper.get('[data-test="exclusion-undo"]').trigger('click')
  expect(wrapper.find('[data-test="exclusion-pending"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-save"]').exists()).toBe(false)
  await wrapper.get('[data-test="binding-close"]').trigger('click')
  expect(wrapper.emitted('close')).toHaveLength(1)
})
test('states how many exclusions from other documents will be retained', async () => {
  const wrapper = await editor({ document: pdfDocument }, pdfRead([otherExclusion]))
  expect(wrapper.text()).toContain('保存时会保留其他文件的 1 个排除页')
})

test('partial evidence response never allows whole-page exclusion even if the document list is stale', async () => {
  const base = pdfRead()
  const wrapper = await editor({ document: pdfDocument }, async (path, token, init) => {
    const response = await base(path, token, init)
    return path.includes('/evidence?') ? { ...(response as object), parse_status: 'partial' } : response
  })
  await fillExclusion(wrapper)
  expect(wrapper.get('[data-test="exclusion-stage"]').attributes('disabled')).toBeDefined()
})

test('reads all continuation pages before staging and displays tail text from the selected page', async () => {
  const hundred = Array.from({ length: 100 }, (_, ordinal) => ({ ...pdfBlocks[1]!, id: `part-${ordinal}`, ordinal }))
  const base = pdfRead([], false, hundred)
  const wrapper = await editor({ document: pdfDocument }, async (path, token, init) => {
    if (path.includes('/evidence?') && path.includes('after_ordinal=99')) return { case_id: 'case-a', snapshot_id: 'snap-a', document_id: 'doc-a',
      parse_status: 'complete', parser_version: 'pdf-v1', coverage_issues: [], evidence: [{ ...pdfBlocks[2], ordinal: 100, source_text: '末尾共同条款，请人工核对' }], next_ordinal: 100 }
    return base(path, token, init)
  })
  await wrapper.get('[data-test="exclusion-add"]').trigger('click')
  await wrapper.get('[data-test="exclusion-load-all"]').trigger('click'); await flushPromises()
  await wrapper.get('[data-test="exclusion-page"]').setValue('2')
  expect(wrapper.get('[data-test="exclusion-page-text"]').text()).toContain('末尾共同条款，请人工核对')
  expect(apiRequest.mock.calls.filter(([path]) => path.includes('after_ordinal=99'))).toHaveLength(1)
})

test('keeps final confirmation outside the scrolling form', async () => {
  const wrapper = await editor()
  await stage(wrapper)
  expect(wrapper.find('.binding-dialog-footer [data-test="binding-save"]').exists()).toBe(true)
  expect(wrapper.find('.binding-dialog-footer [data-test="binding-confirm"]').exists()).toBe(true)
  expect(wrapper.get('.binding-dialog-body').find('[data-test="binding-save"]').exists()).toBe(false)
  expect(wrapper.get('.binding-dialog-body').find('[data-test="binding-entry-form"]').exists()).toBe(false)
  await wrapper.get('[data-test="binding-add"]').trigger('click')
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
  const lastControl = wrapper.findAll('.binding-dialog-footer button').at(-1)!
  const last = lastControl.element as HTMLElement
  for (const element of [first, last]) vi.spyOn(element, 'getClientRects').mockReturnValue([new DOMRect()] as unknown as DOMRectList)
  first.focus()
  await wrapper.get('[data-test="binding-close"]').trigger('keydown', { key: 'Tab', shiftKey: true })
  expect(window.document.activeElement).toBe(last)
  await lastControl.trigger('keydown', { key: 'Tab' })
  expect(window.document.activeElement).toBe(first)
})

test('successful confirmation is visible and focused in the fixed footer', async () => {
  const wrapper = await editor()
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  const feedback = wrapper.get('.binding-dialog-footer .binding-feedback')
  expect(feedback.classes()).toContain('binding-feedback-success')
  expect(feedback.attributes('role')).toBe('status')
  expect(feedback.text()).toContain('资料关联已确认')
  expect(wrapper.get('.binding-dialog-body').find('.binding-feedback').exists()).toBe(false)
  expect(window.document.activeElement).toBe(feedback.element)
})

test('a rejected save shows its reason in the fixed footer and preserves staged content', async () => {
  const wrapper = await editor()
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? Promise.reject(new Error('item_binding_invalid')) : read(path, token, init))
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  const feedback = wrapper.get('.binding-dialog-footer .binding-feedback')
  expect(feedback.classes()).toContain('binding-feedback-error')
  expect(feedback.attributes('role')).toBe('alert')
  expect(feedback.text()).toContain('重叠范围')
  expect(window.document.activeElement).toBe(feedback.element)
  expect(wrapper.get('.binding-entry-list').text()).toContain('采购品目：苹果')
  expect(wrapper.emitted('confirmed')).toBeUndefined()
  expect(writes()).toHaveLength(1)
})

test('unknown save result offers the original retry next to its warning instead of another save', async () => {
  const wrapper = await editor()
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? Promise.reject(new TypeError('Failed to fetch')) : read(path, token, init))
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  const footer = wrapper.get('.binding-dialog-footer')
  expect(footer.get('.binding-feedback').classes()).toContain('binding-feedback-warning')
  expect(footer.text()).toContain('保存结果待核对')
  expect(footer.find('[data-test="binding-retry"]').exists()).toBe(true)
  expect(footer.find('[data-test="binding-save"]').exists()).toBe(false)
  expect(writes()).toHaveLength(1)
})

test.each(['offline', 'forbidden'])('a received save receipt with a %s refresh shows one warning and no unknown-write retry', async code => {
  const wrapper = await editor()
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  let saved = false
  apiRequest.mockImplementation((path, token, init) => {
    if (init?.method === 'PUT') { saved = true; return read(path, token, init) }
    return saved && path.endsWith('/item-bindings') ? Promise.reject(new Error(code)) : read(path, token, init)
  })
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  const footer = wrapper.get('.binding-dialog-footer')
  expect(footer.findAll('.binding-feedback')).toHaveLength(1)
  expect(footer.get('.binding-feedback').classes()).toContain('binding-feedback-warning')
  expect(footer.text()).toContain('资料关联已保存')
  if (code === 'offline') expect(footer.text()).toContain('无需重复保存')
  else expect(footer.text()).toContain('没有修改权限')
  expect(footer.find('[data-test="binding-retry"]').exists()).toBe(false)
  expect(wrapper.get('.binding-dialog-body').find('[role="alert"]').exists()).toBe(false)
})

test('saving progress stays in the footer until the response is known', async () => {
  const wrapper = await editor()
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  let receipt!: (value: unknown) => void
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? new Promise(resolve => { receipt = resolve }) : read(path, token, init))
  await wrapper.get('[data-test="binding-save"]').trigger('click')
  const feedback = wrapper.get('.binding-dialog-footer .binding-feedback')
  expect(feedback.attributes('role')).toBe('status')
  expect(feedback.text()).toContain('正在保存')
  receipt(await read('', undefined, writes()[0]![2])); await flushPromises()
})

test('local validation errors are visible without dropping the current form values', async () => {
  const wrapper = await editor()
  await wrapper.get('[data-test="binding-quote"]').setValue('尚未核对的摘录')
  await wrapper.get('[data-test="binding-entry-form"]').trigger('submit')
  const feedback = wrapper.get('.binding-dialog-footer .binding-feedback')
  expect(feedback.text()).toContain('请选择已确认审查清单中的采购商品')
  expect((wrapper.get('[data-test="binding-quote"]').element as HTMLTextAreaElement).value).toBe('尚未核对的摘录')
  expect(writes()).toHaveLength(0)
})

test.each(['edit', 'add'])('starting another %s clears the previous successful confirmation message', async action => {
  const wrapper = await editor()
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  let savedEntries: unknown
  apiRequest.mockImplementation((path, token, init) => {
    if (init?.method === 'PUT') { savedEntries = JSON.parse(init.body as string).entries; return read(path, token, init) }
    return savedEntries && path.endsWith('/item-bindings') ? Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a',
      state: 'confirmed', binding_revision: 2, current_version: version(savedEntries as never), historical_version: null }) : read(path, token, init)
  })
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('资料关联已确认')
  if (action === 'edit') await wrapper.get('[data-test="binding-edit"]').trigger('click')
  else { await wrapper.get('[data-test="binding-add"]').trigger('click'); await wrapper.get('[data-test="binding-item"]').setValue('apple') }
  expect(wrapper.find('.binding-feedback').exists()).toBe(false)
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
    ? Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', state: 'stale', binding_revision: 4, current_version: null, historical_version: version([otherEntry, appleEntry]) })
    : read(path, token, init))
  await wrapper.setProps({ refreshKey: 1 }); await flushPromises()
  expect(wrapper.text()).toContain('查看旧关联')
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
  expect(wrapper.get('[data-test="binding-pending"]').text()).toContain('待保存的修改')
})

test.each(['validation_error', 'invalid_request'])('explicit validation refusal remains editable instead of retrying an invalid body: %s', async code => {
  const wrapper = await editor()
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? Promise.reject(new Error(code)) : read(path, token, init))
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[data-test="binding-retry"]').exists()).toBe(false)
  await wrapper.get('[data-test="binding-edit"]').trigger('click')
  expect(wrapper.get('[data-test="binding-entry-form"] fieldset').attributes('disabled')).toBeUndefined()
  expect(wrapper.text()).not.toContain('保存结果暂时无法确认')
  expect(wrapper.emitted('confirmed')).toBeUndefined()
})

test('an un-staged edit cannot be silently omitted by the confirmation button', async () => {
  const wrapper = await editor(); await stage(wrapper)
  await wrapper.get('[data-test="binding-add"]').trigger('click')
  await wrapper.get('[data-test="binding-item"]').setValue('clothes')
  expect(wrapper.get('[data-test="binding-save"]').attributes('disabled')).toBeDefined()
  expect(wrapper.get('[data-test="binding-confirm"]').attributes('disabled')).toBeDefined()
  expect(wrapper.text()).toContain('还有未加入清单的填写')
  expect(writes()).toHaveLength(0)
})

test('temporary upload busy state locks editing without clearing staged entries or an in-flight confirmation', async () => {
  const wrapper = await editor(); await stage(wrapper)
  await wrapper.setProps({ busy: true }); await flushPromises()
  expect(wrapper.get('[data-test="binding-pending"]').text()).toContain('待保存的修改')
  expect(wrapper.get('[data-test="binding-edit"]').attributes('disabled')).toBeDefined()
  expect(wrapper.get('[data-test="binding-save"]').attributes('disabled')).toBeDefined()
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
  await wrapper.get('[data-test="binding-edit"]').trigger('click')
  await wrapper.get('[data-test="binding-kind"]').setValue('xlsx')
  await wrapper.get('[data-test="binding-sheet"]').setValue('报价')
  await wrapper.get('[data-test="binding-start-row"]').setValue('4')
  await wrapper.get('[data-test="binding-end-row"]').setValue('4')
  await wrapper.get('[data-test="binding-entry-form"]').trigger('submit')
  expect(wrapper.get('.binding-entry-list').text()).toContain('报价!A4:B4')
  await wrapper.get('[data-test="binding-undo"]').trigger('click')
  expect(wrapper.findAll('.binding-entry-list li')).toHaveLength(0)
  expect(wrapper.find('[data-test="binding-save"]').exists()).toBe(false)
  await stage(wrapper)
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
  expect(wrapper.get('[data-test="binding-state"]').text()).toContain('尚未关联')
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
  expect(wrapper.text()).toContain('关联版本 3')
  expect(wrapper.text()).not.toContain('关联版本 2')
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
  await wrapper.get('[data-test="binding-refresh"]').trigger('click')
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

const appleEntry = { ...otherEntry, document_id: 'doc-a', source_sha256: document.source_sha256,
  item_id: 'apple', anchor: { evidence_refs: ['title'], quote: '采购品目：苹果' },
  identifier_class: 'category', evidence_refs: ['title', 'qty'] }
function currentApple(path: string, token?: unknown, init?: RequestInit) {
  return path.endsWith('/item-bindings') && !init?.method
    ? Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', state: 'confirmed', binding_revision: 1,
      current_version: version([otherEntry, appleEntry]), historical_version: null }) : read(path, token, init)
}

test('confirmed associations are the default view without an unrelated empty editor', async () => {
  const wrapper = await editor({}, currentApple)
  expect(wrapper.get('[data-test="binding-state"]').text()).toContain('已关联')
  expect(wrapper.get('[data-test="binding-current"]').text()).toContain('苹果')
  expect(wrapper.text()).toContain('无需重复关联')
  expect(wrapper.find('[data-test="binding-entry-form"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-pending"]').exists()).toBe(false)
  expect(writes()).toHaveLength(0)
})

test('cancelling a confirmed association keeps its effective value visible until explicitly saved', async () => {
  const wrapper = await editor({}, currentApple)
  await wrapper.get('[data-test="binding-remove"]').trigger('click')
  expect(wrapper.get('[data-test="binding-current"]').text()).toContain('苹果')
  expect(wrapper.get('[data-test="binding-pending"]').text()).toContain('待取消')
  expect(wrapper.text()).toContain('保存后生效')
  expect(wrapper.text()).not.toContain('此文件尚未确认对应品目')
  expect(writes()).toHaveLength(0)
  await wrapper.get('[data-test="binding-undo"]').trigger('click')
  expect(wrapper.find('[data-test="binding-pending"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-save"]').exists()).toBe(false)
})

test('editing a confirmed range shows both the effective range and the pending replacement', async () => {
  const wrapper = await editor({}, currentApple)
  await wrapper.get('[data-test="binding-edit"]').trigger('click')
  await wrapper.get('[data-test="binding-kind"]').setValue('xlsx')
  await wrapper.get('[data-test="binding-sheet"]').setValue('报价')
  await wrapper.get('[data-test="binding-start-row"]').setValue('4')
  await wrapper.get('[data-test="binding-end-row"]').setValue('4')
  await wrapper.get('[data-test="binding-entry-form"]').trigger('submit')
  expect(wrapper.get('[data-test="binding-current"]').text()).toContain('整份文件')
  expect(wrapper.get('[data-test="binding-pending"]').text()).toContain('待修改')
  expect(wrapper.get('[data-test="binding-pending"]').text()).toContain('报价!A4:B4')
  expect(wrapper.find('[data-test="binding-entry-form"]').exists()).toBe(false)
  await wrapper.get('[data-test="binding-undo"]').trigger('click')
  expect(wrapper.find('[data-test="binding-pending"]').exists()).toBe(false)
  expect(wrapper.get('[data-test="binding-current"]').text()).toContain('整份文件')
  expect(writes()).toHaveLength(0)
})

test.each(['close', 'refresh'])('unsaved typing is protected before %s and is kept when the user continues', async action => {
  const wrapper = await editor()
  await wrapper.get('[data-test="binding-item"]').setValue('apple')
  const calls = apiRequest.mock.calls.length
  await wrapper.get(`[data-test="binding-${action}"]`).trigger('click')
  expect(wrapper.get('[data-test="binding-leave-warning"]').text()).toContain('尚未保存')
  expect(wrapper.emitted('close')).toBeUndefined()
  expect(apiRequest.mock.calls).toHaveLength(calls)
  await wrapper.get('[data-test="binding-keep-editing"]').trigger('click')
  expect((wrapper.get('[data-test="binding-item"]').element as HTMLSelectElement).value).toBe('apple')
  await wrapper.get(`[data-test="binding-${action}"]`).trigger('click')
  await wrapper.get('[data-test="binding-discard"]').trigger('click'); await flushPromises()
  if (action === 'close') expect(wrapper.emitted('close')).toHaveLength(1)
  else expect((wrapper.get('[data-test="binding-item"]').element as HTMLSelectElement).value).toBe('')
  expect(writes()).toHaveLength(0)
})

test('old associations needing confirmation are distinguished from new associations without being copied', async () => {
  const wrapper = await editor({}, (path, token, init) => path.endsWith('/item-bindings') && !init?.method
    ? Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', state: 'stale', binding_revision: 2,
      current_version: null, historical_version: version([appleEntry]) }) : read(path, token, init))
  expect(wrapper.get('[data-test="binding-state"]').text()).toContain('需要重新确认')
  expect(wrapper.get('[data-test="binding-previous"]').text()).toContain('苹果')
  expect(wrapper.get('[data-test="binding-previous"]').text()).toContain('整份文件')
  expect(wrapper.text()).toContain('旧关联')
  expect(wrapper.find('[data-test="binding-current"]').exists()).toBe(false)
  expect((wrapper.get('[data-test="binding-item"]').element as HTMLSelectElement).value).toBe('')
  expect(writes()).toHaveLength(0)
})

test('an absent legacy upload identifier does not ask the user to classify nothing', async () => {
  const wrapper = await editor({ document: { ...document, model_name: null } })
  expect(wrapper.find('[data-test="binding-identifier"]').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('旧产品标识')
  expect(wrapper.text()).toContain('具体型号或货号')
})


test('saving a pending cancellation removes only that file after explicit confirmation', async () => {
  let entries = [otherEntry, appleEntry]
  const wrapper = await editor({}, (path, token, init) => {
    if (init?.method === 'PUT') entries = JSON.parse(init.body as string).entries
    return path.endsWith('/item-bindings') ? Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a',
      state: 'confirmed', binding_revision: 2, current_version: version(entries), historical_version: null }) : read(path, token, init)
  })
  await wrapper.get('[data-test="binding-remove"]').trigger('click')
  expect(writes()).toHaveLength(0)
  expect(wrapper.get('[data-test="binding-save"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(JSON.parse(writes()[0]![2].body).entries).toEqual([expect.objectContaining({ document_id: 'doc-other', item_id: 'clothes' })])
  expect(wrapper.find('[data-test="binding-current"]').exists()).toBe(false)
  expect(wrapper.get('[data-test="binding-state"]').text()).toContain('尚未关联')
  expect(wrapper.find('[data-test="binding-pending"]').exists()).toBe(false)
})

test('a pending replacement preserves confirmed values and other documents in the save request', async () => {
  const wrapper = await editor({}, currentApple)
  await wrapper.get('[data-test="binding-edit"]').trigger('click')
  await wrapper.get('[data-test="binding-kind"]').setValue('xlsx')
  await wrapper.get('[data-test="binding-sheet"]').setValue('报价')
  await wrapper.get('[data-test="binding-start-row"]').setValue('4')
  await wrapper.get('[data-test="binding-end-row"]').setValue('4')
  await wrapper.get('[data-test="binding-entry-form"]').trigger('submit')
  expect(wrapper.get('[data-test="binding-current"]').text()).toContain('整份文件')
  await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(JSON.parse(writes()[0]![2].body).entries).toEqual([
    expect.objectContaining({ document_id: 'doc-other', item_id: 'clothes' }),
    expect.objectContaining({ document_id: 'doc-a', selector: { kind: 'xlsx', sheet: '报价', start_row: 4, end_row: 4, start_column: 1, end_column: 2 } }),
  ])
  expect(appleEntry.selector).toEqual({ kind: 'whole' })
})

test.each(['snapshot_id', 'checklist_version_id'])('a mismatched %s is never described as an effective association', async field => {
  const wrapper = await editor({}, (path, token, init) => path.endsWith('/item-bindings') && !init?.method
    ? Promise.resolve({ case_id: 'case-a', snapshot_id: 'snap-a', state: 'confirmed', binding_revision: 1,
      current_version: { ...version([appleEntry]), [field]: 'different-version' }, historical_version: null }) : read(path, token, init))
  expect(wrapper.get('[data-test="binding-state"]').text()).toContain('需要刷新核对')
  expect(wrapper.find('[data-test="binding-current"]').exists()).toBe(false)
  expect(wrapper.get('[data-test="binding-add"]').attributes('disabled')).toBeDefined()
  expect(writes()).toHaveLength(0)
})

test('historical associations are labelled read only instead of effective for the current review', async () => {
  const wrapper = await editor({ historical: true }, currentApple)
  expect(wrapper.get('[data-test="binding-state"]').text()).toContain('历史关联')
  expect(wrapper.text()).not.toContain('当前生效的关联')
  expect(wrapper.find('[data-test="binding-edit"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-remove"]').exists()).toBe(false)
  expect(writes()).toHaveLength(0)
})

test.each(['close', 'refresh'])('an unknown write outcome is not offered as a discardable local edit on %s', async action => {
  const wrapper = await editor()
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? Promise.reject(new TypeError('Failed to fetch')) : read(path, token, init))
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  await wrapper.get(`[data-test="binding-${action}"]`).trigger('click')
  const warning = wrapper.get('[data-test="binding-leave-warning"]')
  expect(warning.text()).toContain('请求可能已经保存')
  expect(warning.text()).toContain('不会取消已经发出的请求')
  expect(warning.text()).not.toContain('放弃修改')
  expect(window.document.activeElement).toBe(wrapper.get('[data-test="binding-keep-editing"]').element)
  expect(wrapper.get('.binding-dialog-body').attributes('inert')).toBeDefined()
  await wrapper.get('[data-test="binding-keep-editing"]').trigger('click')
  expect(wrapper.get('.binding-dialog-body').attributes('inert')).toBeUndefined()
  expect(wrapper.find('[data-test="binding-retry"]').exists()).toBe(true)
  expect(writes()).toHaveLength(1)
})

test('a successful write with failed refresh can close without claiming the write is unsaved', async () => {
  let saved = false
  const wrapper = await editor({}, (path, token, init) => {
    if (init?.method === 'PUT') { saved = true; return read(path, token, init) }
    return saved && path.endsWith('/item-bindings') ? Promise.reject(new Error('offline')) : read(path, token, init)
  })
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  await wrapper.get('[data-test="binding-close"]').trigger('click')
  expect(wrapper.emitted('close')).toHaveLength(1)
  expect(wrapper.find('[data-test="binding-leave-warning"]').exists()).toBe(false)
  expect(writes()).toHaveLength(1)
})

test('an active edit cannot be replaced by starting another row operation', async () => {
  const wrapper = await editor({}, currentApple)
  await wrapper.get('[data-test="binding-edit"]').trigger('click')
  await wrapper.get('[data-test="binding-quote"]').setValue('苹果')
  expect(wrapper.get('[data-test="binding-remove"]').attributes('disabled')).toBeDefined()
  expect(wrapper.get('[data-test="binding-edit"]').attributes('disabled')).toBeDefined()
  expect(wrapper.find('[data-test="binding-add"]').exists()).toBe(false)
  expect((wrapper.get('[data-test="binding-quote"]').element as HTMLTextAreaElement).value).toBe('苹果')
  expect(writes()).toHaveLength(0)
})

test('an unknown outcome never presents submitted edits as definitely not effective', async () => {
  const wrapper = await editor({}, currentApple)
  apiRequest.mockImplementation((path, token, init) => init?.method === 'PUT'
    ? Promise.reject(new TypeError('Failed to fetch')) : currentApple(path, token, init))
  await wrapper.get('[data-test="binding-remove"]').trigger('click')
  await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  expect(wrapper.get('[data-test="binding-pending"]').text()).not.toContain('尚未生效')
  expect(wrapper.get('[data-test="binding-current"]').text()).not.toContain('当前生效的关联')
  expect(wrapper.get('[data-test="binding-pending"]').text()).toContain('保存结果待核实')
  expect(wrapper.get('[data-test="binding-pending"]').attributes('aria-label')).toContain('保存结果待核实')
})

test('a failed refresh of an unknown save preserves the same frozen retry and staged content', async () => {
  const wrapper = await editor()
  let failRead = false
  let attempts = 0
  apiRequest.mockImplementation((path, token, init) => {
    if (init?.method === 'PUT') return ++attempts === 1 ? Promise.reject(new TypeError('Failed to fetch')) : read(path, token, init)
    return failRead && path.endsWith('/item-bindings') ? Promise.reject(new Error('offline')) : read(path, token, init)
  })
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  failRead = true
  await wrapper.get('[data-test="binding-refresh"]').trigger('click')
  await wrapper.get('[data-test="binding-discard"]').trigger('click'); await flushPromises()
  expect(wrapper.get('[data-test="binding-pending"]').text()).toContain('苹果')
  expect(wrapper.get('[data-test="binding-retry"]').attributes('disabled')).toBeUndefined()
  expect(wrapper.text()).toContain('保存结果仍待核实')
  failRead = false
  await wrapper.get('[data-test="binding-retry"]').trigger('click'); await flushPromises()
  expect(writes()).toHaveLength(2)
  expect(writes()[0]![2]).toEqual(writes()[1]![2])
})

test.each(['forbidden', 'session_expired'])('refreshing an unknown save cannot restore editing after %s', async code => {
  const wrapper = await editor()
  let denyRead = false
  apiRequest.mockImplementation((path, token, init) => {
    if (init?.method === 'PUT') return Promise.reject(new TypeError('Failed to fetch'))
    return denyRead && path.endsWith('/item-bindings') ? Promise.reject(new Error(code)) : read(path, token, init)
  })
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  denyRead = true
  await wrapper.get('[data-test="binding-refresh"]').trigger('click')
  await wrapper.get('[data-test="binding-discard"]').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('访问已被拒绝')
  expect(wrapper.find('[data-test="binding-pending"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-retry"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-entry-form"]').exists()).toBe(false)
  denyRead = false
  await wrapper.get('[data-test="binding-refresh"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[data-test="binding-retry"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-save"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-entry-form"]').exists()).toBe(false)
  expect(writes()).toHaveLength(1)
})

test('an old unknown-save refresh failure cannot restore a frozen request after the task revision changes', async () => {
  const wrapper = await editor()
  let holdOldRead = false
  let held = false
  let revision = 2
  let rejectOldRead!: (error: Error) => void
  apiRequest.mockImplementation((path, token, init) => {
    if (init?.method === 'PUT') return Promise.reject(new TypeError('Failed to fetch'))
    if (path.endsWith('/checklist')) return Promise.resolve({ ...checklist, revision })
    if (holdOldRead && !held && path.endsWith('/item-bindings')) {
      held = true
      return new Promise((_resolve, reject) => { rejectOldRead = reject })
    }
    return read(path, token, init)
  })
  await stage(wrapper); await wrapper.get('[data-test="binding-confirm"]').setValue(true)
  await wrapper.get('[data-test="binding-save"]').trigger('click'); await flushPromises()
  holdOldRead = true
  await wrapper.get('[data-test="binding-refresh"]').trigger('click')
  await wrapper.get('[data-test="binding-discard"]').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('正在读取')
  revision = 3
  await wrapper.setProps({ revision }); await flushPromises()
  rejectOldRead(new Error('offline')); await flushPromises()
  expect(wrapper.find('[data-test="binding-pending"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="binding-retry"]').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('保存结果仍待核实')
  expect(wrapper.get('[data-test="binding-item"]').attributes('disabled')).toBeUndefined()
  expect(writes()).toHaveLength(1)
})
