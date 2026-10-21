// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import App from './App.vue'

const { apiRequest, uploadDocument } = vi.hoisted(() => ({
  apiRequest: vi.fn(), uploadDocument: vi.fn(),
}))
vi.mock('./auth', () => ({
  accessToken: async () => 'token', identity: { tokenParsed: {} }, signOut: vi.fn(),
}))
vi.mock('./api', () => ({ apiRequest, uploadDocument }))

const cases = ['case-1', 'case-2'].map(id => ({
  id, project_id: 'project-1', revision: 1, created_at: '2026-10-01T00:00:00Z',
}))

function reads(path: string) {
  if (path === '/projects') return Promise.resolve({ projects: [{
    id: 'project-1', name: '采购', role: 'owner', created_at: '',
  }] })
  if (path === '/projects/project-1/cases') return Promise.resolve({ cases })
  const caseId = path.split('/')[2]
  if (path.endsWith('/documents')) return Promise.resolve({
    case_id: caseId, revision: 1, snapshot_id: `snapshot-${caseId}`, documents: [],
  })
  if (path.endsWith('/snapshots')) return Promise.resolve({
    case_id: caseId, snapshots: [], next_before_revision: null,
  })
  throw new Error(`Unexpected path: ${path}`)
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const wrappers: ReturnType<typeof mount>[] = []
async function workbench() {
  apiRequest.mockImplementation(reads)
  const wrapper = mount(App, { global: { stubs: {
    DocumentsPanel: { name: 'DocumentsPanel', props: ['selectedCase', 'notifyBindingsConfirmed', 'documents',
      'selectedHistoryId', 'historyDocuments', 'historyLoading', 'historyError', 'canWrite', 'busy'],
      template: '<div class="documents-stub">{{ selectedCase?.id }}</div>' },
    AgentWorkbench: true, EvidencePanel: true, ChatWorkspace: true,
  } } })
  wrappers.push(wrapper)
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '资料')!.trigger('click')
  await flushPromises()
  return wrapper
}

test('item binding confirmation refreshes chat currentness without resetting the documents checklist scope', async () => {
  const wrapper = await workbench()
  const documents = wrapper.findComponent({ name: 'DocumentsPanel' })
  const notify = documents.props('notifyBindingsConfirmed') as (target: { caseId: string; snapshotId: string }) => void
  await wrapper.find('.work-panel-heading button').trigger('click')
  notify({ caseId: 'case-1', snapshotId: 'snapshot-case-1' })
  await flushPromises()
  expect(wrapper.findComponent({ name: 'ChatWorkspace' }).props('bindingChanges')).toEqual({ 'snapshot-case-1': 1 })
  expect(wrapper.findComponent({ name: 'ChatWorkspace' }).props('checklistChanges')).toEqual({})
  notify({ caseId: 'case-2', snapshotId: 'snapshot-case-2' })
  notify({ caseId: 'case-1', snapshotId: 'old-snapshot' })
  await flushPromises()
  expect(wrapper.findComponent({ name: 'ChatWorkspace' }).props('bindingChanges')).toEqual({ 'snapshot-case-1': 1 })
})

afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.useRealTimers()
  apiRequest.mockReset()
  uploadDocument.mockReset()
})

test('history failure stops loading and retry reads the selected snapshot without the current documents request', async () => {
  const wrapper = await workbench()
  const history = deferred<object>()
  const path = '/cases/case-1/snapshots/history-a/documents'
  apiRequest.mockImplementation((requested: string) => requested === path ? history.promise : reads(requested))
  const documents = wrapper.findComponent({ name: 'DocumentsPanel' })
  documents.vm.$emit('selectHistory', 'history-a')
  await flushPromises()
  expect(documents.props('historyLoading')).toBe(true)
  history.reject(new Error('http_503'))
  await flushPromises()
  expect(documents.props('historyLoading')).toBe(false)
  expect(documents.props('historyError')).toContain('历史资料')
  expect(documents.props('historyDocuments')).toBeNull()
  expect(wrapper.find('.chat-main [role="alert"]').exists()).toBe(false)

  apiRequest.mockClear()
  apiRequest.mockImplementation((requested: string) => requested === path
    ? Promise.resolve({ case_id: 'case-1', revision: 7, snapshot_id: 'history-a', documents: [] })
    : Promise.reject(new Error('current_documents_unavailable')))
  documents.vm.$emit('refreshDocuments')
  await flushPromises()
  expect(apiRequest.mock.calls.map(([requested]) => requested)).toEqual([path])
  expect(documents.props('historyDocuments')).toMatchObject({ revision: 7, snapshot_id: 'history-a' })
  expect(documents.props('historyError')).toBe('')
  expect(documents.props('canWrite')).toBe(true)
  expect(documents.props('documents').snapshot_id).toBe('snapshot-case-1')
})

