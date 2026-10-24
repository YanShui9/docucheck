// @vitest-environment jsdom
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AssistantAnswer from './AssistantAnswer.vue'
import { apiRequest } from '../api'

vi.mock('../api', () => ({ apiRequest: vi.fn() }))
vi.mock('../auth', () => ({ accessToken: vi.fn() }))
const request = vi.mocked(apiRequest)
const id = 'a'.repeat(64)
const head = '本次已确认并启用的指标审查完成；未确认或停用项未参与本次审查。\n甲 · 苹果 · 数量：满足。'
const answer = `${head}\n核验来源：\n来源 [${id}]：报价.xlsx · 报价!B4\n原文：20箱`
const props = { answer, kind: 'review', caseId: 'case', snapshotId: 'snap', runId: 'run', revision: 1 }
const result = { result: { run_id: 'run', case_id: 'case', snapshot_id: 'snap', revision: 1 },
  sources: [{ evidence_id: id, document_name: '报价.xlsx', page: null, sheet: '报价', cell: 'B4', source_text: '20箱' }] }
beforeEach(() => { request.mockReset() })
afterEach(() => { vi.useRealTimers() })

test('repeated references share an evidence entry while every body reference stays visible', async () => {
  const citation = { evidence_id: id, file_name: '报价.pdf', location: '第 1 页', quote: '20箱' }
  const wrapper = mount(AssistantAnswer, { props: { ...props, answer: '数量20箱 [1]，交期7天 [2]。', kind: 'evidence',
    citations: [citation, { ...citation, quote: '交期7天' }] } })
  expect(wrapper.get('.chat-citations').findAll('button')).toHaveLength(1)
  expect(wrapper.get('.chat-citations button').text()).toContain('[1][2]')
  await wrapper.get('.chat-citations button').trigger('click')
  expect(wrapper.emitted('cite')).toEqual([[id]])
  wrapper.unmount()
})

test('a source response arriving after the timeout cannot replace the original answer', async () => {
  vi.useFakeTimers()
  let release!: (value: unknown) => void
  request.mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  const wrapper = mount(AssistantAnswer, { props })
  await vi.advanceTimersByTimeAsync(20_000)
  expect(wrapper.text()).toContain('来源暂时无法读取')
  expect(wrapper.text()).not.toContain('正在读取来源')
  release(result); await flushPromises()
  expect(wrapper.get('.answer-text').text()).toBe(answer)
  expect(wrapper.find('.chat-citations').exists()).toBe(false)
  expect(wrapper.text()).toContain('来源暂时无法读取')
  wrapper.unmount()
})

test('only three deduplicated sources show initially and the remaining originals keep their reference numbers', async () => {
  const citations = Array.from({ length: 5 }, (_, i) => ({ evidence_id: String(i).repeat(64), file_name: `文件${i}.pdf`, location: '第 1 页', quote: '原文' }))
  const wrapper = mount(AssistantAnswer, { props: { ...props, answer: '见来源 [5]。', kind: 'evidence', citations: [...citations, citations[0]!] } })
  await flushPromises()
  expect(wrapper.get('.assistant-answer > .chat-citations').findAll('button')).toHaveLength(3)
  expect(wrapper.get('.answer-more-sources summary').text()).toBe('查看其余 2 处来源')
  expect((wrapper.get('.answer-more-sources').element as HTMLDetailsElement).open).toBe(false)
  await wrapper.get('.answer-more-sources button').trigger('click')
  expect(wrapper.emitted('cite')).toEqual([['3'.repeat(64)]])
  expect(request).not.toHaveBeenCalled()
  wrapper.unmount()
})

test('late source responses cannot enter another snapshot', async () => {
  let release!: (value: unknown) => void
  request.mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  const wrapper = mount(AssistantAnswer, { props })
  await wrapper.setProps({ snapshotId: 'new-snap', kind: 'chat', answer: '新的答复' })
  release(result); await flushPromises()
  expect(wrapper.get('.answer-text').text()).toBe('新的答复')
  expect(wrapper.find('.chat-citations').exists()).toBe(false)
  expect(wrapper.find('.answer-original').exists()).toBe(false)
  wrapper.unmount()
})

test('changed result text immediately clears prior sources and cancels their request', async () => {
  let oldSignal: AbortSignal | undefined
  request.mockImplementationOnce((_, __, init) => { oldSignal = init?.signal as AbortSignal; return new Promise(() => {}) })
  const wrapper = mount(AssistantAnswer, { props })
  await wrapper.setProps({ answer: '更新后没有可引用的原文。' })
  expect(oldSignal?.aborted).toBe(true)
  expect(wrapper.find('.chat-citations').exists()).toBe(false)
  wrapper.unmount()
})

test('source denial is forwarded to the containing history permission guard', async () => {
  request.mockRejectedValue(new Error('forbidden'))
  const wrapper = mount(AssistantAnswer, { props }); await flushPromises()
  expect(wrapper.emitted('sourceError')?.[0]?.[0]).toBeInstanceOf(Error)
  expect(wrapper.find('.chat-citations').exists()).toBe(false)
  wrapper.unmount()
})

test('failed read preserves the answer and a retry reads sources without submitting a model task', async () => {
  request.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(result)
  const wrapper = mount(AssistantAnswer, { props }); await flushPromises()
  expect(wrapper.get('.answer-text').text()).toBe(answer)
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(wrapper.get('.answer-text').text()).toBe(head)
  expect(wrapper.find('.answer-original').exists()).toBe(true)
  expect(request.mock.calls.map(([path]) => path)).toEqual([
    '/agent-runs/run/export?format=json', '/agent-runs/run/export?format=json',
  ])
  expect(request.mock.calls.every(([, , init]) => !init?.method)).toBe(true)
  wrapper.unmount()
})

test('a slow source read aborts after twenty seconds and unmount aborts an outstanding read', async () => {
  vi.useFakeTimers()
  const signals: AbortSignal[] = []
  request.mockImplementation((_, __, init) => new Promise((_, reject) => {
    const signal = init?.signal as AbortSignal
    signals.push(signal)
    signal.addEventListener('abort', () => reject(new Error('AbortError')))
  }))
  const wrapper = mount(AssistantAnswer, { props })
  await vi.advanceTimersByTimeAsync(20_000)
  expect(signals[0]?.aborted).toBe(true)
  expect(wrapper.text()).toContain('来源暂时无法读取')
  await wrapper.get('button').trigger('click')
  wrapper.unmount()
  expect(signals[1]?.aborted).toBe(true)
})
