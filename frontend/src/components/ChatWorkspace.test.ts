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
  run_id: 'run-a', case_id: 'case-a', revision: 0, snapshot_id: 'snap-1', is_current: true,
  result: { kind: 'changes', answer: '已整理 1 项要求。', citations: [],
    candidate: { items: [{ item_id: 'apple', name: '苹果' }],
      criteria: [{ criterion_id: 'c-qty', item_id: 'apple', name: '数量',
        rule: { type: 'numeric', operator: 'gte', value: '20', unit: '箱' },
        source: { kind: 'user_defined', reason: '用户要求' }, required: true,
        enabled: true, conditions: [] }] },
    changes: [{ op: 'add', criterion_id: 'c-qty', reason: '用户要求', before: null, after: {} }] },
}

test('a changes candidate shows an explicit edit entry and never saves by itself', async () => {
  apiRequest.mockImplementation((path: string) => {
    if (path.includes('/conversations?')) return Promise.resolve({ conversations: [{ id: 'conv-a', created_at: '' }], next_offset: null })
    if (path.includes('/messages?')) return Promise.resolve({ messages: [{ id: 'message-a', role: 'user', content: '整理要求', run_id: 'run-a', created_at: '' }], next_offset: null })
    if (path === '/agent-runs/run-a') return Promise.resolve({ id: 'run-a', conversation_id: 'conv-a', status: 'completed', workflow: 'assistant', revision: 0 })
    if (path.endsWith('/assistant-result')) return Promise.resolve(candidateResult)
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
        changes: candidateResult.result.changes, runId: 'run-a' } },
  ]])
  expect(apiRequest.mock.calls.some(([, , init]) => init?.method === 'PUT')).toBe(false)
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