test.each(['success', 'failure'])('late history %s cannot replace a newer A to B to A selection', async outcome => {
  const wrapper = await workbench()
  const older = deferred<object>()
  const latest = deferred<object>()
  let aRequests = 0
  apiRequest.mockImplementation((path: string) => {
    if (path === '/cases/case-1/snapshots/history-a/documents') return ++aRequests === 1 ? older.promise : latest.promise
    if (path === '/cases/case-1/snapshots/history-b/documents') return Promise.resolve({
      case_id: 'case-1', revision: 2, snapshot_id: 'history-b', documents: [],
    })
    return reads(path)
  })
  const documents = wrapper.findComponent({ name: 'DocumentsPanel' })
  for (const id of ['history-a', 'history-b', 'history-a']) {
    documents.vm.$emit('selectHistory', id)
    await flushPromises()
  }
  latest.resolve({ case_id: 'case-1', revision: 3, snapshot_id: 'history-a', documents: [] })
  await flushPromises()
  if (outcome === 'success') older.resolve({ case_id: 'case-1', revision: 1, snapshot_id: 'history-a', documents: [] })
  else older.reject(new Error('not_found'))
  await flushPromises()
  expect(documents.props('historyDocuments')).toMatchObject({ revision: 3 })
  expect(documents.props('historyError')).toBe('')
  expect(documents.props('historyLoading')).toBe(false)
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})

test('history retry supersedes a pending request for the same snapshot', async () => {
  const wrapper = await workbench()
  const older = deferred<object>()
  const latest = deferred<object>()
  const requests = [older, latest]
  const signals: AbortSignal[] = []
  apiRequest.mockImplementation((path: string, _token: unknown, init?: RequestInit) => {
    if (path === '/cases/case-1/snapshots/history-a/documents') {
      if (init?.signal) signals.push(init.signal)
      return requests.shift()!.promise
    }
    return reads(path)
  })
  const documents = wrapper.findComponent({ name: 'DocumentsPanel' })
  documents.vm.$emit('selectHistory', 'history-a')
  await flushPromises()
  documents.vm.$emit('refreshDocuments')
  await flushPromises()
  expect(signals[0]?.aborted).toBe(true)
  latest.reject(new Error('http_503'))
  await flushPromises()
  older.resolve({ case_id: 'case-1', revision: 1, snapshot_id: 'history-a', documents: [] })
  await flushPromises()
  expect(documents.props('historyDocuments')).toBeNull()
  expect(documents.props('historyError')).toContain('历史资料')
  expect(documents.props('historyLoading')).toBe(false)
})

test('history requests time out locally even when the transport never settles', async () => {
  const wrapper = await workbench()
  vi.useFakeTimers()
  let signal: AbortSignal | null = null
  apiRequest.mockImplementation((path: string, _token: unknown, init?: RequestInit) => {
    if (path === '/cases/case-1/snapshots/history-a/documents') {
      signal = init?.signal as AbortSignal
      return new Promise(() => {})
    }
    return reads(path)
  })
  const documents = wrapper.findComponent({ name: 'DocumentsPanel' })
  documents.vm.$emit('selectHistory', 'history-a')
  await flushPromises()
  await vi.advanceTimersByTimeAsync(20_000)
  await flushPromises()
  expect(documents.props('historyLoading')).toBe(false)
  expect(documents.props('historyError')).toContain('超时')
  expect(signal).toMatchObject({ aborted: true })
  documents.vm.$emit('selectHistory', null)
  await flushPromises()
  expect(documents.props('selectedHistoryId')).toBeNull()
  expect(documents.props('historyError')).toBe('')
  expect(documents.props('documents').snapshot_id).toBe('snapshot-case-1')
})

