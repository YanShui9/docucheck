<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { apiRequest } from '../api'
import type {
  AssistantResult, ChatRun, ChecklistDefinitionPayload, ChecklistView, ConversationRecord,
  MessagesPage, StoredMessage,
} from '../api'
import { candidateState } from '../checklistCandidate'
import type { ChecklistTarget } from '../checklistCandidate'
import { accessToken } from '../auth'
import ChatComposer from './ChatComposer.vue'
const props = defineProps<{ caseId: string | null; scopeKey?: string; revision: number; canWrite: boolean; prepareCase: () => Promise<string | null>;
  busy?: boolean;
  checklistChanges?: Record<string, number>;
  bindingChanges?: Record<string, number>;
  pendingReportCorrections?: Record<string, { caseId: string; snapshotId: string; count: number }> }>()
const emit = defineEmits<{
  upload: []
  openEvidence: [target: { caseId: string; snapshotId: string; evidenceId: string }]
  openChecklist: [target: ChecklistTarget]
  openReview: [target: { caseId: string; snapshotId: string; runId: string }]
  title: [value: { caseId: string; title: string }]
}>()
const conversations = ref<ConversationRecord[]>([])
const conversationId = ref<string | null>(null)
const messages = ref<StoredMessage[]>([])
const runs = ref<Record<string, ChatRun>>({})
const results = ref<Record<string, AssistantResult>>({})
const resultErrors = ref<Record<string, string>>({})
const checklistViews = ref<Record<string, ChecklistView>>({})
const checklistReads = new Map<string, Promise<void>>()
const checklistReadVersions = new Map<string, number>()
const draft = ref('')
const drafts = new Map<string, string>()
const readVersions = new Map<string, number>()
const loading = ref(false)
const historyReady = ref(true)
const sending = ref(false)
const error = ref('')
const scrollArea = ref<HTMLElement | null>(null)
const atBottom = ref(true)
let generation = 0
let disposed = false
let hydration: Promise<void> | null = null
let timer: ReturnType<typeof setTimeout> | undefined
let pending: { caseId: string; conversationId: string; content: string; revision: number; key: string; path: string; resume: boolean; retry?: boolean } | null = null
const uncertain = ref(false)
const olderLoading = ref(false)
const oldestOffset = ref(0)
const totalKnown = ref<number | null>(null)
const PAGE_SIZE = 100
const MAX_PAGES = 20
const activeRun = computed(() => Object.values(runs.value).find(run => ['queued', 'running', 'waiting_user'].includes(run.status)))
const running = computed(() => !!activeRun.value && activeRun.value.status !== 'waiting_user')
const labels: Record<string, string> = { queued: '排队中', running: '正在处理', completed: '处理完成', partial: '部分完成', failed: '处理失败', cancelled: '已停止', waiting_user: '等待补充信息', interrupted: '处理已中断' }
function notice(reason: unknown): string {
  const code = reason instanceof Error ? reason.message : ''
  return ({ revision_conflict: '资料已更新，请重新查看后发送。', conversation_busy: '这个对话仍有任务正在处理，请稍后重试。', session_expired: '登录已过期，请重新登录。', case_deleting: '该采购任务正在删除。', assistant_source_changed: '结果来源发生变化，暂不能展示，请刷新资料后重新处理。', assistant_preview_limit: '连续修改的未保存候选较多，请先打开最新候选并保存为草稿，再继续补充要求。', history_limit: '历史记录较多，请选择较新的对话。', run_active: '已有任务正在处理，请稍后重试。' } as Record<string, string>)[code] ?? '暂时无法完成请求。请检查服务或网络后重试。'
}
function current(version: number) { return !disposed && version === generation }
function fail(reason: unknown) {
  error.value = notice(reason)
  const code = reason instanceof Error ? reason.message : ''
  if (['not_found', 'run_not_found', 'forbidden', 'session_expired', 'case_deleting'].includes(code)) {
    // Losing access invalidates every in-flight read, including independent older-page/status requests.
    ++generation
    if (timer) clearTimeout(timer)
    messages.value = []; runs.value = {}; results.value = {}; resultErrors.value = {}; checklistViews.value = {}
    checklistReads.clear()
    loading.value = false; olderLoading.value = false; sending.value = false; historyReady.value = false
    pending = null; uncertain.value = false
  }
}
async function latest() { await nextTick(); if (atBottom.value && scrollArea.value) scrollArea.value.scrollTop = scrollArea.value.scrollHeight }
function updateScroll() { const area = scrollArea.value; if (area) atBottom.value = area.scrollHeight - area.scrollTop - area.clientHeight < 90 }
function goLatest() { atBottom.value = true; void latest() }
function lastMessageFor(runId: string) { return messages.value.filter(item => item.run_id === runId && item.role === 'user').at(-1)?.id }
function correctionPending(runId: string) {
  const correction = props.pendingReportCorrections?.[runId]
  return correction?.caseId === props.caseId && correction.count > 0
}
function answer(message: StoredMessage) {
  if (!message.run_id || lastMessageFor(message.run_id) !== message.id) return ''
  if (correctionPending(message.run_id)) return ''
  if (resultErrors.value[message.run_id]) return ''
  const run = runs.value[message.run_id]
  if (!run || !['completed', 'partial', 'waiting_user'].includes(run.status)) return ''
  return run.workflow === 'assistant' ? results.value[run.id]?.result?.answer ?? '' : run.workflow === 'legacy' ? run.answer_text ?? '' : ''
}
async function pages<T>(path: string, field: string, version: number): Promise<T[]> {
  const items: T[] = []
  let offset: number | null = 0
  for (let page = 0; offset !== null; page++) {
    if (page >= 20) throw new Error('history_limit')
    const response: Record<string, unknown> = await apiRequest(`${path}?offset=${offset}&limit=100`, accessToken)
    if (!current(version)) return []
    items.push(...response[field] as T[])
    offset = response.next_offset as number | null
  }
  return items
}

