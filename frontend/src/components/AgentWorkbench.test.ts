// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AgentWorkbench from './AgentWorkbench.vue'
import type { DocumentsResult } from '../api'

const { apiRequest, downloadRunExport } = vi.hoisted(() => ({
  apiRequest: vi.fn(), downloadRunExport: vi.fn(),
}))
vi.mock('../auth', () => ({ accessToken: async () => 'token' }))
vi.mock('../api', () => ({ apiRequest, downloadRunExport }))

const document: DocumentsResult = {
  case_id: 'case-1', revision: 1, snapshot_id: 'snapshot-1',
  documents: [{ id: 'doc-1', original_name: 'quote.xlsx', role: 'quote',
    supplier_handle: 'vendor-a', supplier_name: '供应商甲', model_name: 'Model-X1',
    parse_status: 'complete', source_sha256: 'hash' }],
}

function mockReads(formal: boolean, suppliers = ['vendor-a']) {
  apiRequest.mockImplementation((path: string) => {
    if (path.startsWith('/cases/case-1/runs')) return Promise.resolve({
      runs: [{ id: 'run-1', conversation_id: 'conversation-1', snapshot_id: 'snapshot-1',
        revision: 1, mode: 'thorough', status: 'completed', question: '完整审查八项',
        answer_text: '内存：满足', error_code: null, created_at: '', updated_at: '' }],
      next_offset: null,
    })
    if (path === '/agent-runs/run-1/checks') return Promise.resolve({
      run_id: 'run-1', case_id: 'case-1', snapshot_id: 'snapshot-1',
      revision: 1, run_status: 'completed', is_current: formal,
      checks: suppliers.flatMap(supplier => ([
        'memory', 'storage', 'screen_size', 'weight', 'warranty',
        'quantity', 'unit_price', 'delivery_days',
      ] as const).map(field => ({ check_result_id: `${supplier}-${field}`,
        snapshot_id: 'snapshot-1', supplier, field_key: field,
        processing_status: 'complete', business_status: '满足',
        reason_code: 'threshold_met', evidence_refs: ['evidence-1'],
        reason_evidence_refs: ['evidence-1'], fact_ids: [],
        rule_version: 'memory-v1', is_formal: formal }))),
    })
    if (path === '/agent-runs/run-1/facts') return Promise.resolve({
      available: false, fact_sets: [],
    })
    if (path === '/agent-runs/run-1/events') return Promise.resolve({ events: [] })
    if (path === '/agent-runs/run-1') return Promise.resolve({
      workflow: 'legacy', correction_id: null, outgoing_correction_id: null,
    })
    throw new Error(`Unexpected path: ${path}`)
  })
}

afterEach(() => { apiRequest.mockReset(); downloadRunExport.mockReset(); vi.unstubAllGlobals() })

test('matrix reads structured checks and opens evidence in the bound snapshot', async () => {
  mockReads(true)
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: true,
  } })
  await flushPromises()
  expect(wrapper.findAll('.matrix-table tbody tr')).toHaveLength(8)
  expect(wrapper.text()).toContain('满足 · 正式')
  await wrapper.find('.evidence-links button').trigger('click')
  expect(wrapper.emitted('openEvidence')?.[0]).toEqual([{
    caseId: 'case-1', snapshotId: 'snapshot-1', evidenceId: 'evidence-1',
  }])
  wrapper.unmount()
})

test('historical results have a visible nonformal label and no submit controls', async () => {
  mockReads(false)
  const current: DocumentsResult = { ...document, revision: 2,
    snapshot_id: 'snapshot-2' }
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: current, historyDocuments: document,
    selectedHistoryId: 'snapshot-1', canWrite: true,
  } })
  await flushPromises()
  expect(wrapper.text()).toContain('历史快照')
  expect(wrapper.text()).toContain('满足 · 非正式')
  expect(wrapper.find('#agent-question').exists()).toBe(false)
  wrapper.unmount()
})

test('three suppliers occupy all 24 matrix cells', async () => {
  mockReads(true, ['vendor-a', 'vendor-b', 'vendor-c'])
  const documents: DocumentsResult = { ...document, documents: [
    ...document.documents,
    { ...document.documents[0], id: 'doc-2', supplier_handle: 'vendor-b' },
    { ...document.documents[0], id: 'doc-3', supplier_handle: 'vendor-c' },
  ] }
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents, historyDocuments: null,
    selectedHistoryId: null, canWrite: true,
  } })
  await flushPromises()
  expect(wrapper.findAll('.matrix-table thead th')).toHaveLength(4)
  expect(wrapper.findAll('.matrix-table tbody td')).toHaveLength(24)
  expect(wrapper.findAll('.matrix-table .matrix-status')).toHaveLength(24)
  wrapper.unmount()
})

