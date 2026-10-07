// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ChatWorkspace from './ChatWorkspace.vue'
const { apiRequest } = vi.hoisted(() => ({ apiRequest: vi.fn() }))
vi.mock('../api', () => ({ apiRequest }))
vi.mock('../auth', () => ({ accessToken: async () => 'token' }))
const wrappers: ReturnType<typeof mount>[] = []
function setup(caseId = 'case-a') {
  const wrapper = mount(ChatWorkspace, { props: { caseId, revision: 0, canWrite: true,
    prepareCase: async () => 'case-a' } })
  wrappers.push(wrapper)
  return wrapper
}
function reads(path: string) {
  if (path.includes('/conversations?')) return Promise.resolve({ conversations: [], next_offset: null })
  if (path.includes('/messages?')) return Promise.resolve({ messages: [], next_offset: null })
  if (path.includes('/runs?')) return Promise.resolve({ runs: [], next_offset: null })
  if (path.endsWith('/conversations')) return Promise.resolve({ id: 'conv-a' })
  if (path === '/conversations/conv-a/messages') return Promise.resolve({ message_id: 'message-a', run_id: 'run-a' })
  if (path === '/agent-runs/run-a') return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a',
    message_id: 'message-a', revision: 0, status: 'queued', answer_text: null })
  throw new Error(path)
}
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); apiRequest.mockReset(); vi.useRealTimers() })

test('empty materials can use the real assistant workflow and a stable idempotency header', async () => {
  let accepted = false
  apiRequest.mockImplementation((path: string) => {
    if (path === '/conversations/conv-a/messages') { accepted = true; return reads(path) }
    if (accepted && path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '你好，我准备采购苹果', run_id: 'run-a', created_at: '' }], next_offset: null })
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('textarea').setValue('你好，我准备采购苹果')
  await wrapper.get('[aria-label="发送消息"]').trigger('click')
  await flushPromises()
  const call = apiRequest.mock.calls.find(([path]) => path === '/conversations/conv-a/messages')!
  expect(JSON.parse(call[2].body)).toEqual({ content: '你好，我准备采购苹果', operation: 'assistant', mode: 'standard', expected_revision: 0 })
  expect(call[2].headers['Idempotency-Key']).toBeTruthy()
  expect(wrapper.text()).toContain('排队中')
})

test('restores stored questions and verified assistant answers instead of manufacturing chat history', async () => {
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '苹果需要什么指标？', run_id: 'run-a', created_at: '' }], next_offset: null })
    if (path === '/agent-runs/run-a') return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a', message_id: 'message-a', status: 'completed', workflow: 'assistant', revision: 0, answer_text: '采购建议' })
    if (path.endsWith('/assistant-result')) return Promise.resolve({ run_id: 'run-a', case_id: 'case-a', revision: 0, snapshot_id: null, is_current: true, result: { kind: 'chat', answer: '先明确品种、等级和交付日期。', citations: [], candidate: null } })
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('苹果需要什么指标？')
  expect(wrapper.text()).toContain('先明确品种、等级和交付日期。')
})