async function messagesRange(id: string, from: number, version: number): Promise<StoredMessage[]> {
  const items: StoredMessage[] = []
  let offset: number | null = from
  for (let page = 0; offset !== null; page++) {
    if (page >= MAX_PAGES) throw new Error('history_limit')
    const response: MessagesPage = await apiRequest<MessagesPage>(
      `/conversations/${id}/messages?offset=${offset}&limit=${PAGE_SIZE}`, accessToken)
    if (!current(version)) return []
    items.push(...response.messages)
    offset = response.next_offset
  }
  return items
}
async function readRun(runId: string, version: number) {
  if (correctionPending(runId)) {
    resultErrors.value = { ...resultErrors.value, [runId]: '正在保存事实修正，原结论已暂停展示。' }
    return
  }
  const request = (readVersions.get(runId) ?? 0) + 1
  readVersions.set(runId, request)
  const latestRead = () => current(version) && readVersions.get(runId) === request
  try {
    const run = await apiRequest<ChatRun>(`/agent-runs/${runId}`, accessToken)
    if (!latestRead()) return
    if (run.id !== runId || run.conversation_id !== conversationId.value) throw new Error('result_scope')
    runs.value = { ...runs.value, [runId]: run }
    if (run.workflow === 'legacy') delete resultErrors.value[runId]
    if (!['completed', 'partial', 'waiting_user'].includes(run.status)) { delete results.value[runId]; delete resultErrors.value[runId] }
    if (run.workflow === 'assistant' && ['completed', 'partial', 'waiting_user'].includes(run.status)) {
      try {
        const result = await apiRequest<AssistantResult>(`/agent-runs/${runId}/assistant-result`, accessToken)
        if (!latestRead()) return
        if (result.run_id !== runId || result.case_id !== props.caseId) throw new Error('result_scope')
        if (latestRead()) { results.value = { ...results.value, [runId]: result }; delete resultErrors.value[runId] }
        if (result.snapshot_id && result.result?.candidate) await readChecklist(result.snapshot_id, version)
      } catch (reason) {
        if (latestRead()) {
          delete results.value[runId]
          const code = reason instanceof Error ? reason.message : ''
          if (['not_found', 'run_not_found', 'forbidden', 'session_expired', 'case_deleting'].includes(code)) { fail(reason); throw reason }
          else resultErrors.value = { ...resultErrors.value, [runId]: notice(reason) }
        }
      }
    }
  } catch (reason) {
    if (!latestRead()) return
    const code = reason instanceof Error ? reason.message : ''
    if (['not_found', 'run_not_found', 'forbidden', 'session_expired', 'case_deleting'].includes(code)) fail(reason)
    else throw reason
  }
}
async function readChecklist(snapshotId: string, version: number, refresh = false): Promise<void> {
  if (!current(version) || !props.caseId) return
  if (!refresh && checklistViews.value[snapshotId]) return
  if (!refresh && checklistReads.has(snapshotId)) return checklistReads.get(snapshotId)
  const caseId = props.caseId
  const request = (checklistReadVersions.get(snapshotId) ?? 0) + 1
  checklistReadVersions.set(snapshotId, request)
  delete checklistViews.value[snapshotId]
  const latestRead = () => current(version) && checklistReadVersions.get(snapshotId) === request
  const reading = (async () => {
    try {
      const view = await apiRequest<ChecklistView>(`/cases/${caseId}/snapshots/${snapshotId}/checklist`, accessToken)
      if (!latestRead()) return
      if (view.case_id !== caseId || view.snapshot_id !== snapshotId) throw new Error('result_scope')
      checklistViews.value = { ...checklistViews.value, [snapshotId]: view }
    } catch (reason) {
      if (!latestRead()) return
      const code = reason instanceof Error ? reason.message : ''
      if (['not_found', 'run_not_found', 'forbidden', 'session_expired', 'case_deleting'].includes(code)) throw reason
      // No view is safer than retaining a stale saved/confirmed label. The card offers a scoped GET retry.
    }
    finally { if (latestRead()) checklistReads.delete(snapshotId) }
  })()
  checklistReads.set(snapshotId, reading)
  return reading
}
function refreshChecklist(snapshotId: string, version = generation) {
  void readChecklist(snapshotId, version, true).catch(reason => { if (current(version)) fail(reason) })
}
async function hydrateRuns(stored: StoredMessage[], version: number, onlyNew = false) {
  const ids = [...new Set(stored.map(item => item.run_id).filter((id): id is string => !!id))]
    .filter(id => !onlyNew || !runs.value[id])
  // Wait for each bounded batch to settle before reporting a failure or starting the next batch.
  for (let index = 0; index < ids.length; index += 5) {
    if (!current(version)) return
    const settled = await Promise.allSettled(ids.slice(index, index + 5).map(async id => {
      try { await readRun(id, version) }
      catch (reason) {
        if (!current(version)) return
        const code = reason instanceof Error ? reason.message : ''
        if (['not_found', 'run_not_found', 'forbidden', 'session_expired', 'case_deleting'].includes(code)) throw reason
        delete results.value[id]
        resultErrors.value = { ...resultErrors.value, [id]: notice(reason) }
      }
    }))
    if (!current(version)) return
    const rejected = settled.find(item => item.status === 'rejected')
    if (rejected?.status === 'rejected') throw rejected.reason
  }
}
function schedule(version: number, delay = 2500) {
  if (timer) clearTimeout(timer)
  if (!current(version) || !running.value) return
  timer = setTimeout(async () => {
    try {
      for (const run of Object.values(runs.value).filter(item => ['queued', 'running'].includes(item.status))) {
        if (!current(version)) return
        await readRun(run.id, version)
      }
      if (current(version)) { error.value = ''; void latest(); schedule(version) }
    } catch (reason) { if (current(version)) { fail(reason); schedule(version, 10000) } }
  }, delay)
}
async function loadMessages(version: number, keepOldest?: number) {
  const id = conversationId.value
  if (!id) return
  let stored: StoredMessage[]
  if (keepOldest !== undefined) {
    stored = await messagesRange(id, keepOldest, version)
    if (!current(version)) return
    oldestOffset.value = keepOldest
  } else {
    const probe = await apiRequest<MessagesPage>(
      `/conversations/${id}/messages?offset=0&limit=1`, accessToken)
    if (!current(version)) return
    const total = typeof probe.total === 'number' ? probe.total : null
    totalKnown.value = total
    if (total === null || total <= PAGE_SIZE) {
      stored = await messagesRange(id, 0, version)
      if (!current(version)) return
      oldestOffset.value = 0
    } else {
      const tailOffset = total - PAGE_SIZE
      const page = await apiRequest<MessagesPage>(
        `/conversations/${id}/messages?offset=${tailOffset}&limit=${PAGE_SIZE}`, accessToken)
      if (!current(version)) return
      stored = page.messages
      oldestOffset.value = tailOffset
    }
  }
  messages.value = stored
  const first = stored.find(item => item.role === 'user')
  if (props.caseId && first) emit('title', { caseId: props.caseId, title: first.content.slice(0, 30) })
  await hydrateRuns(stored, version)
  if (current(version)) { schedule(version); void latest() }
}

