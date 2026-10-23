<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import { apiRequest } from '../api'
import type { ContextUsageView } from '../api'
import { accessToken } from '../auth'

const props = defineProps<{
  caseId: string | null; conversationId: string | null; runId: string | null
  attemptId?: string; runStatus?: string
}>()
const opened = ref(false)
const loading = ref(false)
const error = ref('')
const response = ref<ContextUsageView | null>(null)
const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const panelId = `context-usage-${useId()}`
const hasScope = computed(() => !!props.caseId && !!props.conversationId && !!props.runId)
const record = computed(() => response.value?.record)
const processing = computed(() => ['queued', 'running'].includes(props.runStatus ?? response.value?.run_status ?? ''))
const numberFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 })
let generation = 0
let disposed = false
let controller: AbortController | null = null
let timeout: ReturnType<typeof setTimeout> | undefined

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}
function kb(value: unknown) { return finite(value) ? `${numberFormat.format(value / 1024)} KB` : '未记录' }
function tokens(value: unknown) { return finite(value) ? `${numberFormat.format(value)} token` : '未记录' }
function count(value: unknown, unit: string) { return finite(value) ? `${numberFormat.format(value)}${unit}` : '未记录' }
const percent = computed(() => {
  const estimate = record.value?.estimate
  if (!finite(estimate?.input_bytes) || !finite(estimate?.limit_bytes) || estimate.limit_bytes === 0) return null
  const value = estimate.input_bytes / estimate.limit_bytes * 100
  return Number.isFinite(value) ? value : null
})
const summaryStatus = computed(() => ({ completed: '已完成', failed: '未完成', not_recorded: '未记录' })[record.value?.summary_status ?? 'not_recorded'] ?? '未记录')
const requestStatus = computed(() => ({ completed: '已记录', rejected: '未获准发送', failed: '请求失败',
  outcome_unknown: '结果未知', started: '请求中', reserved: '已预留' } as Record<string, string>)[record.value?.status ?? ''] ?? '未记录')
const providerStatus = computed(() => record.value?.provider_called === true ? '已进入模型服务'
  : record.value?.provider_called === false ? '未进入模型服务' : '未记录')