test.each([false, true])('report correction refreshes cached chat answers, failed=%s', async failed => {
  let refreshing = false
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '审查苹果', run_id: 'run-a', created_at: '' }], next_offset: null })
    if (path === '/agent-runs/run-a') return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a',
      status: 'completed', workflow: 'assistant', snapshot_id: 'snap-1', answer_text: '旧正式答案' })
    if (path.endsWith('/assistant-result')) {
      if (refreshing && failed) return Promise.reject(new Error('offline'))
      return Promise.resolve({ run_id: 'run-a', case_id: 'case-a', snapshot_id: 'snap-1',
        result: { kind: 'review', answer: refreshing ? '修正后的待人工复核结果' : '旧正式答案', citations: [] } })
    }
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('旧正式答案')
  refreshing = true
  const target = { caseId: 'case-a', runId: 'run-a', snapshotId: 'snap-1', count: 1 }
  await wrapper.setProps({ pendingReportCorrections: { 'run-a': target } })
  await flushPromises()
  expect(wrapper.text()).not.toContain('旧正式答案')
  expect(wrapper.text()).toContain('正在保存事实修正')
  // Reopening the conversation while the POST is pending must not bring the old answer back.
  const count = apiRequest.mock.calls.filter(([path]) => path.endsWith('/assistant-result')).length
  await wrapper.get('[aria-label="选择对话"]').trigger('change')
  await flushPromises()
  expect(apiRequest.mock.calls.filter(([path]) => path.endsWith('/assistant-result'))).toHaveLength(count)
  await wrapper.setProps({ pendingReportCorrections: {} })
  await flushPromises()
  expect(wrapper.text()).not.toContain('旧正式答案')
  expect(wrapper.text()).toContain(failed ? '暂时无法完成请求' : '修正后的待人工复核结果')
  expect(apiRequest.mock.calls.some(([, , init]) => init?.method === 'POST')).toBe(false)
})

test.each(['normal', 'fast'])('two report corrections completing in one update restore both answers (%s)', async timing => {
  let updated = false
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: ['run-a', 'run-b'].map(id => ({
      id: `message-${id}`, role: 'user', content: id, run_id: id, created_at: '',
    })), next_offset: null })
    const runId = path.includes('run-b') ? 'run-b' : 'run-a'
    if (path.endsWith('/assistant-result')) return Promise.resolve({ run_id: runId, case_id: 'case-a', snapshot_id: 'snap-1',
      result: { kind: 'review', answer: `${updated ? '新' : '旧'}${runId}`, citations: [] } })
    if (path.startsWith('/agent-runs/')) return Promise.resolve({ id: runId, conversation_id: 'conv-a',
      status: 'completed', workflow: 'assistant', snapshot_id: 'snap-1' })
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  if (timing === 'normal') {
    await wrapper.setProps({ pendingReportCorrections: {
      'run-a': { caseId: 'case-a', snapshotId: 'snap-1', count: 1 }, 'run-b': { caseId: 'case-a', snapshotId: 'snap-1', count: 1 },
    } })
    expect(wrapper.text()).not.toContain('旧run-a')
    expect(wrapper.text()).not.toContain('旧run-b')
  }
  updated = true
  await wrapper.setProps({ pendingReportCorrections: {
    'run-a': { caseId: 'case-a', snapshotId: 'snap-1', count: 0 }, 'run-b': { caseId: 'case-a', snapshotId: 'snap-1', count: 0 },
  } })
  await flushPromises()
  expect(wrapper.text()).toContain('新run-a')
  expect(wrapper.text()).toContain('新run-b')
  expect(wrapper.text()).not.toContain('正在保存事实修正')
})

test('a late send response cannot display an old task result or clear the new task draft', async () => {
  let resolve!: (value: object) => void
  const pending = new Promise<object>(yes => { resolve = yes })
  apiRequest.mockImplementation((path: string) => path === '/conversations/conv-a/messages' ? pending : reads(path))
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('textarea').setValue('旧任务问题')
  await wrapper.get('[aria-label="发送消息"]').trigger('click')
  await flushPromises()
  await wrapper.setProps({ caseId: 'case-b' })
  await flushPromises()
  await wrapper.get('textarea').setValue('新任务草稿')
  resolve({ message_id: 'message-a', run_id: 'run-a' })
  await flushPromises()
  expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('新任务草稿')
  expect(wrapper.text()).not.toContain('旧任务问题')
  expect(apiRequest.mock.calls.some(([path]) => path === '/agent-runs/run-a')).toBe(false)
})