test('selected historical Run can be exported with a nonformal notice', async () => {
  mockReads(false)
  downloadRunExport.mockResolvedValue(new Blob(['export']))
  const createObjectURL = vi.fn().mockReturnValue('blob:export')
  const revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  const current: DocumentsResult = { ...document, revision: 2,
    snapshot_id: 'snapshot-2' }
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: current, historyDocuments: document,
    selectedHistoryId: 'snapshot-1', canWrite: false,
  } })
  await flushPromises()
  expect(wrapper.text()).toContain('导出包含非正式结果')
  vi.useFakeTimers()
  await wrapper.find('[data-test="export-json"]').trigger('click')
  await Promise.resolve()
  await Promise.resolve()
  expect(downloadRunExport).toHaveBeenCalledWith('run-1', 'json', expect.any(Function))
  expect(click).toHaveBeenCalledOnce()
  expect(revokeObjectURL).not.toHaveBeenCalled()
  vi.advanceTimersByTime(1000)
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:export')
  vi.useRealTimers()
  wrapper.unmount()
  click.mockRestore()
})

test('late polling response cannot replace a newer Run state', async () => {
  mockReads(false)
  const fallback = apiRequest.getMockImplementation()!
  let resolveOld!: (value: unknown) => void
  const oldResponse = new Promise(resolve => { resolveOld = resolve })
  let requests = 0
  const run = { id: 'run-1', conversation_id: 'conversation-1',
    snapshot_id: 'snapshot-1', revision: 1, mode: 'standard',
    question: '审查内存', answer_text: null, error_code: null,
    created_at: '', updated_at: '' }
  apiRequest.mockImplementation((path: string) => {
    if (path.startsWith('/cases/case-1/runs')) {
      requests++
      return requests === 1 ? oldResponse : Promise.resolve({
        runs: [{ ...run, status: 'queued' }], next_offset: null,
      })
    }
    return fallback(path)
  })
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: true,
  } })
  await wrapper.find('.run-list-title button').trigger('click')
  await flushPromises()
  expect(wrapper.find('.run-item').text()).toContain('排队中')
  resolveOld({ runs: [{ ...run, status: 'partial' }], next_offset: null })
  await flushPromises()
  expect(wrapper.find('.run-item').text()).toContain('排队中')
  wrapper.unmount()
})

test('correction finishes against its original Case after the view changes', async () => {
  mockReads(true)
  const fallback = apiRequest.getMockImplementation()!
  let acceptCorrection!: (value: unknown) => void
  const submitted = new Promise(resolve => { acceptCorrection = resolve })
  apiRequest.mockImplementation((path: string, ...args: unknown[]) => {
    if (path === '/agent-runs/run-1/facts') return Promise.resolve({
      available: true, fact_sets: [{ fact_set_id: 'set-1', field_key: 'storage',
        supplier: 'vendor-a', facts: [{ fact_id: 'fact-1', role: 'quote',
          raw_value: '1', raw_unit: 'TB', evidence_refs: ['evidence-1'] }] }],
    })
    if (path === '/cases/case-1/corrections') return submitted
    if (path.startsWith('/cases/case-2/runs')) return Promise.resolve({
      runs: [], next_offset: null,
    })
    if (path === '/cases/case-1/corrections/correction-1/recheck') {
      return Promise.resolve({ review_status: 'needs_review' })
    }
    if (path === '/cases/case-1/corrections/correction-1') return Promise.resolve({})
    return fallback(path, ...args)
  })
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: true,
  } })
  await flushPromises()
  await wrapper.find('.correction-form select').setValue('set-1/fact-1')
  await wrapper.find('.correction-grid input').setValue('512')
  await wrapper.findAll('.correction-grid input')[1].setValue('GB')
  await wrapper.find('.correction-form textarea').setValue('按同一原文修正')
  await wrapper.find('.correction-form').trigger('submit')
  await wrapper.setProps({ caseId: 'case-2', documents: {
    ...document, case_id: 'case-2', snapshot_id: 'snapshot-2',
  } })
  acceptCorrection({ correction_id: 'correction-1' })
  await flushPromises()
  expect(apiRequest.mock.calls.some(([path]) =>
    path === '/cases/case-1/corrections/correction-1/recheck')).toBe(true)
  expect(apiRequest.mock.calls.some(([path]) =>
    String(path).includes('/cases/case-2/corrections/'))).toBe(false)
  expect(wrapper.emitted('changed')).toBeUndefined()
  expect(wrapper.text()).not.toContain('操作未完成')
  wrapper.unmount()
})

