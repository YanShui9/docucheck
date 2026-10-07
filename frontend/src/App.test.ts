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
    DocumentsPanel: { name: 'DocumentsPanel', props: ['selectedCase'],
      template: '<div class="documents-stub">{{ selectedCase?.id }}</div>' },
    AgentWorkbench: true, EvidencePanel: true, ChatWorkspace: true,
  } } })
  wrappers.push(wrapper)
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '资料')!.trigger('click')
  await flushPromises()
  return wrapper
}

afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  apiRequest.mockReset()
  uploadDocument.mockReset()
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
    ChecklistPanel: { name: 'ChecklistPanel', props: ['caseId', 'snapshotId', 'candidate'],
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