test('uncertain POST retries reuse the original body and idempotency key', async () => {
  let attempts = 0
  apiRequest.mockImplementation((path: string) => {
    if (path === '/conversations/conv-a/messages' && ++attempts === 1) return Promise.reject(new TypeError('Failed to fetch'))
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('textarea').setValue('采购水果')
  await wrapper.get('[aria-label="发送消息"]').trigger('click')
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '重试原请求')!.trigger('click')
  await flushPromises()
  const calls = apiRequest.mock.calls.filter(([path]) => path === '/conversations/conv-a/messages')
  expect(calls).toHaveLength(2)
  expect(calls[0]![2].headers).toEqual(calls[1]![2].headers)
  expect(calls[0]![2].body).toEqual(calls[1]![2].body)
})

test('source validation failure cannot fall back to unverified answer_text', async () => {
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '请核验', run_id: 'run-a', created_at: '' }], next_offset: null })
    if (path === '/agent-runs/run-a') return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a', status: 'completed', workflow: 'assistant', answer_text: '未经验证的正式结论' })
    if (path.endsWith('/assistant-result')) return Promise.reject(new Error('assistant_source_changed'))
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).not.toContain('未经验证的正式结论')
  expect(wrapper.text()).toContain('结果来源发生变化')
})

test('a failed history read can recover by opening another conversation', async () => {
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'good', created_at: '' }, { id: 'bad', created_at: '' }], next_offset: null })
    if (path.startsWith('/conversations/bad/messages')) return Promise.reject(new Error('http_500'))
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
  await wrapper.get('[aria-label="选择对话"]').setValue('good')
  await flushPromises()
  expect(wrapper.get('textarea').attributes('disabled')).toBeUndefined()
})

test('transient polling failure retries reading status without resubmitting a model task', async () => {
  vi.useFakeTimers()
  let polls = 0
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '问题', run_id: 'run-a', created_at: '' }], next_offset: null })
    if (path === '/agent-runs/run-a' && ++polls === 2) return Promise.reject(new Error('http_500'))
    return reads(path)
  })
  setup()
  await flushPromises()
  await vi.advanceTimersByTimeAsync(2500)
  await flushPromises()
  await vi.advanceTimersByTimeAsync(10000)
  await flushPromises()
  expect(polls).toBe(3)
  expect(apiRequest.mock.calls.some(([, , init]) => init?.method === 'POST')).toBe(false)
})

test('an empty task draft cannot leak into another workspace', async () => {
  apiRequest.mockImplementation(reads)
  const wrapper = mount(ChatWorkspace, { props: { caseId: null, scopeKey: 'project-a', revision: 0, canWrite: true, prepareCase: async () => null } })
  wrappers.push(wrapper)
  await wrapper.get('textarea').setValue('甲工作区采购需求')
  await wrapper.setProps({ scopeKey: 'project-b' })
  expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
})

test('interrupted requests disclose unknown outcome and retry the bound run without discarding a new draft', async () => {
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '旧问题', run_id: 'run-a', created_at: '' }], next_offset: null })
    if (path === '/agent-runs/run-a') return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a', revision: 0, status: 'interrupted', workflow: 'assistant', outcome_unknown: true, is_current: true })
    if (path.endsWith('/retry')) return Promise.resolve({ run_id: 'run-a' })
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('处理已中断')
  expect(wrapper.text()).toContain('重新处理可能再次调用模型')
  await wrapper.get('textarea').setValue('新问题草稿')
  await wrapper.findAll('button').find(button => button.text() === '重新处理')!.trigger('click')
  await flushPromises()
  const call = apiRequest.mock.calls.find(([path]) => path === '/agent-runs/run-a/retry')!
  expect(JSON.parse(call[2].body)).toEqual({ expected_revision: 0 })
  expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('新问题草稿')
})

test('a late running poll cannot overwrite the newer cancelled status', async () => {
  vi.useFakeTimers()
  let polls = 0
  let resolve!: (value: object) => void
  const late = new Promise<object>(yes => { resolve = yes })
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '问题', run_id: 'run-a', created_at: '' }], next_offset: null })
    if (path.endsWith('/cancel')) return Promise.resolve({})
    if (path === '/agent-runs/run-a') {
      if (++polls === 2) return late
      return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a', status: polls > 2 ? 'cancelled' : 'running' })
    }
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  vi.advanceTimersByTime(2500)
  await flushPromises()
  await wrapper.get('[aria-label="停止处理"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('已停止')
  resolve({ id: 'run-a', conversation_id: 'conv-a', status: 'running' })
  await flushPromises()
  expect(wrapper.text()).toContain('已停止')
  expect(wrapper.text()).not.toContain('正在处理')
})