function cancel() {
  ++generation
  if (timeout !== undefined) clearTimeout(timeout)
  timeout = undefined
  controller?.abort()
  controller = null
  loading.value = false
}
function clear() { response.value = null; error.value = '' }
function close() {
  if (!opened.value) return
  opened.value = false
  cancel(); clear()
  void nextTick(() => trigger.value?.focus())
}
function outside(event: Event) {
  if (opened.value && event.target instanceof Node && !root.value?.contains(event.target)) close()
}
function keyboard(event: KeyboardEvent) {
  if (opened.value && event.key === 'Escape') { event.preventDefault(); close() }
}
async function read() {
  cancel(); clear()
  if (!opened.value || !hasScope.value || disposed) return
  const scope = { caseId: props.caseId, conversationId: props.conversationId, runId: props.runId, attemptId: props.attemptId }
  const version = generation
  const abort = new AbortController()
  controller = abort
  loading.value = true
  const current = () => !disposed && opened.value && version === generation && !abort.signal.aborted
  let timedOut = false
  // The same abort promise also bounds authentication, so a late token cannot start a cancelled fetch.
  const cancelled = new Promise<never>((_resolve, reject) => {
    abort.signal.addEventListener('abort', () => reject(new Error(timedOut ? 'context_usage_timeout' : 'context_usage_cancelled')), { once: true })
  })
  timeout = setTimeout(() => { timedOut = true; abort.abort() }, 20000)
  try {
    const result = await Promise.race([
      apiRequest<ContextUsageView>(`/agent-runs/${encodeURIComponent(scope.runId!)}/context-usage`,
        () => Promise.race([accessToken(), cancelled]), { signal: abort.signal }),
      cancelled,
    ])
    if (!current()) return
    if (result.case_id !== scope.caseId || result.conversation_id !== scope.conversationId || result.run_id !== scope.runId
      || (scope.attemptId && result.attempt_id !== scope.attemptId)) throw new Error('context_usage_scope')
    response.value = result
  } catch (reason) {
    if (disposed || !opened.value || version !== generation) return
    const code = reason instanceof Error ? reason.message : ''
    error.value = timedOut ? '读取超时，请重试。'
      : code === 'context_usage_scope' ? '记录归属已变化，请重试。'
      : ['forbidden', 'session_expired', 'http_401', 'http_403'].includes(code) ? '暂无权限读取，请检查登录状态。'
      : ['not_found', 'run_not_found', 'case_deleting', 'http_404'].includes(code) ? '记录已不可用，请刷新对话。'
      : '暂时无法读取，请重试。'
    response.value = null
  } finally {
    if (version === generation) {
      if (timeout !== undefined) clearTimeout(timeout)
      timeout = undefined; loading.value = false
    }
  }
}
async function toggle() {
  if (opened.value) { close(); return }
  opened.value = true
  void read()
  await nextTick()
  if (opened.value) panel.value?.focus()
}
watch(() => [props.caseId, props.conversationId, props.runId, props.attemptId, props.runStatus], () => {
  cancel(); clear()
  if (opened.value) void read()
}, { flush: 'sync' })
// Close after the pointer target's default focus action, then restore the entry focus.
onMounted(() => { document.addEventListener('click', outside); document.addEventListener('keydown', keyboard) })
onUnmounted(() => {
  disposed = true; cancel()
  document.removeEventListener('click', outside); document.removeEventListener('keydown', keyboard)
})
</script>
<template>
  <div ref="root" class="context-usage">
    <button ref="trigger" type="button" class="context-usage-trigger" :aria-expanded="opened"
            :aria-controls="panelId" aria-haspopup="dialog" @click="toggle">上下文 · 估算 <span aria-hidden="true">⌃</span></button>
    <section v-if="opened" :id="panelId" ref="panel" class="context-usage-card" role="dialog" aria-label="上下文用量" tabindex="-1">
      <div class="context-usage-heading"><h2>上下文用量</h2><button type="button" aria-label="关闭上下文用量" @click="close">×</button></div>
      <p class="context-usage-caption">当前对话最近一次已记录请求</p>
      <p v-if="processing" class="context-usage-note">处理中，记录可能尚未更新。</p>
      <p v-if="!hasScope" class="context-usage-empty">发送消息后可查看</p>
      <p v-else-if="loading" class="context-usage-empty" role="status">正在读取…</p>
      <div v-else-if="error" class="context-usage-error" role="alert">
        <p>{{ error }}</p><button type="button" class="text-button" data-test="context-usage-retry" @click="read">重新读取</button>
      </div>
      <p v-else-if="!record" class="context-usage-empty">未记录</p>
      <template v-else>
        <div class="context-usage-estimate">
          <span>估算输入 / 系统输入额度</span>
          <strong>{{ kb(record.estimate?.input_bytes) }} / {{ kb(record.estimate?.limit_bytes) }}</strong>
          <span>{{ percent === null ? '占比未记录' : `${numberFormat.format(percent)}%` }}</span>
          <div v-if="percent !== null" class="context-usage-progress" role="progressbar" aria-label="估算输入占系统输入额度"
               :aria-valuenow="Math.min(100, percent)" aria-valuemin="0" aria-valuemax="100" :aria-valuetext="`${numberFormat.format(percent)}%`">
            <span class="context-usage-progress-fill" :style="{ width: `${Math.min(100, percent)}%` }" />
          </div>
          <p class="context-usage-note">按 UTF-8 字节估算，1 KB = 1024 字节。</p>
        </div>
        <dl class="context-usage-details">
          <div><dt>配置的模型窗口</dt><dd>{{ tokens(record.estimate?.model_window_tokens) }}</dd></div>
          <div><dt>输出预留</dt><dd>{{ tokens(record.estimate?.output_reserved_tokens) }}</dd></div>
          <div><dt>安全预留</dt><dd>{{ tokens(record.estimate?.safety_reserved_tokens) }}</dd></div>
          <div><dt>请求状态</dt><dd>{{ requestStatus }}</dd></div>
          <div><dt>模型服务</dt><dd>{{ providerStatus }}</dd></div>
        </dl>
        <h3>本次请求选用</h3>
        <dl class="context-usage-details">
          <div><dt>近期原话</dt><dd>{{ count(record.memory?.history_turns, '轮') }}</dd></div>
          <div><dt>历史摘要</dt><dd>{{ count(record.memory?.summary_notes, '条') }}</dd></div>
          <div><dt>任务交接</dt><dd>{{ record.memory?.task_handoff === true ? '已选用' : record.memory?.task_handoff === false ? '未选用' : '未记录' }}</dd></div>
        </dl>
        <h3>本轮摘要整理</h3>
        <p class="context-usage-note">{{ summaryStatus }}</p>
        <h3>API 实际用量</h3>
        <p v-if="!record.usage" class="context-usage-note">未记录</p>
        <dl v-else class="context-usage-details">
          <div><dt>输入</dt><dd>{{ tokens(record.usage.input_tokens) }}</dd></div>
          <div><dt>输出</dt><dd>{{ tokens(record.usage.output_tokens) }}</dd></div>
          <div><dt>合计</dt><dd>{{ tokens(record.usage.total_tokens) }}</dd></div>
        </dl>
        <p class="context-usage-note">仅对应这一次请求；配置窗口与字节估算口径不同。</p>
      </template>
    </section>
  </div>
</template>
