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
    run_status: 'completed', is_current: true, checklist_version_id: 'ver-1', payload_digest: 'digest-1',
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
      criterion_id: 'c-qty', rule: { type: 'numeric', operator: 'gte', value: '10', unit: '箱' },
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
    if (path.endsWith('/fact-corrections')) return Promise.resolve({ run_id: 'run-r',
      payload_digest: (facts as ReturnType<typeof factsPayload>).payload_digest, corrections: [] })
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
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.restoreAllMocks();
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
  setupApi(checksPayload({ is_current: false }), factsPayload({ is_current: false }))
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
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
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
  expect(apiRequest.mock.calls.filter(([path]) => path.endsWith('/checks'))).toHaveLength(2)
  // The mocked server still returns the old digest, so no stale formal matrix may survive.
  expect(wrapper.find('.matrix-table').exists()).toBe(false)
})

async function fillCorrection(wrapper: ReturnType<typeof mount>, value: string | boolean = '30') {
  await wrapper.get('[data-test="fact-select"]').setValue('fs-1/f-1')
  await wrapper.get('[data-test="correction-value"]').setValue(value)
  await wrapper.get('[data-test="correction-reason"]').setValue('核对同一原文中的备选')
}

test('reloads authoritative checks, facts and audit before the next correction', async () => {
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  await fillCorrection(wrapper)
  const refreshedChecks = checksPayload({ payload_digest: 'digest-2' })
  refreshedChecks.checks.forEach(c => { c.is_formal = false })
  const refreshedFacts = factsPayload({ payload_digest: 'digest-2' })
  setupApi(refreshedChecks, refreshedFacts)
  const baseApi = apiRequest.getMockImplementation()!
  apiRequest.mockImplementation((path: string) => path.endsWith('/fact-corrections')
    ? Promise.resolve({ run_id: 'run-r', payload_digest: 'digest-2', corrections: [{
      correction_id: 'cor-1', reason: '后端保存的原因', review_status: 'needs_review',
      old_fact: refreshedFacts.fact_sets[0].facts[0], corrected_fact: refreshedFacts.fact_sets[0].facts[0],
    }] }) : baseApi(path))
  submitDynamicFactCorrection.mockResolvedValue({ run_id: 'run-r', payload_digest: 'digest-2' })
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('后端保存的原因')
  expect(wrapper.findAll('.matrix-status').every(c => c.text().endsWith('非正式'))).toBe(true)
  await fillCorrection(wrapper, '35')
  await wrapper.get('form').trigger('submit')
  expect(submitDynamicFactCorrection.mock.calls[1][1].expected_payload_digest).toBe('digest-2')
})

test('a saved correction with failed refresh hides stale conclusions and retries reads only', async () => {
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  await fillCorrection(wrapper)
  submitDynamicFactCorrection.mockResolvedValue({ run_id: 'run-r', payload_digest: 'digest-2' })
  apiRequest.mockRejectedValue(new Error('offline'))
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('修正已保存')
  expect(wrapper.find('.matrix-table').exists()).toBe(false)
  expect(wrapper.find('[data-test="export-xlsx"]').exists()).toBe(false)
  expect(wrapper.emitted('correctionSubmitted')?.[0]).toEqual([
    { caseId: 'case-a', runId: 'run-r', snapshotId: 'snap-1' }, expect.any(Promise)])
  setupApi(checksPayload({ payload_digest: 'digest-2' }), factsPayload({ payload_digest: 'digest-2' }))
  await wrapper.get('.chat-error button').trigger('click')
  await flushPromises()
  expect(submitDynamicFactCorrection).toHaveBeenCalledTimes(1)
  expect(wrapper.find('.matrix-table').exists()).toBe(true)
})

test.each(['resolve', 'reject'])('late correction %s cannot alter a different report', async result => {
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  await fillCorrection(wrapper)
  let settle!: (value: unknown) => void
  submitDynamicFactCorrection.mockImplementation(() => new Promise((resolve, reject) => {
    settle = result === 'resolve' ? resolve : reject
  }))
  await wrapper.get('form').trigger('submit')
  setupApi(checksPayload({ run_id: 'run-b' }), factsPayload({ run_id: 'run-b' }), progressPayload({ run_id: 'run-b' }))
  const api = apiRequest.getMockImplementation()!
  apiRequest.mockImplementation((path: string) => path.endsWith('/fact-corrections')
    ? Promise.resolve({ run_id: 'run-b', payload_digest: 'digest-1', corrections: [] }) : api(path))
  await wrapper.setProps({ runId: 'run-b' })
  await flushPromises()
  settle(result === 'resolve' ? { run_id: 'run-r', payload_digest: 'digest-2' } : new Error('forbidden'))
  await flushPromises()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.find('.correction-audit').exists()).toBe(false)
  expect(wrapper.get('[data-test="fact-select"]').element).toHaveProperty('value', '')
})