test('waiting for clarification resumes the existing run and displays its latest answer only once', async () => {
  let resumed = false
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '采购苹果', run_id: 'run-a', created_at: '' }, ...(resumed ? [{ id: 'message-b', role: 'user', content: '红富士', run_id: 'run-a', created_at: '' }] : [])], next_offset: null })
    if (path === '/agent-runs/run-a') return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a', revision: 0, status: resumed ? 'completed' : 'waiting_user', workflow: 'assistant' })
    if (path.endsWith('/assistant-result')) return Promise.resolve({ run_id: 'run-a', case_id: 'case-a', revision: 0, snapshot_id: null, is_current: true, result: { kind: 'chat', answer: resumed ? '品种已明确为红富士。' : '请补充品种。', citations: [], candidate: null } })
    if (path.endsWith('/resume')) { resumed = true; return Promise.resolve({ run_id: 'run-a' }) }
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('textarea').setValue('红富士')
  await wrapper.get('[aria-label="发送消息"]').trigger('click')
  await flushPromises()
  const call = apiRequest.mock.calls.find(([path]) => path === '/agent-runs/run-a/resume')!
  expect(JSON.parse(call[2].body)).toEqual({ content: '红富士', expected_revision: 0 })
  expect(wrapper.text().split('品种已明确为红富士。')).toHaveLength(2)
  expect(apiRequest.mock.calls.some(([path]) => path === '/conversations/conv-a/messages')).toBe(false)
})

test('access denial during result hydration clears already loaded private messages', async () => {
  vi.useFakeTimers()
  let polls = 0
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '内部采购要求', run_id: 'run-a', created_at: '' }], next_offset: null })
    if (path === '/agent-runs/run-a') return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a', status: ++polls === 1 ? 'running' : 'completed', workflow: 'assistant' })
    if (path.endsWith('/assistant-result')) return Promise.reject(new Error('not_found'))
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('内部采购要求')
  await vi.advanceTimersByTimeAsync(2500)
  await flushPromises()
  expect(wrapper.text()).not.toContain('内部采购要求')
  expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
})

const candidateResult = {
  run_id: 'run-a', case_id: 'case-a', revision: 0, snapshot_id: 'snap-1', is_current: true, checklist_revision: 0,
  result: { kind: 'changes', answer: '已整理 1 项要求。', citations: [],
    candidate: { items: [{ item_id: 'apple', name: '苹果' }],
      criteria: [{ criterion_id: 'c-qty', item_id: 'apple', name: '数量',
        rule: { type: 'numeric', operator: 'gte', value: '20', unit: '箱' },
        source: { kind: 'user_defined', reason: '用户要求' }, required: true,
        enabled: true, conditions: [] }] },
    changes: [{ op: 'add', criterion_id: 'c-qty', reason: '用户要求', before: null, after: {} }] },
}

function checklistView(overrides: object = {}) {
  return { case_id: 'case-a', snapshot_id: 'snap-1', revision: 0, is_current_snapshot: true,
    checklist_revision: 0, state: 'empty', draft: null, confirmed_version: null,
    previous_version_id: null, issues: [], execution_available: false, ...overrides }
}
function candidateReads(path: string) {
  if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
  if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '整理要求', run_id: 'run-a', created_at: '' }], next_offset: null })
  if (path === '/agent-runs/run-a') return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a', status: 'completed', workflow: 'assistant', revision: 0 })
  if (path.endsWith('/assistant-result')) return Promise.resolve(candidateResult)
  if (path.endsWith('/checklist')) return Promise.resolve(checklistView())
  return reads(path)
}