async function loadOlder() {
  const version = generation
  const id = conversationId.value
  if (!id || olderLoading.value || loading.value || sending.value || uncertain.value || oldestOffset.value <= 0) return
  const previousOffset = Math.max(0, oldestOffset.value - PAGE_SIZE)
  if (totalKnown.value !== null &&
      Math.ceil((totalKnown.value - previousOffset) / PAGE_SIZE) > MAX_PAGES) {
    fail(new Error('history_limit'))
    return
  }
  olderLoading.value = true
  const area = scrollArea.value
  const scrollHeightBefore = area?.scrollHeight ?? 0
  try {
    const page = await apiRequest<MessagesPage>(
      `/conversations/${id}/messages?offset=${previousOffset}&limit=${PAGE_SIZE}`, accessToken)
    if (!current(version)) return
    const known = new Set(messages.value.map(item => item.id))
    const fresh = page.messages.filter(item => !known.has(item.id))
    if (fresh.length) {
      messages.value = [...fresh, ...messages.value]
    }
    oldestOffset.value = previousOffset
    await hydrateRuns(fresh, version, true)
    if (!current(version)) return
    schedule(version)
    await nextTick()
    if (current(version) && area) area.scrollTop += area.scrollHeight - scrollHeightBefore
  } catch (reason) { if (current(version)) fail(reason) }
  finally { if (current(version)) olderLoading.value = false }
}
async function loadCase() {
  const version = generation
  if (!props.caseId) return
  loading.value = true
  historyReady.value = false
  try {
    const list = await pages<ConversationRecord>(`/cases/${props.caseId}/conversations`, 'conversations', version)
    if (!current(version)) return
    conversations.value = list
    conversationId.value = list.at(-1)?.id ?? null
    draft.value = drafts.get(`${props.scopeKey ?? ''}:${props.caseId}:${conversationId.value ?? 'new'}`) ?? draft.value
    await loadMessages(version)
    if (current(version)) historyReady.value = true
  } catch (reason) { if (current(version)) fail(reason) }
  finally { if (current(version)) loading.value = false }
}
async function selectConversation(id: string | null) {
  const version = ++generation
  if (timer) clearTimeout(timer)
  drafts.set(`${props.scopeKey ?? ''}:${props.caseId ?? 'new'}:${conversationId.value ?? 'new'}`, draft.value)
  conversationId.value = id; messages.value = []; runs.value = {}; results.value = {}; resultErrors.value = {}
  checklistViews.value = {}; checklistReads.clear()
  olderLoading.value = false
  oldestOffset.value = 0; totalKnown.value = null
  draft.value = drafts.get(`${props.scopeKey ?? ''}:${props.caseId ?? 'new'}:${id ?? 'new'}`) ?? ''
  pending = null; uncertain.value = false; sending.value = false; error.value = ''; atBottom.value = true
  loading.value = true; historyReady.value = false
  try { await loadMessages(version); if (current(version)) historyReady.value = true } catch (reason) { if (current(version)) fail(reason) }
  finally { if (current(version)) loading.value = false }
}
async function send(retry = false) {
  if (!props.canWrite || props.busy || sending.value || loading.value || olderLoading.value || running.value || !historyReady.value) return
  const content = retry && pending ? pending.content : draft.value.trim()
  if ((!content && !(retry && pending?.retry)) || (!retry && uncertain.value)) return
  let version = generation
  sending.value = true; error.value = ''
  try {
    let caseId = props.caseId
    if (!caseId) {
      caseId = await props.prepareCase()
      if (!caseId || disposed || props.caseId !== caseId) return
      version = generation
      sending.value = true
      if (hydration) await hydration
      if (!current(version)) return
      if (!historyReady.value) return
    }
    if (!conversationId.value) {
      const created = await apiRequest<ConversationRecord>(`/cases/${caseId}/conversations`, accessToken, { method: 'POST' })
      if (!current(version)) return
      conversations.value.push(created); conversationId.value = created.id
    }
    if (!current(version)) return
    const waiting = activeRun.value?.status === 'waiting_user' ? activeRun.value : null
    pending ??= { caseId, conversationId: conversationId.value, content, revision: props.revision, key: crypto.randomUUID(),
      path: waiting ? `/agent-runs/${waiting.id}/resume` : `/conversations/${conversationId.value}/messages`, resume: !!waiting }
    const request = pending
    const receipt = await apiRequest<{ message_id?: string; run_id: string }>(request.path, accessToken, {
      method: 'POST', headers: { 'Idempotency-Key': request.key }, body: JSON.stringify(request.retry
        ? { expected_revision: request.revision } : request.resume
        ? { content: request.content, expected_revision: request.revision }
        : { content: request.content, operation: 'assistant', mode: 'standard', expected_revision: request.revision }),
    })
    if (!current(version)) return
    pending = null; uncertain.value = false; if (!request.retry) { draft.value = ''; drafts.delete(`${props.scopeKey ?? ''}:new:new`); drafts.delete(`${props.scopeKey ?? ''}:${props.caseId}:${conversationId.value}`) }; atBottom.value = true
    await readRun(receipt.run_id, version)
    if (current(version)) {
      try { await loadMessages(version, oldestOffset.value > 0 ? oldestOffset.value : undefined) }
      catch (reason) {
        if (reason instanceof Error && reason.message === 'history_limit') await loadMessages(version)
        else throw reason
      }
    }
    if (current(version)) schedule(version)
  } catch (reason) {
    if (current(version)) {
      fail(reason)
      const code = reason instanceof Error ? reason.message : ''
      const rejected = ['revision_conflict', 'conversation_busy', 'run_active', 'case_deleting', 'forbidden', 'not_found', 'run_not_found', 'http_422'].includes(code)
      if (rejected) pending = null
      uncertain.value = !!pending
      if (!conversationId.value) historyReady.value = false
    }
  } finally { if (current(version)) sending.value = false }
}
async function stop() {
  const run = activeRun.value; const version = generation
  if (!run || sending.value || !props.canWrite || props.busy) return
  sending.value = true
  try { await apiRequest(`/agent-runs/${run.id}/cancel`, accessToken, { method: 'POST' }); if (current(version)) { await readRun(run.id, version); schedule(version) } }
  catch (reason) { if (current(version)) fail(reason) }
  finally { if (current(version)) sending.value = false }
}
async function retryRun(runId: string) {
  const run = runs.value[runId]
  if (!props.caseId || !props.canWrite || props.busy || sending.value || loading.value || uncertain.value || activeRun.value || !run?.is_current || run.revision !== props.revision || !['failed', 'partial', 'interrupted'].includes(run.status)) return
  pending = { caseId: props.caseId, conversationId: run.conversation_id, content: '', revision: props.revision, key: crypto.randomUUID(), path: `/agent-runs/${runId}/retry`, resume: false, retry: true }
  await send(true)
}
async function refresh() {
  if (sending.value || loading.value || uncertain.value || olderLoading.value) return
  const version = generation
  error.value = ''; loading.value = true; checklistViews.value = {}
  for (const snapshotId of checklistReads.keys()) {
    checklistReadVersions.set(snapshotId, (checklistReadVersions.get(snapshotId) ?? 0) + 1)
  }
  checklistReads.clear()
  try {
    if (!historyReady.value || !conversationId.value) await loadCase()
    else await loadMessages(version, oldestOffset.value)
  } catch (reason) { if (current(version)) fail(reason) }
  finally { if (current(version)) loading.value = false }
}
function cite(message: StoredMessage, evidenceId: string) {
  const result = message.run_id ? results.value[message.run_id] : null
  if (props.caseId && result?.snapshot_id) emit('openEvidence', { caseId: props.caseId, snapshotId: result.snapshot_id, evidenceId })
}
function candidateOf(message: StoredMessage): ChecklistDefinitionPayload | null {
  const result = message.run_id ? results.value[message.run_id] : null
  const kind = result?.result?.kind
  return result?.result?.candidate && (kind === 'changes' || kind === 'proposal')
    ? result.result.candidate : null
}
function stateOf(message: StoredMessage) {
  const result = message.run_id ? results.value[message.run_id] : null
  const candidate = candidateOf(message)
  if (!candidate || !result?.snapshot_id) return 'unknown'
  if (result.revision !== props.revision) return 'historical'
  const view = checklistViews.value[result.snapshot_id]
  if (view && view.revision !== result.revision) return 'unknown'
  return candidateState(candidate, view, props.revision, result.checklist_revision, result.is_current)
}
const candidateLabels = { unknown: '状态待核实', candidate: '待确认的审查要求', saved: '已保存，待确认',
  confirmed: '已确认的审查要求', partial: '部分已确认', historical: '历史候选（只读）' }
