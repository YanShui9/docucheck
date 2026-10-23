// @vitest-environment jsdom
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ContextUsage from './ContextUsage.vue'

const { apiRequest, accessToken } = vi.hoisted(() => ({ apiRequest: vi.fn(), accessToken: vi.fn() }))
vi.mock('../api', () => ({ apiRequest }))
vi.mock('../auth', () => ({ accessToken }))
const wrappers: ReturnType<typeof mount>[] = []
function view() {
  return { run_id: 'run-a', case_id: 'case-a', conversation_id: 'conv-a', snapshot_id: 'snap-a',
    revision: 2, attempt_id: 'attempt-a', run_status: 'completed', record: {
      seq: 12, created_at: '2026-10-23T00:00:00Z', phase: 'assistant_decision', status: 'completed', provider_called: true,
      estimate: { input_bytes: 2048, limit_bytes: 8192, model_window_tokens: 65536,
        output_reserved_tokens: 4096, safety_reserved_tokens: 3277 },
      usage: { input_tokens: 500, output_tokens: 20, total_tokens: 520 },
      memory: { history_turns: 8, summary_notes: 2, task_handoff: true }, summary_status: 'completed',
    } }
}
function setup(props: Partial<InstanceType<typeof ContextUsage>['$props']> = {}) {
  const wrapper = mount(ContextUsage, { props: { caseId: 'case-a', conversationId: 'conv-a', runId: 'run-a',
    attemptId: 'attempt-a', runStatus: 'completed', ...props }, attachTo: document.body })
  wrappers.push(wrapper)
  return wrapper
}
async function open(wrapper: ReturnType<typeof setup>) {
  await wrapper.get('.context-usage-trigger').trigger('click'); await flushPromises()
}
beforeEach(() => { accessToken.mockResolvedValue('token'); apiRequest.mockResolvedValue(view()) })
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  apiRequest.mockReset(); accessToken.mockReset(); vi.unstubAllGlobals(); vi.useRealTimers()
})