test('a changes candidate shows an explicit edit entry and never saves by itself', async () => {
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '整理要求', run_id: 'run-a', created_at: '' }], next_offset: null })
    if (path === '/agent-runs/run-a') return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a', status: 'completed', workflow: 'assistant', revision: 0 })
    if (path.endsWith('/assistant-result')) return Promise.resolve(candidateResult)
    if (path.endsWith('/checklist')) return Promise.resolve(checklistView())
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('待确认的审查要求')
  expect(wrapper.text()).toContain('尚未保存确认')
  await wrapper.findAll('button').find(button => button.text() === '查看与编辑')!.trigger('click')
  expect(wrapper.emitted('openChecklist')).toEqual([[
    { caseId: 'case-a', snapshotId: 'snap-1', runId: 'run-a',
      candidate: { candidate: candidateResult.result.candidate,
        changes: candidateResult.result.changes, runId: 'run-a', baseChecklistRevision: 0 } },
  ]])
  expect(apiRequest.mock.calls.some(([, , init]) => init?.method === 'PUT')).toBe(false)
})

test.each(['draft', 'confirmed'])('candidate card reflects the persisted %s definition, despite an old base revision', async state => {
  apiRequest.mockImplementation((path: string) => {
    if (path.endsWith('/assistant-result')) return Promise.resolve({ ...candidateResult, is_current: false })
    if (path.endsWith('/checklist')) return Promise.resolve(checklistView({ state, checklist_revision: 2,
      draft: state === 'draft' ? candidateResult.result.candidate : null,
      confirmed_version: state === 'confirmed' ? { definition: candidateResult.result.candidate } : null,
      execution_available: state === 'confirmed' }))
    return candidateReads(path)
  })
  const wrapper = setup()
  await flushPromises()
  const card = wrapper.get('.candidate-card')
  expect(card.text()).toContain(state === 'draft' ? '已保存' : '已确认')
  expect(card.text()).not.toContain('尚未保存确认')
  await card.get('button').trigger('click')
  expect((wrapper.emitted('openChecklist')![0]![0] as { candidate: unknown }).candidate).toBeNull()
})

test.each(['snapshot', 'checklist'])('historical %s candidates are read-only and keep their original snapshot', async history => {
  apiRequest.mockImplementation((path: string) => {
    if (path.endsWith('/assistant-result')) return Promise.resolve({ ...candidateResult, is_current: false })
    if (path.endsWith('/checklist')) return Promise.resolve(checklistView({
      state: history === 'snapshot' ? 'historical' : 'draft',
      is_current_snapshot: history !== 'snapshot', checklist_revision: 2 }))
    return candidateReads(path)
  })
  const wrapper = setup()
  await flushPromises()
  const card = wrapper.get('.candidate-card')
  expect(card.text()).toContain('历史候选')
  expect(card.text()).not.toContain('查看与编辑')
  await card.get('button').trigger('click')
  expect(wrapper.emitted('openChecklist')![0]![0]).toMatchObject({ snapshotId: 'snap-1', readOnly: true })
})

test('save and confirm notifications refresh the candidate status without model requests', async () => {
  let view = checklistView()
  apiRequest.mockImplementation((path: string) => path.endsWith('/checklist') ? Promise.resolve(view) : candidateReads(path))
  const wrapper = setup()
  await flushPromises()
  view = checklistView({ state: 'draft', checklist_revision: 1, draft: candidateResult.result.candidate })
  await wrapper.setProps({ checklistChanges: { 'snap-1': 1 } })
  await flushPromises()
  expect(wrapper.get('.candidate-card').text()).toContain('已保存')
  view = checklistView({ state: 'confirmed', checklist_revision: 2,
    confirmed_version: { definition: candidateResult.result.candidate }, execution_available: true })
  await wrapper.setProps({ checklistChanges: { 'snap-1': 2 } })
  await flushPromises()
  expect(wrapper.get('.candidate-card').text()).toContain('已确认')
  expect(apiRequest.mock.calls.some(([, , init]) => init?.method === 'POST' || init?.method === 'PUT')).toBe(false)
})

