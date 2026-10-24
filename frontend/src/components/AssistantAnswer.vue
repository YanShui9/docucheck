<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { apiRequest } from '../api'
import type { AssistantCitation } from '../api'
import { accessToken } from '../auth'
import { displayAnswer, hasReviewSources, validReviewSources } from '../answerDisplay'
import type { ReviewSource } from '../answerDisplay'

const props = defineProps<{
  answer: string; kind?: string; citations?: AssistantCitation[];
  caseId: string | null; snapshotId: string | null; runId: string; revision: number;
}>()
const emit = defineEmits<{ cite: [evidenceId: string]; sourceError: [reason: unknown] }>()
const sources = ref<ReviewSource[]>()
const loading = ref(false)
const error = ref('')
let generation = 0
let controller: AbortController | undefined
const view = computed(() => displayAnswer(props.answer, props.kind, props.citations ?? [], sources.value))
const first = computed(() => view.value.sources.slice(0, 3))
const rest = computed(() => view.value.sources.slice(3))

async function loadSources() {
  const ticket = ++generation
  controller?.abort()
  sources.value = undefined; error.value = ''; loading.value = false
  if (!hasReviewSources(props.answer, props.kind) || !props.caseId || !props.snapshotId) return
  const context = { caseId: props.caseId, snapshotId: props.snapshotId, runId: props.runId, revision: props.revision }
  controller = new AbortController()
  const requestController = controller
  const cancelled = new Promise<never>((_resolve, reject) => {
    requestController.signal.addEventListener('abort', () => reject(new Error('source_read_cancelled')), { once: true })
  })
  const timeout = setTimeout(() => requestController.abort(), 20_000)
  loading.value = true
  try {
    const payload = await Promise.race([
      apiRequest<{ result: { run_id: string; case_id: string; snapshot_id: string; revision: number }; sources: unknown }>(
        `/agent-runs/${encodeURIComponent(context.runId)}/export?format=json`,
        () => Promise.race([accessToken(), cancelled]), { signal: requestController.signal }),
      cancelled,
    ])
    if (ticket !== generation || requestController.signal.aborted) return
    if (!payload?.result || payload.result.run_id !== context.runId || payload.result.case_id !== context.caseId ||
        payload.result.snapshot_id !== context.snapshotId || payload.result.revision !== context.revision || !validReviewSources(payload.sources)) {
      throw new Error('source_scope')
    }
    sources.value = payload.sources
  } catch (reason) {
    if (ticket !== generation) return
    sources.value = undefined
    error.value = '来源暂时无法读取，原始答复已保留。'
    if (reason instanceof Error && ['not_found', 'forbidden', 'session_expired', 'case_deleting'].includes(reason.message)) emit('sourceError', reason)
  } finally {
    clearTimeout(timeout)
    if (ticket === generation) loading.value = false
  }
}
watch(() => [props.caseId, props.snapshotId, props.runId, props.revision, props.answer, props.kind], loadSources, { immediate: true, flush: 'sync' })
onUnmounted(() => { ++generation; controller?.abort() })
</script>

<template>
  <div class="assistant-answer">
    <p class="answer-text">{{ view.text }}</p>
    <div v-if="first.length" class="chat-citations" aria-label="答复来源">
      <button v-for="source in first" :key="source.evidence_id" type="button" @click="emit('cite', source.evidence_id)">{{ source.numbers.map(number => `[${number}]`).join('') }} {{ source.file_name }} · {{ source.location }} ↗</button>
    </div>
    <details v-if="rest.length" class="answer-more-sources">
      <summary>查看其余 {{ rest.length }} 处来源</summary>
      <div class="chat-citations">
        <button v-for="source in rest" :key="source.evidence_id" type="button" @click="emit('cite', source.evidence_id)">{{ source.numbers.map(number => `[${number}]`).join('') }} {{ source.file_name }} · {{ source.location }} ↗</button>
      </div>
    </details>
    <p v-if="loading" class="state-note" role="status">正在读取来源…</p>
    <p v-if="error" class="state-note" role="status">{{ error }} <button type="button" class="text-button" @click="loadSources">重试读取来源</button></p>
    <details v-if="view.original" class="answer-original">
      <summary>查看原始答复</summary>
      <p>{{ view.original }}</p>
    </details>
  </div>
</template>