test('followup continues the selected Run conversation', async () => {
  mockReads(true)
  const fallback = apiRequest.getMockImplementation()!
  const olderRun = { id: 'run-older', conversation_id: 'conversation-older',
    snapshot_id: 'snapshot-1', revision: 1, mode: 'standard', status: 'completed',
    question: '供应商 A 内存', answer_text: '资料冲突', error_code: null,
    created_at: '', updated_at: '' }
  apiRequest.mockImplementation((path: string, ...args: unknown[]) => {
    if (path.startsWith('/cases/case-1/runs')) {
      return Promise.resolve(fallback(path).then((response: { runs: unknown[] }) => ({
        ...response, runs: [...response.runs, olderRun],
      })))
    }
    if (path.startsWith('/agent-runs/run-older')) {
      return fallback(path.replace('run-older', 'run-1'), ...args)
    }
    if (path.endsWith('/messages')) return Promise.resolve({ run_id: 'run-older' })
    return fallback(path, ...args)
  })
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: true,
  } })
  await flushPromises()
  await wrapper.findAll('.run-item')[1].trigger('click')
  await flushPromises()
  await wrapper.find('#agent-question').setValue('你根据哪两处判断冲突？')
  await wrapper.find('.compose-actions .button-primary').trigger('click')
  await flushPromises()
  expect(apiRequest.mock.calls.some(([path]) =>
    path === '/conversations/conversation-older/messages')).toBe(true)
  wrapper.unmount()
})

function modernReads(workflow: 'assistant' | 'review', resultFailure = false) {
  mockReads(true)
  const fallback = apiRequest.getMockImplementation()!
  apiRequest.mockImplementation((path: string, ...args: unknown[]) => {
    if (path.startsWith('/cases/case-1/runs')) return Promise.resolve({
      runs: [{ id: 'run-1', conversation_id: 'conversation-1', snapshot_id: 'snapshot-1',
        revision: 1, mode: 'standard', status: 'completed', question: '苹果报价是多少？',
        answer_text: 'RAW_UNVERIFIED', error_code: null, created_at: '', updated_at: '' }], next_offset: null,
    })
    if (path === '/agent-runs/run-1') return Promise.resolve({
      workflow, correction_id: null, outgoing_correction_id: null,
    })
    if (path === '/agent-runs/run-1/assistant-result') {
      if (resultFailure) return Promise.reject(new Error('assistant_source_changed'))
      return Promise.resolve({ run_id: 'run-1', case_id: 'case-1', snapshot_id: 'snapshot-1',
        revision: 1, is_current: true, checklist_revision: 1,
        result: { kind: 'evidence', answer: '原文：90元/箱，含税', candidate: null, changes: [],
          citations: [{ evidence_id: 'price-source', quote: '90元/箱，含税',
            file_name: '苹果报价.xlsx', location: '报价!C4' }] } })
    }
    if (path === '/agent-runs/run-1/checks' && workflow === 'review') return Promise.resolve({
      run_id: 'run-1', case_id: 'case-1', snapshot_id: 'snapshot-1', revision: 1,
      run_status: 'completed', is_current: true, checklist_version_id: 'checklist-1',
      checks: [{ criterion_id: 'price', criterion_name: '含税单价', item_id: 'apple',
        item_name: '苹果', supplier: 'vendor-a', business_status: '满足' }],
    })
    return fallback(path, ...args)
  })
}

test('assistant history shows verified excerpts and source links without a legacy matrix', async () => {
  modernReads('assistant')
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: true, historyOnly: true,
  } })
  await flushPromises()
  expect(wrapper.text()).toContain('90元/箱，含税')
  expect(wrapper.text()).not.toContain('RAW_UNVERIFIED')
  expect(wrapper.text()).not.toContain('八字段核验矩阵')
  expect(wrapper.text()).not.toContain('完整审查八项')
  expect(wrapper.find('#agent-question').exists()).toBe(false)
  await wrapper.find('[data-test="assistant-source"]').trigger('click')
  expect(wrapper.emitted('openEvidence')?.[0]).toEqual([{
    caseId: 'case-1', snapshotId: 'snapshot-1', evidenceId: 'price-source',
  }])
  expect(apiRequest.mock.calls.some(([, , options]) => options?.method === 'POST')).toBe(false)
  wrapper.unmount()
})