test('a failed candidate state refresh cannot retain a saved label or allow editing', async () => {
  let failed = false
  apiRequest.mockImplementation((path: string) => {
    if (path.endsWith('/checklist')) return failed ? Promise.reject(new Error('offline'))
      : Promise.resolve(checklistView({ state: 'draft', checklist_revision: 1, draft: candidateResult.result.candidate }))
    return candidateReads(path)
  })
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.get('.candidate-card').text()).toContain('已保存')
  failed = true
  await wrapper.setProps({ checklistChanges: { 'snap-1': 1 } })
  await flushPromises()
  expect(wrapper.get('.candidate-card').text()).toContain('状态待核实')
  await wrapper.get('.candidate-card button').trigger('click')
  await flushPromises()
  expect(wrapper.emitted('openChecklist')).toBeUndefined()
  failed = false
  await wrapper.get('.candidate-card button').trigger('click')
  await flushPromises()
  expect(wrapper.get('.candidate-card').text()).toContain('已保存')
})

test('a delayed candidate status cannot overwrite a newer confirmation response', async () => {
  let resolveOld!: (value: unknown) => void
  let count = 0
  apiRequest.mockImplementation((path: string) => {
    if (path.endsWith('/checklist')) {
      if (++count === 1) return new Promise(resolve => { resolveOld = resolve })
      return Promise.resolve(checklistView({ state: 'confirmed', checklist_revision: 2,
        confirmed_version: { definition: candidateResult.result.candidate }, execution_available: true }))
    }
    return candidateReads(path)
  })
  const wrapper = setup()
  await flushPromises()
  await wrapper.setProps({ checklistChanges: { 'snap-1': 1 } })
  await flushPromises()
  expect(wrapper.get('.candidate-card').text()).toContain('已确认')
  resolveOld(checklistView())
  await flushPromises()
  expect(wrapper.get('.candidate-card').text()).toContain('已确认')
  expect(wrapper.get('.candidate-card').text()).not.toContain('尚未保存确认')
})

test('candidate status access denial clears private messages and candidate actions', async () => {
  apiRequest.mockImplementation((path: string) => path.endsWith('/checklist')
    ? Promise.reject(new Error('forbidden')) : candidateReads(path))
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).not.toContain('整理要求')
  expect(wrapper.find('.candidate-card').exists()).toBe(false)
  expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
})

test('access denial invalidates an older message page already in flight', async () => {
  let denied = false
  let resolvePage!: (value: unknown) => void
  apiRequest.mockImplementation((path: string) => {
    if (path.endsWith('offset=0&limit=1')) return Promise.resolve({ total: 101, messages: [], next_offset: 1 })
    if (path.includes('/messages?') && path.includes('offset=0&limit=100')) return new Promise(resolve => { resolvePage = resolve })
    if (path.endsWith('/checklist') && denied) return Promise.reject(new Error('forbidden'))
    return candidateReads(path)
  })
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="load-older"]').trigger('click')
  await flushPromises()
  denied = true
  await wrapper.setProps({ checklistChanges: { 'snap-1': 1 } })
  await flushPromises()
  expect(wrapper.text()).not.toContain('整理要求')
  resolvePage({ messages: [{ id: 'private-old', role: 'user', content: '失权前读取的内部报价', run_id: null }], next_offset: null })
  await flushPromises()
  expect(wrapper.text()).not.toContain('失权前读取的内部报价')
  expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
})

test('a formal review offers the full report entry bound to its snapshot', async () => {
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '开始审查', run_id: 'run-a', created_at: '' }], next_offset: null })
    if (path === '/agent-runs/run-a') return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a', status: 'completed', workflow: 'assistant', revision: 0 })
    if (path.endsWith('/assistant-result')) return Promise.resolve({ run_id: 'run-a', case_id: 'case-a',
      revision: 1, snapshot_id: 'snap-1', is_current: true,
      result: { kind: 'review', answer: '审查完成：1 项满足。', citations: [], candidate: null, changes: [] } })
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('审查完成：1 项满足。')
  await wrapper.findAll('button').find(button => button.text().includes('查看完整报告'))!.trigger('click')
  expect(wrapper.emitted('openReview')).toEqual([[{ caseId: 'case-a', snapshotId: 'snap-1', runId: 'run-a' }]])
})

