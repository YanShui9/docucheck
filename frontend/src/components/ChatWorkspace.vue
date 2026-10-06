<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { apiRequest } from '../api'
import type { AssistantResult, ChatRun, ConversationRecord, StoredMessage } from '../api'
import { accessToken } from '../auth'
import ChatComposer from './ChatComposer.vue'
const props = defineProps<{ caseId: string | null; scopeKey?: string; revision: number; canWrite: boolean; prepareCase: () => Promise<string | null> }>()
const emit = defineEmits<{ upload: []; openEvidence: [target: { caseId: string; snapshotId: string; evidenceId: string }]; title: [value: { caseId: string; title: string }] }>()
const conversations = ref<ConversationRecord[]>([])
const conversationId = ref<string | null>(null)
const messages = ref<StoredMessage[]>([])
const runs = ref<Record<string, ChatRun>>({})
const results = ref<Record<string, AssistantResult>>({})
const resultErrors = ref<Record<string, string>>({})
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
const activeRun = computed(() => Object.values(runs.value).find(run => ['queued', 'running', 'waiting_user'].includes(run.status)))
const running = computed(() => !!activeRun.value && activeRun.value.status !== 'waiting_user')
const labels: Record<string, string> = { queued: '排队中', running: '正在处理', completed: '处理完成', partial: '部分完成', failed: '处理失败', cancelled: '已停止', waiting_user: '等待补充信息', interrupted: '处理已中断' }
function notice(reason: unknown): string {
  const code = reason instanceof Error ? reason.message : ''
  return ({ revision_conflict: '资料已更新，请重新查看后发送。', conversation_busy: '这个对话仍有任务正在处理，请稍后重试。', session_expired: '登录已过期，请重新登录。', case_deleting: '该采购任务正在删除。', assistant_source_changed: '结果来源发生变化，暂不能展示，请刷新资料后重新处理。', history_limit: '历史记录较多，请选择较新的对话。', run_active: '已有任务正在处理，请稍后重试。' } as Record<string, string>)[code] ?? '暂时无法完成请求。请检查服务或网络后重试。'
}
function current(version: number) { return !disposed && version === generation }
function fail(reason: unknown) {
  error.value = notice(reason)
  const code = reason instanceof Error ? reason.message : ''
  if (['not_found', 'run_not_found', 'forbidden', 'session_expired', 'case_deleting'].includes(code)) {
    messages.value = []; runs.value = {}; results.value = {}; resultErrors.value = {}
    historyReady.value = false
  }
}
async function latest() { await nextTick(); if (atBottom.value && scrollArea.value) scrollArea.value.scrollTop = scrollArea.value.scrollHeight }
function updateScroll() { const area = scrollArea.value; if (area) atBottom.value = area.scrollHeight - area.scrollTop - area.clientHeight < 90 }
function goLatest() { atBottom.value = true; void latest() }
function lastMessageFor(runId: string) { return messages.value.filter(item => item.run_id === runId && item.role === 'user').at(-1)?.id }
function answer(message: StoredMessage) {
  if (!message.run_id || lastMessageFor(message.run_id) !== message.id) return ''
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
async function readRun(runId: string, version: number) {
  const request = (readVersions.get(runId) ?? 0) + 1
  readVersions.set(runId, request)
  const latestRead = () => current(version) && readVersions.get(runId) === request
  try {
    const run = await apiRequest<ChatRun>(`/agent-runs/${runId}`, accessToken)
    if (!latestRead()) return
    if (run.id !== runId || run.conversation_id !== conversationId.value) throw new Error('result_scope')
    runs.value = { ...runs.value, [runId]: run }
    if (!['completed', 'partial', 'waiting_user'].includes(run.status)) { delete results.value[runId]; delete resultErrors.value[runId] }
    if (run.workflow === 'assistant' && ['completed', 'partial', 'waiting_user'].includes(run.status)) {
      try {
        const result = await apiRequest<AssistantResult>(`/agent-runs/${runId}/assistant-result`, accessToken)
        if (!latestRead()) return
        if (result.run_id !== runId || result.case_id !== props.caseId) throw new Error('result_scope')
        if (latestRead()) { results.value = { ...results.value, [runId]: result }; delete resultErrors.value[runId] }
      } catch (reason) {
        if (latestRead()) {
          delete results.value[runId]
          const code = reason instanceof Error ? reason.message : ''
          if (['not_found', 'run_not_found', 'forbidden', 'session_expired', 'case_deleting'].includes(code)) { fail(reason); throw reason }
          else resultErrors.value = { ...resultErrors.value, [runId]: notice(reason) }
        }
      }
    }
  } catch (reason) { if (latestRead()) throw reason }
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
async function loadMessages(version: number) {
  const id = conversationId.value
  if (!id) return
  const stored = await pages<StoredMessage>(`/conversations/${id}/messages`, 'messages', version)
  if (!current(version)) return
  messages.value = stored
  const first = stored.find(item => item.role === 'user')
  if (props.caseId && first) emit('title', { caseId: props.caseId, title: first.content.slice(0, 30) })
  const ids = [...new Set(stored.map(item => item.run_id).filter((id): id is string => !!id))]
  // Bounded batches keep history hydration from flooding the API.
  for (let index = 0; index < ids.length; index += 5) {
    if (!current(version)) return
    await Promise.all(ids.slice(index, index + 5).map(id => readRun(id, version)))
  }
  if (current(version)) { schedule(version); void latest() }
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
  draft.value = drafts.get(`${props.scopeKey ?? ''}:${props.caseId ?? 'new'}:${id ?? 'new'}`) ?? ''
  pending = null; uncertain.value = false; sending.value = false; error.value = ''; atBottom.value = true
  loading.value = true; historyReady.value = false
  try { await loadMessages(version); if (current(version)) historyReady.value = true } catch (reason) { if (current(version)) fail(reason) }
  finally { if (current(version)) loading.value = false }
}
async function send(retry = false) {
  if (!props.canWrite || sending.value || loading.value || running.value || !historyReady.value) return
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
    if (current(version)) await loadMessages(version)
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
  if (!run || sending.value || !props.canWrite) return
  sending.value = true
  try { await apiRequest(`/agent-runs/${run.id}/cancel`, accessToken, { method: 'POST' }); if (current(version)) { await readRun(run.id, version); schedule(version) } }
  catch (reason) { if (current(version)) fail(reason) }
  finally { if (current(version)) sending.value = false }
}
async function retryRun(runId: string) {
  const run = runs.value[runId]
  if (!props.caseId || !props.canWrite || sending.value || loading.value || uncertain.value || activeRun.value || !run?.is_current || run.revision !== props.revision || !['failed', 'partial', 'interrupted'].includes(run.status)) return
  pending = { caseId: props.caseId, conversationId: run.conversation_id, content: '', revision: props.revision, key: crypto.randomUUID(), path: `/agent-runs/${runId}/retry`, resume: false, retry: true }
  await send(true)
}
async function refresh() { if (sending.value || loading.value || uncertain.value) return; error.value = ''; loading.value = true; const version = generation; try { if (!historyReady.value || !conversationId.value) await loadCase(); else await loadMessages(version) } catch (reason) { if (current(version)) fail(reason) } finally { if (current(version)) loading.value = false } }
function cite(message: StoredMessage, evidenceId: string) {
  const result = message.run_id ? results.value[message.run_id] : null
  if (props.caseId && result?.snapshot_id) emit('openEvidence', { caseId: props.caseId, snapshotId: result.snapshot_id, evidenceId })
}
watch(() => [props.caseId, props.scopeKey] as const, (_, previous) => {
  drafts.set(`${previous?.[1] ?? ''}:${previous?.[0] ?? 'new'}:${conversationId.value ?? 'new'}`, draft.value)
  ++generation
  if (timer) clearTimeout(timer)
  conversationId.value = null; conversations.value = []; messages.value = []; runs.value = {}; results.value = {}; resultErrors.value = {}
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
      <button class="text-button" :disabled="sending || loading || uncertain || !canWrite || !!activeRun" @click="selectConversation(null)">新对话</button>
    </div>
    <div ref="scrollArea" class="chat-scroll" @scroll="updateScroll">
      <div v-if="loading && !messages.length" class="chat-loading" role="status">正在读取对话…</div>
      <div v-else-if="!messages.length" class="chat-welcome">
        <div class="assistant-symbol" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M16 4v24M4 16h24M7.5 7.5l17 17m-17 0 17-17" /></svg></div>
        <span class="welcome-eyebrow">你的采购助手</span>
        <h1>今天要采购什么？</h1>
        <p>可以先描述需求，也可以上传需求单和供应商资料。</p>
        <div class="starter-actions">
          <button :disabled="!canWrite" @click="draft = '我要采购20箱苹果，帮我梳理需要确认的采购要求。'"><strong>梳理采购需求</strong><span>从品类、数量和交付要求开始</span></button>
          <button :disabled="!canWrite" @click="emit('upload')"><strong>上传资料开始审查</strong><span>需求单、供应商报价与产品说明</span></button>
        </div>
      </div>
      <div v-else class="chat-transcript">
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
            <div v-if="results[message.run_id]?.result?.candidate" class="candidate-note">已整理候选审查要求。候选内容尚未保存确认。</div>
            <div v-if="runs[message.run_id]" class="message-status" :role="['queued', 'running'].includes(runs[message.run_id]!.status) ? 'status' : undefined">
              {{ labels[runs[message.run_id]!.status] ?? '状态待确认' }}
              <span v-if="runs[message.run_id]!.revision !== revision"> · 历史资料版本 {{ runs[message.run_id]!.revision }}</span>
              <span v-else-if="results[message.run_id] && !results[message.run_id]!.is_current"> · 历史审查要求</span>
            </div>
            <p v-if="runs[message.run_id]?.outcome_unknown" class="state-note">上次模型请求的结果未知，重新处理可能再次调用模型。</p>
            <button v-if="runs[message.run_id]?.is_current && runs[message.run_id]!.revision === revision && ['failed', 'partial', 'interrupted'].includes(runs[message.run_id]!.status) && canWrite" class="text-button" :disabled="sending || loading || uncertain || !!activeRun" @click="retryRun(message.run_id)">重新处理</button>
          </article>
        </template>
      </div>
    </div>
    <div class="composer-dock">
      <button v-if="!atBottom" class="back-to-latest" @click="goLatest">↓ 回到最新</button>
      <div v-if="error" class="chat-error" role="alert">{{ error }} <button v-if="uncertain" class="text-button" @click="send(true)">重试原请求</button><button v-else class="text-button" @click="refresh">刷新对话</button></div>
      <div v-if="activeRun?.status === 'waiting_user'" class="state-note">请补充信息后发送，或<button class="text-button" :disabled="sending || !canWrite" @click="stop">结束本次处理</button>。</div>
      <ChatComposer v-model="draft" :disabled="!canWrite || loading || sending || uncertain || !historyReady" :running="running && canWrite" @send="send()" @upload="emit('upload')" @stop="stop" />
      <p class="composer-disclaimer">{{ canWrite ? '助手结论供采购审查参考，请结合原文确认。' : '当前为只读权限，可查看对话和资料。' }}</p>
    </div>
  </section>
</template>