test('the history verification panel also exposes loading, failure, retry and return for the shared history request', async () => {
  const wrapper = await workbench()
  const history = deferred<object>()
  const path = '/cases/case-1/snapshots/history-a/documents'
  apiRequest.mockImplementation((requested: string) => requested === path ? history.promise : reads(requested))
  wrapper.findComponent({ name: 'DocumentsPanel' }).vm.$emit('selectHistory', 'history-a')
  await wrapper.findAll('button').find(button => button.text() === '历史核验')!.trigger('click')
  await flushPromises()
  expect(wrapper.get('.work-panel-content [role="status"]').text()).toBe('正在读取历史资料…')
  history.reject(new Error('not_found'))
  await flushPromises()
  const alert = wrapper.get('.work-panel-content [role="alert"]')
  expect(alert.text()).toContain('该历史资料不存在，或当前账号无权访问。')
  const retry = deferred<object>()
  apiRequest.mockImplementation((requested: string) => requested === path ? retry.promise : reads(requested))
  await alert.findAll('button').find(button => button.text() === '重试')!.trigger('click')
  await flushPromises()
  expect(wrapper.find('.work-panel-content [role="alert"]').exists()).toBe(false)
  retry.reject(new Error('session_expired'))
  await flushPromises()
  await wrapper.get('.work-panel-content [role="alert"]').findAll('button')
    .find(button => button.text() === '返回当前版本')!.trigger('click')
  await flushPromises()
  expect(wrapper.find('.work-panel-content [role="alert"]').exists()).toBe(false)
  expect(wrapper.findComponent({ name: 'AgentWorkbench' }).props('selectedHistoryId')).toBeNull()
})

test.each(['return', 'switch', 'unmount'])('leaving history by %s cancels its request and ignores late failure', async destination => {
  const wrapper = await workbench()
  const history = deferred<object>()
  let signal: AbortSignal | undefined
  apiRequest.mockImplementation((path: string, _token: unknown, init?: RequestInit) => {
    if (path === '/cases/case-1/snapshots/history-a/documents') {
      signal = init?.signal ?? undefined
      return history.promise
    }
    return reads(path)
  })
  const documents = wrapper.findComponent({ name: 'DocumentsPanel' })
  documents.vm.$emit('selectHistory', 'history-a')
  await flushPromises()
  if (destination === 'return') documents.vm.$emit('selectHistory', null)
  else if (destination === 'switch') await wrapper.findAll('.case-item')[1]!.trigger('click')
  else wrapper.unmount()
  await flushPromises()
  expect(signal?.aborted).toBe(true)
  history.reject(new Error('session_expired'))
  await flushPromises()
  if (destination !== 'unmount') {
    expect(documents.props('historyLoading')).toBe(false)
    expect(documents.props('historyError')).toBe('')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  }
})

test('upload busy state blocks chat writes without changing owner permissions', async () => {
  const wrapper = await workbench()
  const upload = deferred<void>()
  uploadDocument.mockReturnValue(upload.promise)
  wrapper.findComponent({ name: 'DocumentsPanel' }).vm.$emit('upload', {
    file: new File(['test'], 'test.xlsx'), role: 'quote',
    supplierHandle: 'vendor-a', supplierName: 'Vendor A', modelName: '',
  })
  await flushPromises()
  const chat = wrapper.findComponent({ name: 'ChatWorkspace' })
  expect(chat.props('canWrite')).toBe(true)
  expect(chat.props('busy')).toBe(true)
  upload.resolve()
  await flushPromises()
  expect(chat.props('canWrite')).toBe(true)
  expect(chat.props('busy')).toBe(false)
})

test.each(['success', 'failure'])('late upload %s cannot replace a newly selected Case', async outcome => {
  const wrapper = await workbench()
  const upload = deferred<void>()
  uploadDocument.mockReturnValue(upload.promise)
  wrapper.findComponent({ name: 'DocumentsPanel' }).vm.$emit('upload', {
    file: new File(['test'], 'test.xlsx'), role: 'quote',
    supplierHandle: 'vendor-a', supplierName: 'Vendor A', modelName: 'Model-X1',
  })
  await flushPromises()
  await wrapper.findAll('.case-item')[1].trigger('click')
  await flushPromises()
  if (outcome === 'success') upload.resolve()
  else upload.reject(new Error('revision_conflict'))
  await flushPromises()
  expect(wrapper.find('.documents-stub').text()).toBe('case-2')
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(uploadDocument.mock.calls[0]?.[0]).toBe('case-1')
})