test('long conversations load the recent page first and page back progressively', async () => {
  const tail = Array.from({ length: 3 }, (_, i) => ({
    id: `m${i + 3}`, role: 'user' as const, content: `较新消息${i + 3}`, run_id: null, created_at: '' }))
  const older = [{ id: 'm1', role: 'user' as const, content: '最早消息1', run_id: null, created_at: '' }]
  const fetched: string[] = []
  apiRequest.mockImplementation((path: string) => {
    fetched.push(path)
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('offset=0&limit=1')) return Promise.resolve({ conversation_id: 'conv-a', total: 203, messages: [], next_offset: 1 })
    if (path.includes('offset=103')) return Promise.resolve({ conversation_id: 'conv-a', total: 203, messages: tail, next_offset: null })
    if (path.includes('offset=3')) return Promise.resolve({ conversation_id: 'conv-a', total: 203, messages: older, next_offset: 103 })
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('较新消息5')
  expect(wrapper.text()).not.toContain('最早消息1')
  await wrapper.get('[data-test="load-older"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('最早消息1')
  expect(wrapper.text()).toContain('剩余 3 条')
  expect(fetched.some(path => path.includes('offset=3&limit=100'))).toBe(true)
})

test('progressive paging stops with a clear error beyond the 20-page budget', async () => {
  const fetched: string[] = []
  apiRequest.mockImplementation((path: string) => {
    fetched.push(path)
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('offset=0&limit=1')) return Promise.resolve({ conversation_id: 'conv-a', total: 2400, messages: [], next_offset: 1 })
    if (path.includes('offset=2300')) return Promise.resolve({ conversation_id: 'conv-a', total: 2400, messages: [{ id: 'tail', role: 'user', content: '最新消息', run_id: null, created_at: '' }], next_offset: null })
    const offset = Number(path.match(/offset=(\d+)/)?.[1])
    return Promise.resolve({ conversation_id: 'conv-a', total: 2400,
      messages: [{ id: `m${offset}`, role: 'user' as const, content: `消息${offset}`, run_id: null, created_at: '' }],
      next_offset: offset + 100 })
  })
  const wrapper = setup()
  await flushPromises()
  for (let i = 0; i < 25; i++) {
    const button = wrapper.findAll('button').find(item => item.text().includes('加载更早消息'))
    if (!button) break
    await button.trigger('click')
    await flushPromises()
  }
  expect(wrapper.text()).toContain('历史记录较多')
  expect(fetched.filter(path => path.includes('/messages?')).length).toBeLessThanOrEqual(22)
})

test('loading older history restores verified answers and deduplicates Run reads', async () => {
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.endsWith('offset=0&limit=1')) return Promise.resolve({ total: 102, messages: [], next_offset: 1 })
    if (path.includes('/messages?')) return Promise.resolve({ messages: path.includes('offset=2&')
      ? Array.from({ length: 100 }, (_, i) => ({ id: `new-${i}`, role: 'user', content: `最新问题${i}`, run_id: null }))
      : [{ id: 'old', role: 'user', content: '旧问题', run_id: 'old-run' }, { id: 'old-resume', role: 'user', content: '旧补充', run_id: 'old-run' }], next_offset: null })
    if (path === '/agent-runs/old-run') return Promise.resolve({ id: 'old-run', conversation_id: 'conv-a', status: 'completed', workflow: 'assistant', revision: 0 })
    if (path.endsWith('/assistant-result')) return Promise.resolve({ run_id: 'old-run', case_id: 'case-a',
      result: { kind: 'chat', answer: '旧问题的经过验证的答复', candidate: null, citations: [] } })
    return reads(path)
  })
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="load-older"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('旧问题的经过验证的答复')
  expect(apiRequest.mock.calls.filter(([path]) => path === '/agent-runs/old-run')).toHaveLength(1)
  expect(wrapper.findAll('.chat-message.assistant').filter(item => item.text().includes('旧问题的经过验证的答复'))).toHaveLength(1)
})