test('failed verification never falls back to raw assistant history text', async () => {
  modernReads('assistant', true)
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: false, historyOnly: true,
  } })
  await flushPromises()
  expect(wrapper.text()).not.toContain('RAW_UNVERIFIED')
  expect(wrapper.find('[role="alert"]').exists()).toBe(true)
  expect(wrapper.find('.matrix-table').exists()).toBe(false)
  wrapper.unmount()
})

test('dynamic review history opens the selected report instead of the fixed eight fields', async () => {
  modernReads('review')
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: true, historyOnly: true,
  } })
  await flushPromises()
  expect(wrapper.text()).not.toContain('RAW_UNVERIFIED')
  expect(wrapper.find('.matrix-table').exists()).toBe(false)
  expect(wrapper.find('.correction-form').exists()).toBe(false)
  await wrapper.find('[data-test="open-dynamic-report"]').trigger('click')
  expect(wrapper.emitted('openReview')?.[0]).toEqual([{
    caseId: 'case-1', snapshotId: 'snapshot-1', runId: 'run-1',
  }])
  wrapper.unmount()
})

test.each(['run_id', 'case_id', 'snapshot_id', 'revision'])(
  'assistant history rejects a mismatched %s before exposing the answer', async key => {
    modernReads('assistant')
    const fallback = apiRequest.getMockImplementation()!
    apiRequest.mockImplementation(async (path: string, ...args: unknown[]) => {
      const result = await fallback(path, ...args)
      return path.endsWith('/assistant-result') ? { ...result, [key]: key === 'revision' ? 9 : 'other' } : result
    })
    const wrapper = mount(AgentWorkbench, { props: {
      caseId: 'case-1', documents: document, historyDocuments: null,
      selectedHistoryId: null, canWrite: false, historyOnly: true,
    } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('90元/箱，含税')
    expect(wrapper.text()).not.toContain('RAW_UNVERIFIED')
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    wrapper.unmount()
  },
)

test('late verified assistant response cannot restore private text after changing Case', async () => {
  modernReads('assistant')
  const fallback = apiRequest.getMockImplementation()!
  let complete!: (value: unknown) => void
  let pendingValue: unknown
  const pending = new Promise(resolve => { complete = resolve })
  apiRequest.mockImplementation(async (path: string, ...args: unknown[]) => {
    if (path.startsWith('/cases/case-2/runs')) return { runs: [], next_offset: null }
    const result = await fallback(path, ...args)
    if (path.endsWith('/assistant-result')) { pendingValue = result; return pending }
    return result
  })
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: false, historyOnly: true,
  } })
  await flushPromises()
  expect(pendingValue).toBeDefined()
  expect(wrapper.text()).not.toContain('RAW_UNVERIFIED')
  await wrapper.setProps({ caseId: 'case-2', documents: {
    ...document, case_id: 'case-2', snapshot_id: 'snapshot-2',
  } })
  await flushPromises()
  complete(pendingValue)
  await flushPromises()
  expect(wrapper.text()).not.toContain('90元/箱，含税')
  expect(wrapper.find('[data-test="assistant-source"]').exists()).toBe(false)
  expect(wrapper.find('.matrix-table').exists()).toBe(false)
  wrapper.unmount()
})