test('late Case creation does not take focus back from the users current Case', async () => {
  const wrapper = await workbench()
  const creation = deferred<object>()
  apiRequest.mockImplementation((path: string) => path === '/cases'
    ? creation.promise : reads(path))
  wrapper.findComponent({ name: 'CaseSidebar' }).vm.$emit('createCase')
  await flushPromises()
  await wrapper.findAll('.case-item')[1].trigger('click')
  await flushPromises()
  creation.resolve({ id: 'case-created', project_id: 'project-1' })
  await flushPromises()
  expect(wrapper.find('.documents-stub').text()).toBe('case-2')
})

test('late document refresh failure cannot show an alert in a different Case', async () => {
  const wrapper = await workbench()
  const refresh = deferred<object>()
  apiRequest.mockImplementation((path: string) => path === '/cases/case-1/documents'
    ? refresh.promise : reads(path))
  wrapper.findComponent({ name: 'DocumentsPanel' }).vm.$emit('refreshDocuments')
  await flushPromises()
  await wrapper.findAll('.case-item')[1].trigger('click')
  await flushPromises()
  refresh.reject(new Error('revision_conflict'))
  await flushPromises()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})

test('fact correction updates the Case list while preserving the selected historical snapshot', async () => {
  const wrapper = await workbench()
  await wrapper.findAll('button').find(button => button.text() === '历史核验')!.trigger('click')
  await flushPromises()
  apiRequest.mockImplementation((path: string) => {
    if (path === '/cases/case-1/documents') return Promise.resolve({
      case_id: 'case-1', revision: 2, snapshot_id: 'snapshot-case-1-v2', documents: [],
    })
    if (path === '/cases/case-1/snapshots') return Promise.resolve({
      case_id: 'case-1', next_before_revision: null, snapshots: [
        { id: 'snapshot-case-1-v2', revision: 2, document_count: 0 },
        { id: 'snapshot-case-1', revision: 1, document_count: 0 },
      ],
    })
    return reads(path)
  })
  wrapper.findComponent({ name: 'AgentWorkbench' }).vm.$emit('changed', {
    caseId: 'case-1', fromSnapshotId: 'snapshot-case-1',
  })
  await flushPromises()
  const agent = wrapper.findComponent({ name: 'AgentWorkbench' })
  expect(agent.props('documents').revision).toBe(2)
  expect(agent.props('selectedHistoryId')).toBe('snapshot-case-1')
  expect(agent.props('historyDocuments').revision).toBe(1)
  expect(wrapper.findAll('.case-item')[0].text()).toContain('版本 2')
})

test('history is read only and opens the selected dynamic report in the existing panel', async () => {
  apiRequest.mockImplementation(reads)
  const wrapper = mount(App, { global: { stubs: {
    DocumentsPanel: true, EvidencePanel: true, ChatWorkspace: true,
    AgentWorkbench: true,
    ReviewPanel: { name: 'ReviewPanel', props: ['caseId', 'snapshotId', 'runId'], template: '<div />' },
  } } })
  wrappers.push(wrapper)
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '历史核验')!.trigger('click')
  await flushPromises()
  const history = wrapper.findComponent({ name: 'AgentWorkbench' })
  expect(history.props('historyOnly')).toBe(true)
  history.vm.$emit('openReview', { caseId: 'case-1', snapshotId: 'snapshot-case-1', runId: 'dynamic-run' })
  await flushPromises()
  const report = wrapper.findComponent({ name: 'ReviewPanel' })
  expect(report.props()).toMatchObject({ caseId: 'case-1', snapshotId: 'snapshot-case-1', runId: 'dynamic-run' })
})

test('first interaction creates only one task and late creation cannot reclaim a different task', async () => {
  const wrapper = await workbench()
  await wrapper.findComponent({ name: 'CaseSidebar' }).vm.$emit('newTask')
  await flushPromises()
  const creation = deferred<object>()
  apiRequest.mockImplementation((path: string) => path === '/cases' ? creation.promise : reads(path))
  const prepare = wrapper.findComponent({ name: 'ChatWorkspace' }).props('prepareCase') as () => Promise<string | null>
  const first = prepare(); const second = prepare()
  await flushPromises()
  expect(apiRequest.mock.calls.filter(([path]) => path === '/cases')).toHaveLength(1)
  await wrapper.findAll('.case-item')[1].trigger('click')
  await flushPromises()
  creation.resolve({ id: 'created', project_id: 'project-1', revision: 0 })
  expect(await first).toBeNull(); expect(await second).toBeNull()
  expect(wrapper.findComponent({ name: 'ChatWorkspace' }).props('caseId')).toBe('case-2')
})