function olderReads(path: string) {
  if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
  if (path.endsWith('offset=0&limit=1')) return Promise.resolve({ total: 112, messages: [], next_offset: 1 })
  if (path.includes('/messages?')) return Promise.resolve({ messages: path.includes('offset=12&')
    ? [{ id: 'new', role: 'user', content: '最新问题', run_id: null }]
    : Array.from({ length: 12 }, (_, i) => ({ id: `old-${i}`, role: 'user', content: `旧问题${i}`, run_id: `old-${i}` })), next_offset: null })
  return reads(path)
}
test('older Run reads stay bounded and a delayed batch cannot leak into a new task', async () => {
  const releases: Array<() => void> = []
  let active = 0; let maxActive = 0
  apiRequest.mockImplementation((path: string) => {
    if (path === '/cases/case-b/conversations?offset=0&limit=100') return Promise.resolve({ conversations: [], next_offset: null })
    if (path.startsWith('/agent-runs/old-')) return new Promise(resolve => {
      active++; maxActive = Math.max(maxActive, active)
      releases.push(() => { active--; resolve({ id: path.split('/')[2], conversation_id: 'conv-a',
        status: 'completed', workflow: 'legacy', answer_text: '旧答复' }) })
    })
    return olderReads(path)
  })
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="load-older"]').trigger('click')
  await flushPromises()
  expect(releases).toHaveLength(5)
  releases.splice(0).forEach(release => release())
  await flushPromises()
  expect(releases).toHaveLength(5)
  expect(maxActive).toBe(5)
  await wrapper.setProps({ caseId: 'case-b' })
  releases.splice(0).forEach(release => release())
  await flushPromises()
  expect(wrapper.text()).not.toContain('旧答复')
  expect(wrapper.text()).not.toContain('旧问题')
  expect(apiRequest.mock.calls.filter(([path]) => path.startsWith('/agent-runs/old-'))).toHaveLength(10)
})

test('denied older Run metadata clears history without waiting for its slow batch peers', async () => {
  let rejectRun!: (reason: Error) => void
  const releases: Array<() => void> = []
  apiRequest.mockImplementation((path: string) => {
    if (path === '/agent-runs/old-0') return new Promise((_resolve, reject) => { rejectRun = reject })
    if (path.startsWith('/agent-runs/old-')) return new Promise(resolve => {
      releases.push(() => resolve({ id: path.split('/')[2], conversation_id: 'conv-a', status: 'completed', workflow: 'legacy', answer_text: '迟到的旧答复' }))
    })
    return olderReads(path)
  })
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="load-older"]').trigger('click')
  await flushPromises()
  rejectRun(new Error('forbidden'))
  await flushPromises()
  expect(wrapper.text()).not.toContain('旧问题')
  releases.forEach(release => release())
  await flushPromises()
  expect(wrapper.text()).not.toContain('迟到的旧答复')
  expect(apiRequest.mock.calls.filter(([path]) => path.startsWith('/agent-runs/old-'))).toHaveLength(5)
})

test('failed older Run reads show an error and refresh restores the same loaded range', async () => {
  let failed = true
  apiRequest.mockImplementation((path: string) => {
    if (path.startsWith('/agent-runs/old-')) return failed ? Promise.reject(new Error('offline'))
      : Promise.resolve({ id: path.split('/')[2], conversation_id: 'conv-a', status: 'completed', workflow: 'legacy', answer_text: '重读后的历史答复' })
    return olderReads(path)
  })
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="load-older"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('暂时无法完成请求')
  failed = false
  // Per-answer failures must provide a retry entry, even when all older pages have loaded.
  await wrapper.findAll('button').find(button => button.text() === '刷新对话')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('重读后的历史答复')
  expect(wrapper.text()).not.toContain('暂时无法完成请求')
})