function candidateNote(message: StoredMessage) {
  const state = stateOf(message)
  if (state === 'confirmed') {
    const result = results.value[message.run_id!]
    return checklistViews.value[result!.snapshot_id!]?.execution_available
      ? '当前已确认要求与此候选一致，可用于正式审查。'
      : '此候选与已确认版本一致；当前另有草稿待确认，暂不能直接执行完整审查。'
  }
  return { unknown: '尚未核实最新清单状态，请重新读取。',
    candidate: '候选内容尚未保存确认。请查看、编辑并保存，再明确确认后才会用于正式审查。',
    saved: '当前草稿与此候选一致。保存不代表确认，请明确确认后再进行正式审查。',
    partial: '部分指标已确认，其余指标保留为草稿；正式审查只使用已确认部分。',
    historical: '资料或审查要求已变化。此候选仅供追溯，不能直接写入当前清单。' }[state]
}
function openChecklist(message: StoredMessage) {
  const result = message.run_id ? results.value[message.run_id] : null
  const candidate = candidateOf(message)
  if (!result?.snapshot_id || !candidate) return
  const state = stateOf(message)
  if (state === 'unknown') { refreshChecklist(result.snapshot_id); return }
  emit('openChecklist', { caseId: result.case_id, snapshotId: result.snapshot_id,
    runId: message.run_id!, ...(state === 'historical' ? { readOnly: true } : {}),
    candidate: ['saved', 'confirmed', 'partial'].includes(state) ? null : {
      candidate, changes: result.result?.changes ?? [], runId: message.run_id!, baseChecklistRevision: result.checklist_revision } })
}
function openReview(message: StoredMessage) {
  const result = message.run_id ? results.value[message.run_id] : null
  if (!result?.snapshot_id) return
  emit('openReview', { caseId: result.case_id, snapshotId: result.snapshot_id, runId: message.run_id! })
}
watch(() => props.pendingReportCorrections, (pending, previous) => {
  for (const runId of new Set([...Object.keys(pending ?? {}), ...Object.keys(previous ?? {})])) {
    if (pending?.[runId] === previous?.[runId]) continue
    const target = pending?.[runId] ?? previous?.[runId]
    if (!target || target.caseId !== props.caseId || !messages.value.some(message => message.run_id === runId)
      || (runs.value[runId] && runs.value[runId]!.snapshot_id !== target.snapshotId)) continue
    const version = generation
    // Remove the cached answer before reading; a failed GET must never restore the old conclusion.
    delete results.value[runId]; delete resultErrors.value[runId]
    if (runs.value[runId]) runs.value[runId] = { ...runs.value[runId]!, answer_text: null }
    readVersions.set(runId, (readVersions.get(runId) ?? 0) + 1)
    if ((pending?.[runId]?.count ?? 0) > 0) {
      resultErrors.value = { ...resultErrors.value, [runId]: '正在保存事实修正，原结论已暂停展示。' }
      continue
    }
    void readRun(runId, version).catch(reason => {
      if (current(version) && !correctionPending(runId)) {
        resultErrors.value = { ...resultErrors.value, [runId]: notice(reason) }
      }
    })
  }
}, { flush: 'sync' })
watch(() => props.checklistChanges, (changes, previous) => {
  for (const snapshotId of Object.keys(changes ?? {})) {
    if (changes?.[snapshotId] === previous?.[snapshotId]) continue
    if (Object.values(results.value).some(result => result.case_id === props.caseId && result.snapshot_id === snapshotId && result.result?.candidate)) {
      refreshChecklist(snapshotId)
    }
  }
}, { flush: 'sync' })
watch(() => props.bindingChanges, (changes, previous) => {
  for (const snapshotId of Object.keys(changes ?? {})) {
    if (changes?.[snapshotId] === previous?.[snapshotId]) continue
    for (const [runId, run] of Object.entries(runs.value)) {
      if (run.snapshot_id !== snapshotId) continue
      const version = generation
      // Stop presenting cached currentness before the server revalidates its frozen binding.
      runs.value[runId] = { ...run, is_current: false, answer_text: null }
      delete results.value[runId]
      resultErrors.value[runId] = '资料对应关系已变化，正在核对历史结果。'
      readVersions.set(runId, (readVersions.get(runId) ?? 0) + 1)
      void readRun(runId, version).catch(reason => {
        if (current(version)) resultErrors.value = { ...resultErrors.value, [runId]: notice(reason) }
      })
    }
  }
}, { flush: 'sync' })
watch(() => [props.caseId, props.scopeKey] as const, (_, previous) => {
  drafts.set(`${previous?.[1] ?? ''}:${previous?.[0] ?? 'new'}:${conversationId.value ?? 'new'}`, draft.value)
  ++generation
  if (timer) clearTimeout(timer)
  conversationId.value = null; conversations.value = []; messages.value = []; runs.value = {}; results.value = {}; resultErrors.value = {}
  checklistViews.value = {}; checklistReads.clear(); olderLoading.value = false
  oldestOffset.value = 0; totalKnown.value = null
  draft.value = drafts.get(`${props.scopeKey ?? ''}:${props.caseId ?? 'new'}:new`) ?? ''
  loading.value = false; historyReady.value = true; sending.value = false; error.value = ''; pending = null; uncertain.value = false; atBottom.value = true
  hydration = loadCase()
}, { immediate: true })
onUnmounted(() => { disposed = true; ++generation; if (timer) clearTimeout(timer) })
</script>
<template>
  <section class="chat-workspace" aria-label="采购助手对话">
    <div v-if="conversations.length" class="conversation-toolbar">
      <label>对话 <select aria-label="选择对话" :value="conversationId ?? ''" :disabled="sending || uncertain" @change="selectConversation(($event.target as HTMLSelectElement).value)">
        <option v-for="(item, index) in conversations" :key="item.id" :value="item.id">对话 {{ index + 1 }} · {{ new Date(item.created_at).toLocaleDateString('zh-CN') }}</option>
      </select></label>
      <button class="text-button" :disabled="sending || loading || uncertain || !canWrite || busy || !!activeRun" @click="selectConversation(null)">新对话</button>
    </div>
    <div ref="scrollArea" class="chat-scroll" @scroll="updateScroll">
      <div v-if="loading && !messages.length" class="chat-loading" role="status">正在读取对话…</div>
      <div v-else-if="!messages.length" class="chat-welcome">
        <div class="assistant-symbol" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M16 4v24M4 16h24M7.5 7.5l17 17m-17 0 17-17" /></svg></div>
        <span class="welcome-eyebrow">你的采购助手</span>
        <h1>今天要采购什么？</h1>
        <p>可以先描述需求，也可以上传需求单和供应商资料。</p>
        <div class="starter-actions">
          <button :disabled="!canWrite || busy" @click="draft = '我要采购20箱苹果，帮我梳理需要确认的采购要求。'"><strong>梳理采购需求</strong><span>从品类、数量和交付要求开始</span></button>
          <button :disabled="!canWrite || busy" @click="emit('upload')"><strong>上传资料开始审查</strong><span>需求单、供应商报价与产品说明</span></button>
        </div>
      </div>
      <div v-else class="chat-transcript">
        <button v-if="oldestOffset > 0" class="text-button history-older" data-test="load-older"
                :disabled="olderLoading" @click="loadOlder()">
          {{ olderLoading ? '正在读取更早消息…' : `加载更早消息（剩余 ${oldestOffset} 条）` }}
        </button>
        <template v-for="message in messages" :key="message.id">
          <article :class="['chat-message', message.role]">
            <span v-if="message.role === 'assistant'" class="message-author">DocuCheck</span>
            <p>{{ message.content }}</p>
          </article>
          <article v-if="message.run_id && lastMessageFor(message.run_id) === message.id" class="chat-message assistant">
            <span class="message-author"><span class="mini-symbol" aria-hidden="true">✳</span> DocuCheck</span>
            <p v-if="answer(message)">{{ answer(message) }}</p>
            <p v-if="resultErrors[message.run_id]" class="state-note" role="alert">{{ resultErrors[message.run_id] }}</p>
            <div v-if="results[message.run_id]?.result?.citations?.length" class="chat-citations">
              <button v-for="citation in results[message.run_id]?.result?.citations" :key="citation.evidence_id" @click="cite(message, citation.evidence_id)">{{ citation.file_name }} · {{ citation.location }} ↗</button>
            </div>
            <div v-if="candidateOf(message)" class="candidate-card">
              <p class="candidate-title">{{ candidateLabels[stateOf(message)] }} · {{ candidateOf(message)!.items.length }} 个品目 / {{ candidateOf(message)!.criteria.length }} 项指标</p>
              <p class="candidate-note">{{ candidateNote(message) }}</p>
              <button class="text-button" @click="openChecklist(message)">{{ stateOf(message) === 'unknown' ? '重新读取状态' : stateOf(message) === 'historical' ? '查看历史候选' : stateOf(message) === 'candidate' ? '查看与编辑' : '查看当前清单' }}</button>
            </div>
            <div v-if="results[message.run_id]?.result?.kind === 'review' && results[message.run_id]?.snapshot_id" class="review-link">
              <button class="text-button" @click="openReview(message)">查看完整报告 →</button>
            </div>
            <div v-if="runs[message.run_id]" class="message-status" :role="['queued', 'running'].includes(runs[message.run_id]!.status) ? 'status' : undefined">
              {{ labels[runs[message.run_id]!.status] ?? '状态待确认' }}
              <span v-if="runs[message.run_id]!.revision !== revision"> · 历史资料版本 {{ runs[message.run_id]!.revision }}</span>
              <span v-else-if="results[message.run_id] && !results[message.run_id]!.is_current && !candidateOf(message)"> · 历史资料对应或审查要求</span>
            </div>
            <p v-if="runs[message.run_id]?.outcome_unknown" class="state-note">上次模型请求的结果未知，重新处理可能再次调用模型。</p>
            <button v-if="runs[message.run_id]?.is_current && runs[message.run_id]!.revision === revision && ['failed', 'partial', 'interrupted'].includes(runs[message.run_id]!.status) && canWrite" class="text-button" :disabled="sending || loading || uncertain || busy || !!activeRun" @click="retryRun(message.run_id)">重新处理</button>
          </article>
        </template>
      </div>
    </div>
    <div class="composer-dock">
      <button v-if="!atBottom" class="back-to-latest" @click="goLatest">↓ 回到最新</button>
      <div v-if="error" class="chat-error" role="alert">{{ error }} <button v-if="uncertain" class="text-button" @click="send(true)">重试原请求</button><button v-else class="text-button" @click="refresh">刷新对话</button></div>
      <button v-else-if="Object.keys(resultErrors).length" class="text-button" :disabled="loading || sending || olderLoading" @click="refresh">刷新对话</button>
      <div v-if="activeRun?.status === 'waiting_user'" class="state-note">请补充信息后发送，或<button class="text-button" :disabled="sending || !canWrite || busy" @click="stop">结束本次处理</button>。</div>
      <ChatComposer v-model="draft" :disabled="!canWrite || busy || loading || sending || olderLoading || uncertain || !historyReady" :running="running && canWrite && !busy" @send="send()" @upload="emit('upload')" @stop="stop" />
      <p class="composer-disclaimer">{{ !canWrite ? '当前为只读权限，可查看对话和资料。' : busy ? '正在更新采购任务，完成后可继续发送消息。' : '助手结论供采购审查参考，请结合原文确认。' }}</p>
    </div>
  </section>
</template>