test('checklist and review panels open from chat events with persisted supplier names', async () => {
  apiRequest.mockImplementation((path: string) => {
    if (path.endsWith('/documents')) return Promise.resolve({
      case_id: 'case-1', revision: 1, snapshot_id: 'snapshot-case-1', documents: [{
        id: 'doc-1', original_name: '报价.xlsx', role: 'quote',
        supplier_handle: 'vendor-a', supplier_name: '供应商甲', model_name: null,
        parse_status: 'complete', source_sha256: 'sha',
      }],
    })
    return reads(path)
  })
  const wrapper = mount(App, { global: { stubs: {
    DocumentsPanel: true, AgentWorkbench: true, EvidencePanel: true, ChatWorkspace: true,
    ChecklistPanel: { name: 'ChecklistPanel', props: ['caseId', 'snapshotId', 'candidate', 'candidateReadOnly'],
      template: '<div class="checklist-stub">{{ caseId }}/{{ snapshotId }}</div>' },
    ReviewPanel: { name: 'ReviewPanel', props: ['caseId', 'runId', 'supplierNames'],
      template: '<div class="review-stub">{{ runId }}</div>' },
  } } })
  wrappers.push(wrapper)
  await flushPromises()
  const chat = wrapper.findComponent({ name: 'ChatWorkspace' })
  chat.vm.$emit('openChecklist', { caseId: 'case-1', snapshotId: 'snapshot-case-1', runId: 'run-a',
    candidate: { candidate: { items: [], criteria: [] }, changes: [], runId: 'run-a' } })
  await flushPromises()
  expect(wrapper.find('.work-panel-heading').text()).toContain('审查要求')
  expect(wrapper.find('.checklist-stub').text()).toBe('case-1/snapshot-case-1')
  expect(wrapper.findComponent({ name: 'ChecklistPanel' })
    .props('candidate')).toEqual({ candidate: { items: [], criteria: [] }, changes: [], runId: 'run-a' })
  wrapper.findComponent({ name: 'ChecklistPanel' }).vm.$emit('saved', { caseId: 'case-1', snapshotId: 'snapshot-case-1' })
  await flushPromises()
  expect(chat.props('checklistChanges')).toEqual({ 'snapshot-case-1': 1 })
  wrapper.findComponent({ name: 'ChecklistPanel' }).vm.$emit('confirmed', { caseId: 'case-1', snapshotId: 'snapshot-case-1' })
  await flushPromises()
  expect(chat.props('checklistChanges')).toEqual({ 'snapshot-case-1': 2 })
  chat.vm.$emit('openChecklist', { caseId: 'case-1', snapshotId: 'old-snapshot', runId: 'old-run',
    readOnly: true, candidate: { candidate: { items: [], criteria: [] }, changes: [], runId: 'old-run' } })
  await flushPromises()
  expect(wrapper.findComponent({ name: 'ChecklistPanel' }).props('snapshotId')).toBe('old-snapshot')
  expect(wrapper.findComponent({ name: 'ChecklistPanel' }).props('candidateReadOnly')).toBe(true)
  chat.vm.$emit('openReview', { caseId: 'case-1', snapshotId: 'snapshot-case-1', runId: 'run-r' })
  await flushPromises()
  expect(wrapper.find('.work-panel-heading').text()).toContain('审查报告')
  const review = wrapper.findComponent({ name: 'ReviewPanel' })
  expect(review.props('runId')).toBe('run-r')
  expect(review.props('supplierNames')).toEqual({ 'vendor-a': '供应商甲' })
  await wrapper.findAll('button').find(button => button.text() === '审查清单')!.trigger('click')
  await flushPromises()
  expect(wrapper.find('.work-panel-heading').text()).toContain('审查要求')
  expect(wrapper.findComponent({ name: 'ChecklistPanel' }).props('candidate')).toBeNull()
  expect(wrapper.findComponent({ name: 'ChecklistPanel' }).props('snapshotId')).toBe('snapshot-case-1')
  expect(wrapper.findComponent({ name: 'ChecklistPanel' }).props('candidateReadOnly')).toBe(false)
})