test('failed auxiliary reads clear the old report and prevent correction', async () => {
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  await fillCorrection(wrapper)
  setupApi(checksPayload({ run_id: 'run-b' }), new Error('offline'))
  await wrapper.setProps({ runId: 'run-b' })
  await flushPromises()
  expect(wrapper.find('[data-test="fact-select"]').exists()).toBe(false)
  expect(wrapper.find('[role="alert"]').exists()).toBe(true)
  expect(wrapper.find('.matrix-table').exists()).toBe(false)
})

test.each([
  { type: 'presence', values: undefined, initial: true, corrected: false },
  { type: 'enum', values: ['红色'], initial: '红色', corrected: '绿色' },
])('$type corrections use typed values without a required unit', async scenario => {
  const payload = factsPayload()
  const group = payload.fact_sets[0]
  Object.assign(group.rule, { type: scenario.type, values: scenario.values })
  Object.assign(group.facts[0], { value: scenario.initial, raw_value: scenario.initial, unit: '', raw_unit: '' })
  setupApi(checksPayload(), payload)
  const wrapper = setup()
  await flushPromises()
  await fillCorrection(wrapper, scenario.corrected)
  expect(wrapper.find('[data-test="correction-unit"]').exists()).toBe(false)
  submitDynamicFactCorrection.mockRejectedValue(new Error('unsupported_correction'))
  await wrapper.get('form').trigger('submit')
  expect(submitDynamicFactCorrection.mock.calls[0][1].candidate).toMatchObject({ value: scenario.corrected, unit: '' })
})

test('manual rules allow correcting source text without inventing a unit or a formal decision', async () => {
  const payload = factsPayload()
  Object.assign(payload.fact_sets[0].rule, { type: 'manual' })
  Object.assign(payload.fact_sets[0].facts[0], { raw_unit: '', unit: '', raw_value: '需冷藏', value: '需冷藏' })
  setupApi(checksPayload(), payload)
  const wrapper = setup()
  await flushPromises()
  await fillCorrection(wrapper, '需冷冻')
  submitDynamicFactCorrection.mockRejectedValue(new Error('unsupported_correction'))
  await wrapper.get('form').trigger('submit')
  expect(submitDynamicFactCorrection.mock.calls[0][1].candidate).toMatchObject({ value: '需冷冻', unit: '' })
})

test('queued reports without a payload remain readable but cannot be corrected', async () => {
  setupApi(checksPayload({ run_status: 'queued', payload_digest: null, checks: [] }),
    factsPayload({ available: false, payload_digest: null, fact_sets: [] }),
    progressPayload({ run_status: 'queued', planned_batches: 0 }))
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('排队中')
  expect(wrapper.find('[data-test="fact-select"]').exists()).toBe(false)
})

test.each(['snapshotId', 'revision', 'caseId', 'canWrite'])('scope change in %s clears stale correction inputs', async key => {
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  await fillCorrection(wrapper)
  apiRequest.mockRejectedValue(new Error('offline'))
  await wrapper.setProps({ [key]: key === 'revision' ? 3 : key === 'canWrite' ? false : 'new-scope' })
  await flushPromises()
  expect(wrapper.find('[data-test="fact-select"]').exists()).toBe(false)
  expect(wrapper.find('.matrix-table').exists()).toBe(false)
})

test('retry can read a newer coherent report if another correction followed the saved one', async () => {
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  await fillCorrection(wrapper)
  submitDynamicFactCorrection.mockResolvedValue({ run_id: 'run-r', payload_digest: 'digest-2' })
  setupApi(checksPayload({ payload_digest: 'digest-3' }), factsPayload({ payload_digest: 'digest-3' }))
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('修正已保存')
  await wrapper.get('.chat-error button').trigger('click')
  await flushPromises()
  expect(wrapper.find('.matrix-table').exists()).toBe(true)
  expect(submitDynamicFactCorrection).toHaveBeenCalledTimes(1)
})

test('mixed report digests cannot display a formal matrix or allow editing', async () => {
  setupApi(checksPayload(), factsPayload({ payload_digest: 'other' }))
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.find('.matrix-table').exists()).toBe(false)
  expect(wrapper.text()).toContain('结果已更新')
})

test('a completed download from the old run is discarded after switching reports', async () => {
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  let resolve!: (blob: Blob) => void
  downloadRunExport.mockImplementation(() => new Promise(done => { resolve = done }))
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  await wrapper.get('[data-test="export-xlsx"]').trigger('click')
  await wrapper.setProps({ canWrite: false })
  await flushPromises()
  resolve(new Blob(['x']))
  await flushPromises()
  expect(click).not.toHaveBeenCalled()
  click.mockRestore()
})

test('JSON download is named as a report, without claiming it contains the correction audit', async () => {
  setupApi(checksPayload())
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.get('[data-test="export-json"]').text()).toBe('导出报告 JSON')
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