test('opens lazily and shows bytes against the same input allowance with a separate configured token window', async () => {
  const wrapper = setup()
  expect(apiRequest).not.toHaveBeenCalled()
  expect(wrapper.get('.context-usage-trigger').attributes('aria-expanded')).toBe('false')
  await open(wrapper)
  expect(apiRequest.mock.calls[0]?.[0]).toBe('/agent-runs/run-a/context-usage')
  expect(apiRequest.mock.calls[0]?.[2]?.signal).toBeInstanceOf(AbortSignal)
  expect(wrapper.text()).toContain('2 KB / 8 KB')
  expect(wrapper.text()).toContain('25%')
  expect(wrapper.text()).toContain('65,536 token')
  expect(wrapper.text()).toContain('配置的模型窗口')
  expect(wrapper.text()).toContain('最近一次已记录请求')
  expect(wrapper.text()).not.toContain('1000000')
  expect(wrapper.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('25')
})

test('separates selected history and summary from the summary completed in this round', async () => {
  const result = view(); result.record.memory.summary_notes = 0
  apiRequest.mockResolvedValue(result)
  const wrapper = setup(); await open(wrapper)
  expect(wrapper.text()).toContain('近期原话8轮')
  expect(wrapper.text()).toContain('历史摘要0条')
  expect(wrapper.text()).toContain('任务交接已选用')
  expect(wrapper.text()).toContain('本轮摘要整理已完成')
  const selected = wrapper.findAll('h3').find(heading => heading.text() === '本次请求选用')!
  expect(selected.element.nextElementSibling?.textContent).not.toContain('本轮摘要整理')
})

test('shows a selected prior summary independently of a failed summary update', async () => {
  const result = view(); result.record.summary_status = 'failed'
  apiRequest.mockResolvedValue(result)
  const wrapper = setup(); await open(wrapper)
  expect(wrapper.text()).toContain('历史摘要2条')
  expect(wrapper.text()).toContain('本轮摘要整理未完成')
})

test('allows over-limit text but caps only the visible progress bar', async () => {
  const result = view(); result.record.estimate.input_bytes = 10240
  apiRequest.mockResolvedValue(result)
  const wrapper = setup(); await open(wrapper)
  expect(wrapper.text()).toContain('125%')
  expect(wrapper.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('100')
  expect(wrapper.get('.context-usage-progress-fill').attributes('style')).toContain('width: 100%')
})

test('zero allowance shows no calculated percentage and never Infinity or NaN', async () => {
  const result = view(); result.record.estimate.limit_bytes = 0
  apiRequest.mockResolvedValue(result)
  const wrapper = setup(); await open(wrapper)
  expect(wrapper.text()).toContain('2 KB / 0 KB')
  expect(wrapper.text()).toContain('占比未记录')
  expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
  expect(wrapper.text()).not.toMatch(/Infinity|NaN/)
})

test('unknown memory, provider usage, reservations and summary remain unknown rather than zero', async () => {
  const result = { ...view(), record: { ...view().record, memory: null, usage: null, provider_called: null,
    summary_status: 'not_recorded', estimate: { ...view().record.estimate, output_reserved_tokens: null, safety_reserved_tokens: null } } }
  apiRequest.mockResolvedValue(result)
  const wrapper = setup(); await open(wrapper)
  expect(wrapper.text()).toContain('近期原话未记录')
  expect(wrapper.text()).toContain('历史摘要未记录')
  expect(wrapper.text()).toContain('任务交接未记录')
  expect(wrapper.text()).toContain('本轮摘要整理未记录')
  expect(wrapper.text()).toContain('API 实际用量未记录')
  expect(wrapper.text()).not.toContain('0 token')
})

test.each([{ output_tokens: 0 }, { input_tokens: -1, output_tokens: 0, total_tokens: NaN }])('validates every displayed number and does not derive missing or invalid usage (%j)', async usage => {
  apiRequest.mockResolvedValue({ ...view(), record: { ...view().record,
    estimate: { input_bytes: -1, limit_bytes: Infinity, model_window_tokens: NaN,
      output_reserved_tokens: -2, safety_reserved_tokens: null }, usage,
    memory: { history_turns: -1, summary_notes: NaN, task_handoff: false } } })
  const wrapper = setup(); await open(wrapper)
  expect(wrapper.text()).not.toMatch(/Infinity|NaN|-1|-2/)
  expect(wrapper.text()).toContain('输入未记录')
  expect(wrapper.text()).toContain('输出0 token')
  expect(wrapper.text()).toContain('合计未记录')
  expect(wrapper.text()).toContain('任务交接未选用')
  expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
})

test.each(['caseId', 'conversationId', 'runId'] as const)('missing %s shows a local empty state without a request', async key => {
  const wrapper = setup({ [key]: null }); await open(wrapper)
  expect(apiRequest).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('发送消息后可查看')
})

test('an older Run without a usable record displays not recorded', async () => {
  apiRequest.mockResolvedValue({ ...view(), record: null })
  const wrapper = setup(); await open(wrapper)
  expect(wrapper.text()).toContain('未记录')
  expect(wrapper.text()).not.toContain('0 KB')
})

test('in-progress Run explains that a recorded request may not have updated yet', async () => {
  const wrapper = setup({ runStatus: 'running' }); await open(wrapper)
  expect(wrapper.text()).toContain('处理中，记录可能尚未更新')
})

test('request failures offer a local retry', async () => {
  apiRequest.mockRejectedValueOnce(new Error('offline'))
  const wrapper = setup(); await open(wrapper)
  expect(wrapper.text()).toContain('暂时无法读取')
  apiRequest.mockResolvedValue(view())
  await wrapper.get('[data-test="context-usage-retry"]').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('2 KB / 8 KB')
  expect(apiRequest).toHaveBeenCalledTimes(2)
})

test('Escape closes the keyboard-accessible card, aborts reading, and returns focus', async () => {
  apiRequest.mockReturnValue(new Promise(() => {}))
  const wrapper = setup(); await open(wrapper)
  const trigger = wrapper.get('.context-usage-trigger')
  expect(trigger.attributes('aria-controls')).toBe(wrapper.get('[role="dialog"]').attributes('id'))
  const signal = apiRequest.mock.calls[0]![2].signal as AbortSignal
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  await flushPromises()
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  expect(trigger.attributes('aria-expanded')).toBe('false')
  expect(signal.aborted).toBe(true)
  expect(document.activeElement).toBe(trigger.element)
})

test('entry toggles closed and an outside click returns focus after the clicked target takes focus', async () => {
  const wrapper = setup(); await open(wrapper)
  await wrapper.get('.context-usage-trigger').trigger('click')
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  await open(wrapper)
  const outside = document.createElement('button')
  document.body.append(outside)
  try {
    outside.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true })); await flushPromises()
    // Browsers focus the pointer target after pointerdown and before click.
    outside.focus()
    outside.click(); await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('.context-usage-trigger').element)
  } finally { outside.remove() }
})