test.each(['forbidden', 'not_found', 'session_expired', 'unauthorized', 'invalid_token', 'http_401', 'http_403'])(
  'history clears cached answers and questions when refresh loses access (%s)', async code => {
    modernReads('assistant')
    const fallback = apiRequest.getMockImplementation()!
    let denied = false
    apiRequest.mockImplementation((path: string, ...args: unknown[]) =>
      denied && path.startsWith('/cases/case-1/runs')
        ? Promise.reject(new Error(code)) : fallback(path, ...args))
    const wrapper = mount(AgentWorkbench, { props: {
      caseId: 'case-1', documents: document, historyDocuments: null,
      selectedHistoryId: null, canWrite: false, historyOnly: true,
    } })
    await flushPromises()
    expect(wrapper.text()).toContain('90元/箱，含税')
    denied = true
    await wrapper.find('.run-list-title button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('90元/箱，含税')
    expect(wrapper.text()).not.toContain('苹果报价是多少？')
    expect(wrapper.find('[data-test="assistant-source"]').exists()).toBe(false)
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    wrapper.unmount()
  },
)

test('a late detail response cannot restore content after the list reports lost access', async () => {
  modernReads('assistant')
  const fallback = apiRequest.getMockImplementation()!
  let complete!: (value: unknown) => void
  let pendingValue: unknown
  let denied = false
  const pending = new Promise(resolve => { complete = resolve })
  apiRequest.mockImplementation(async (path: string, ...args: unknown[]) => {
    if (denied && path.startsWith('/cases/case-1/runs')) throw new Error('forbidden')
    const result = await fallback(path, ...args)
    if (path.endsWith('/assistant-result')) { pendingValue = result; return pending }
    return result
  })
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: false, historyOnly: true,
  } })
  await flushPromises()
  denied = true
  await wrapper.find('.run-list-title button').trigger('click')
  await flushPromises()
  complete(pendingValue)
  await flushPromises()
  expect(wrapper.text()).not.toContain('90元/箱，含税')
  expect(wrapper.text()).not.toContain('苹果报价是多少？')
  expect(wrapper.find('[data-test="assistant-source"]').exists()).toBe(false)
  wrapper.unmount()
})

test('loss of access to details clears the private list as well as the answer', async () => {
  modernReads('assistant')
  const fallback = apiRequest.getMockImplementation()!
  apiRequest.mockImplementation((path: string, ...args: unknown[]) =>
    path.endsWith('/assistant-result') ? Promise.reject(new Error('forbidden')) : fallback(path, ...args))
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: false, historyOnly: true,
  } })
  await flushPromises()
  expect(wrapper.text()).not.toContain('苹果报价是多少？')
  expect(wrapper.find('.run-item').exists()).toBe(false)
  wrapper.unmount()
})

test.each(['not_found', 'invalid_token'])('legacy export denial clears private history (%s)', async code => {
  mockReads(true)
  downloadRunExport.mockRejectedValue(new Error(code))
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: false, historyOnly: true,
  } })
  await flushPromises()
  expect(wrapper.find('.matrix-table').exists()).toBe(true)
  await wrapper.find('[data-test="export-json"]').trigger('click')
  await flushPromises()
  expect(wrapper.find('.run-item').exists()).toBe(false)
  expect(wrapper.find('.matrix-table').exists()).toBe(false)
  expect(wrapper.find('.answer-text').exists()).toBe(false)
  expect(wrapper.find('[role="alert"]').exists()).toBe(true)
  wrapper.unmount()
})

test.each(['case_changed', 'access_denied'])('late legacy export does not download after %s', async boundary => {
  mockReads(true)
  const fallback = apiRequest.getMockImplementation()!
  let denied = false
  apiRequest.mockImplementation((path: string, ...args: unknown[]) => {
    if (path.startsWith('/cases/case-2/runs')) return Promise.resolve({ runs: [], next_offset: null })
    if (denied && path.startsWith('/cases/case-1/runs')) return Promise.reject(new Error('invalid_token'))
    return fallback(path, ...args)
  })
  let finish!: (value: Blob) => void
  downloadRunExport.mockReturnValue(new Promise(resolve => { finish = resolve }))
  const createObjectURL = vi.fn().mockReturnValue('blob:private-export')
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL: vi.fn() })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  const wrapper = mount(AgentWorkbench, { props: {
    caseId: 'case-1', documents: document, historyDocuments: null,
    selectedHistoryId: null, canWrite: false, historyOnly: true,
  } })
  await flushPromises()
  await wrapper.find('[data-test="export-json"]').trigger('click')
  if (boundary === 'case_changed') await wrapper.setProps({ caseId: 'case-2', documents: {
    ...document, case_id: 'case-2', snapshot_id: 'snapshot-2',
  } })
  else {
    denied = true
    await wrapper.find('.run-list-title button').trigger('click')
  }
  await flushPromises()
  finish(new Blob(['private']))
  await flushPromises()
  expect(createObjectURL).not.toHaveBeenCalled()
  expect(click).not.toHaveBeenCalled()
  wrapper.unmount()
  click.mockRestore()
})
