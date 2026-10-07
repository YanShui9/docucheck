// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ReviewPanel from './ReviewPanel.vue'
const { apiRequest, downloadRunExport, submitDynamicFactCorrection } = vi.hoisted(() => ({
  apiRequest: vi.fn(),
  downloadRunExport: vi.fn(),
  submitDynamicFactCorrection: vi.fn(),
}))
vi.mock('../api', () => ({ apiRequest, downloadRunExport, submitDynamicFactCorrection }))
vi.mock('../auth', () => ({ accessToken: async () => 'token' }))

function checksPayload(overrides: object = {}) {
  return { run_id: 'run-r', case_id: 'case-a', snapshot_id: 'snap-1', revision: 2,
    run_status: 'completed', is_current: true, checklist_version_id: 'ver-1',
    checks: [
      { criterion_id: 'c-qty', item_id: 'apple', item_name: '苹果', criterion_name: '数量',
        supplier: 'vendor-a', field_key: 'c-qty', processing_status: 'complete',
        business_status: '满足', reason_code: 'threshold_met',
        evidence_refs: ['ev-1'], reason_evidence_refs: [], fact_ids: ['f-1'],
        rule_version: 'dyn-v1', is_formal: true },
      { criterion_id: 'c-cert', item_id: 'apple', item_name: '苹果', criterion_name: '检疫证明',
        supplier: 'vendor-a', field_key: 'c-cert', processing_status: 'partial',
        business_status: null, reason_code: 'coverage_incomplete',
        evidence_refs: [], fact_ids: [], rule_version: 'dyn-v1', is_formal: false },
    ], ...overrides }
}
function progressPayload(overrides: object = {}) {
  return { run_id: 'run-r', workflow: 'review', run_status: 'completed',
    checklist_version_id: 'ver-1', validated_batches: 2, planned_batches: 2,
    coverage: { 'vendor-a': true }, ...overrides }
}
function factsPayload(overrides: object = {}) {
  return { run_id: 'run-r', case_id: 'case-a', snapshot_id: 'snap-1', revision: 2,
    is_current: true, available: true, checklist_version_id: 'ver-1', payload_digest: 'digest-1',
    fact_sets: [{ fact_set_id: 'fs-1', supplier: 'vendor-a', field_key: 'c-qty',
      criterion_id: 'c-qty',
      facts: [{ fact_id: 'f-1', value: '25', unit: '箱', raw_value: '25', raw_unit: '箱',
        evidence_refs: ['ev-1'], quote: '苹果 数量25箱', role: 'quote', supplier: 'vendor-a',
        criterion_id: 'c-qty', item_id: 'apple' }] }], ...overrides }
}

function setupApi(checks: object, facts: object | Error = factsPayload(),
                  progress: object | Error = progressPayload()) {
  apiRequest.mockImplementation((path: string) => {
    if (path.endsWith('/checks')) return Promise.resolve(checks)
    if (path.endsWith('/progress')) {
      return progress instanceof Error ? Promise.reject(progress) : Promise.resolve(progress)
    }
    if (path.endsWith('/fact-corrections')) return Promise.resolve({ run_id: 'run-r', corrections: [] })
    if (path.endsWith('/facts')) {
      return facts instanceof Error ? Promise.reject(facts) : Promise.resolve(facts)
    }
    throw new Error(path)
  })
}

const wrappers: ReturnType<typeof mount>[] = []
function setup(props: Record<string, unknown> = {}) {
  const wrapper = mount(ReviewPanel, { props: { caseId: 'case-a', runId: 'run-r',
    snapshotId: 'snap-1', revision: 2, canWrite: true,
    supplierNames: { 'vendor-a': '供应商甲' }, ...props } })
  wrappers.push(wrapper)
  return wrapper
}
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount());
  [apiRequest, downloadRunExport, submitDynamicFactCorrection].forEach(fn => fn.mockReset()) })

test('renders the dynamic matrix grouped by item with supplier display names and statuses', async () => {
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('苹果')
  expect(wrapper.text()).toContain('数量')
  expect(wrapper.text()).toContain('检疫证明')
  expect(wrapper.text()).toContain('供应商甲')
  expect(wrapper.text()).toContain('满足')
  expect(wrapper.text()).toContain('处理未完成')
  expect(wrapper.text()).toContain('正式')
  expect(wrapper.text()).toContain('非正式')
})

test('historical runs are labelled and never presented as current conclusions', async () => {
  setupApi(checksPayload({ is_current: false }))
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('历史结果')
})

test('evidence links open the original document panel', async () => {
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text().includes('查看来源'))!.trigger('click')
  expect(wrapper.emitted('openEvidence')).toEqual([
    [{ caseId: 'case-a', snapshotId: 'snap-1', evidenceId: 'ev-1' }],
  ])
})

test('exports the formal report as Excel with JSON available', async () => {
  downloadRunExport.mockResolvedValue(new Blob(['x']))
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="export-xlsx"]').trigger('click')
  await flushPromises()
  expect(downloadRunExport).toHaveBeenCalledWith('run-r', 'xlsx', expect.anything())
})

test('incomplete runs show batch progress without pretending success', async () => {
  setupApi(checksPayload({ run_status: 'partial' }), factsPayload({ available: false }),
    progressPayload({ run_status: 'partial', validated_batches: 1, planned_batches: 2,
      coverage: { 'vendor-a': false } }))
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('部分完成')
  expect(wrapper.text()).toContain('1 / 2')
})

test('fact corrections reuse the original evidence and payload digest', async () => {
  submitDynamicFactCorrection.mockResolvedValue({ correction_id: 'cor-1', run_id: 'run-r',
    review_status: 'needs_review', payload_digest: 'digest-2' })
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.get('[data-test="fact-select"]').text()).toContain('数量')
  await wrapper.get('[data-test="fact-select"]').setValue('fs-1/f-1')
  await wrapper.get('[data-test="correction-value"]').setValue('30')
  await wrapper.get('[data-test="correction-unit"]').setValue('箱')
  await wrapper.get('[data-test="correction-reason"]').setValue('与供应商确认实际为30箱')
  await wrapper.get('form.correction-form').trigger('submit')
  await flushPromises()
  expect(submitDynamicFactCorrection).toHaveBeenCalledTimes(1)
  const [runId, body] = submitDynamicFactCorrection.mock.calls[0]
  expect(runId).toBe('run-r')
  expect(body.expected_payload_digest).toBe('digest-1')
  expect(body.expected_revision).toBe(2)
  expect(body.fact_id).toBe('f-1')
  expect(body.candidate).toMatchObject({ criterion_id: 'c-qty', item_id: 'apple',
    supplier: 'vendor-a', value: '30', unit: '箱',
    evidence_refs: ['ev-1'], quote: '苹果 数量25箱' })
  expect(wrapper.text()).toContain('需人工复核')
})

test('a changed run blocks stale corrections with a friendly message', async () => {
  submitDynamicFactCorrection.mockRejectedValue(new Error('run_changed'))
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="fact-select"]').setValue('fs-1/f-1')
  await wrapper.get('[data-test="correction-value"]').setValue('30')
  await wrapper.get('[data-test="correction-reason"]').setValue('核对')
  await wrapper.get('form.correction-form').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('结果已更新')
})

test('read-only members can inspect the report but cannot submit corrections', async () => {
  setupApi(checksPayload())
  const wrapper = setup({ canWrite: false })
  await flushPromises()
  expect(wrapper.find('[data-test="fact-select"]').exists()).toBe(false)
  expect(wrapper.text()).toContain('供应商甲')
})