test('a hanging read times out after 20 seconds and its eventual result is ignored', async () => {
  vi.useFakeTimers()
  let release!: (value: unknown) => void
  apiRequest.mockReturnValue(new Promise(resolve => { release = resolve }))
  const wrapper = setup(); await open(wrapper)
  await vi.advanceTimersByTimeAsync(19999)
  expect(wrapper.text()).toContain('正在读取')
  await vi.advanceTimersByTimeAsync(1)
  expect(wrapper.text()).toContain('读取超时')
  expect((apiRequest.mock.calls[0]![2].signal as AbortSignal).aborted).toBe(true)
  release(view()); await flushPromises()
  expect(wrapper.text()).toContain('读取超时')
  expect(wrapper.text()).not.toContain('2 KB')
})

test('a hanging token provider times out and resolving it later cannot dispatch fetch', async () => {
  vi.useFakeTimers()
  const actual = await vi.importActual<typeof import('../api')>('../api')
  let releaseToken!: (token: string) => void
  accessToken.mockReturnValue(new Promise(resolve => { releaseToken = resolve }))
  apiRequest.mockImplementation(actual.apiRequest)
  const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock)
  const wrapper = setup(); await open(wrapper)
  await vi.advanceTimersByTimeAsync(20000)
  expect(wrapper.text()).toContain('读取超时')
  releaseToken('late-token'); await flushPromises()
  expect(fetchMock).not.toHaveBeenCalled()
})

test.each(['case_id', 'conversation_id', 'run_id', 'attempt_id'] as const)('a response with mismatched %s never displays its record', async key => {
  apiRequest.mockResolvedValue({ ...view(), [key]: 'other' })
  const wrapper = setup(); await open(wrapper)
  expect(wrapper.text()).not.toContain('2 KB')
  expect(wrapper.text()).toContain('归属已变化')
})

test('changing scope aborts and clears a read before a late response can replace the new one', async () => {
  let release!: (value: unknown) => void
  apiRequest.mockReturnValueOnce(new Promise(resolve => { release = resolve }))
  const wrapper = setup(); await open(wrapper)
  const signal = apiRequest.mock.calls[0]![2].signal as AbortSignal
  apiRequest.mockResolvedValue({ ...view(), case_id: 'case-b', conversation_id: 'conv-b', run_id: 'run-b',
    record: { ...view().record, estimate: { ...view().record.estimate, input_bytes: 4096 } } })
  await wrapper.setProps({ caseId: 'case-b', conversationId: 'conv-b', runId: 'run-b' }); await flushPromises()
  expect(signal.aborted).toBe(true)
  expect(wrapper.text()).toContain('4 KB / 8 KB')
  release(view()); await flushPromises()
  expect(wrapper.text()).not.toContain('2 KB / 8 KB')
})

test('attempt and status changes refresh only while the card is open', async () => {
  const wrapper = setup(); await wrapper.setProps({ runStatus: 'running' })
  expect(apiRequest).not.toHaveBeenCalled()
  await open(wrapper)
  apiRequest.mockResolvedValue({ ...view(), attempt_id: 'attempt-b' })
  await wrapper.setProps({ attemptId: 'attempt-b' }); await flushPromises()
  await wrapper.setProps({ runStatus: 'completed' }); await flushPromises()
  expect(apiRequest).toHaveBeenCalledTimes(3)
  await wrapper.get('.context-usage-trigger').trigger('click')
  await wrapper.setProps({ runStatus: 'failed' })
  expect(apiRequest).toHaveBeenCalledTimes(3)
})

test('permission loss during a refresh clears previously displayed data', async () => {
  const wrapper = setup(); await open(wrapper)
  apiRequest.mockRejectedValue(new Error('forbidden'))
  await wrapper.setProps({ runStatus: 'failed' }); await flushPromises()
  expect(wrapper.text()).not.toContain('2 KB')
  expect(wrapper.text()).toContain('暂无权限')
})

test('close and reopen ignore a cancelled late read, and unmount cancels the active read', async () => {
  let release!: (value: unknown) => void
  apiRequest.mockReturnValueOnce(new Promise(resolve => { release = resolve }))
  const wrapper = setup(); await open(wrapper)
  const firstSignal = apiRequest.mock.calls[0]![2].signal as AbortSignal
  await wrapper.get('.context-usage-trigger').trigger('click')
  apiRequest.mockResolvedValue({ ...view(), record: null })
  await open(wrapper); release(view()); await flushPromises()
  expect(firstSignal.aborted).toBe(true)
  expect(wrapper.text()).not.toContain('2 KB')
  apiRequest.mockReturnValue(new Promise(() => {}))
  await wrapper.setProps({ runStatus: 'running' }); await flushPromises()
  const activeSignal = apiRequest.mock.calls.at(-1)![2].signal as AbortSignal
  wrapper.unmount(); wrappers.splice(wrappers.indexOf(wrapper), 1)
  expect(activeSignal.aborted).toBe(true)
})