test('switching tasks closes the dynamic panels and clears their targets', async () => {
  apiRequest.mockImplementation(reads)
  const wrapper = mount(App, { global: { stubs: {
    DocumentsPanel: true, AgentWorkbench: true, EvidencePanel: true, ChatWorkspace: true,
    ChecklistPanel: { name: 'ChecklistPanel', props: ['caseId'], template: '<div class="checklist-stub" />' },
    ReviewPanel: true,
  } } })
  wrappers.push(wrapper)
  await flushPromises()
  const chat = wrapper.findComponent({ name: 'ChatWorkspace' })
  chat.vm.$emit('openChecklist', { caseId: 'case-1', snapshotId: 'snapshot-case-1', runId: 'run-a',
    candidate: { candidate: { items: [], criteria: [] }, changes: [], runId: 'run-a' } })
  await flushPromises()
  expect(wrapper.find('.checklist-stub').exists()).toBe(true)
  chat.vm.$emit('openChecklist', { caseId: 'case-2', snapshotId: 'snapshot-case-2', runId: 'run-b',
    candidate: { candidate: { items: [], criteria: [] }, changes: [], runId: 'run-b' } })
  await flushPromises()
  expect(wrapper.find('.checklist-stub').exists()).toBe(true)
  expect(wrapper.findComponent({ name: 'ChecklistPanel' }).props('caseId')).toBe('case-1')
})

test('current report correction events refresh only the corresponding chat result', async () => {
  apiRequest.mockImplementation(reads)
  const wrapper = mount(App, { global: { stubs: {
    DocumentsPanel: true, AgentWorkbench: true, EvidencePanel: true, ChatWorkspace: true,
    ChecklistPanel: true,
    ReviewPanel: { name: 'ReviewPanel', props: ['runId'], emits: ['correctionSubmitted'], template: '<div class="report-stub" />' },
  } } })
  wrappers.push(wrapper)
  await flushPromises()
  const chat = wrapper.findComponent({ name: 'ChatWorkspace' })
  const target = { caseId: 'case-1', snapshotId: 'snapshot-case-1', runId: 'run-a' }
  chat.vm.$emit('openReview', target)
  await flushPromises()
  const report = wrapper.findComponent({ name: 'ReviewPanel' })
  let finish!: () => void
  const completion = new Promise<void>(resolve => { finish = resolve })
  report.vm.$emit('correctionSubmitted', target, completion)
  await flushPromises()
  expect(chat.props('pendingReportCorrections')).toMatchObject({ 'run-a': { ...target, count: 1 } })
  report.vm.$emit('correctionSubmitted', { ...target, caseId: 'case-2' }, completion)
  report.vm.$emit('correctionSubmitted', { ...target, runId: 'old-run' }, completion)
  await flushPromises()
  expect(chat.props('pendingReportCorrections')).toMatchObject({ 'run-a': { ...target, count: 1 } })
  await wrapper.get('[aria-label="关闭工作面板"]').trigger('click')
  await flushPromises()
  expect(wrapper.find('.report-stub').exists()).toBe(false)
  finish()
  await flushPromises()
  expect(chat.props('pendingReportCorrections')).toMatchObject({ 'run-a': { count: 0 } })
})

test('the account menu opens memory preference settings', async () => {
  apiRequest.mockImplementation(reads)
  const wrapper = mount(App, { global: { stubs: {
    DocumentsPanel: true, AgentWorkbench: true, EvidencePanel: true, ChatWorkspace: true,
    ChecklistPanel: true, ReviewPanel: true,
    MemorySettings: { name: 'MemorySettings', emits: ['close'],
      template: '<div class="memory-stub"><button data-test="memory-close" @click="$emit(\'close\')">关闭</button></div>' },
  } } })
  wrappers.push(wrapper)
  await flushPromises()
  await wrapper.get('.account-menu summary').trigger('click')
  await wrapper.findAll('button').find(button => button.text() === '记忆偏好')!.trigger('click')
  await flushPromises()
  expect(wrapper.find('.memory-stub').exists()).toBe(true)
  await wrapper.get('[data-test="memory-close"]').trigger('click')
  await flushPromises()
  expect(wrapper.find('.memory-stub').exists()).toBe(false)
})
