<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { accessToken } from '../auth'
import { apiRequest, downloadRunExport } from '../api'
import type {
  CaseRunsResult, CheckResult, CorrectionRecord, DocumentsResult, FieldKey,
  RunFactsResult, RunSummary, RunChecksResult,
} from '../api'

const props = defineProps<{
  caseId: string | null
  documents: DocumentsResult | null
  historyDocuments: DocumentsResult | null
  selectedHistoryId: string | null
  canWrite: boolean
}>()
const emit = defineEmits<{
  openEvidence: [target: { caseId: string; snapshotId: string; evidenceId: string }]
  changed: [target: { caseId: string; fromSnapshotId: string }]
}>()

const fields: { key: FieldKey; label: string }[] = [
  { key: 'memory', label: '内存' }, { key: 'storage', label: '存储' },
  { key: 'screen_size', label: '屏幕尺寸' }, { key: 'weight', label: '重量' },
  { key: 'warranty', label: '质保' }, { key: 'quantity', label: '采购数量' },
  { key: 'unit_price', label: '单价' }, { key: 'delivery_days', label: '交付期限' },
]
const statusNames: Record<string, string> = {
  queued: '排队中', running: '运行中', waiting_user: '待澄清', completed: '已完成',
  partial: '部分完成', failed: '失败', interrupted: '已中断', cancelled: '已取消',
}
const reasonNames: Record<string, string> = {
  threshold_met: '符合明确需求阈值', threshold_failed: '不符合需求阈值',
  conflicting_supplier_values: '同一口径的供应商资料相互冲突',
  contradicting_installed_capacity: '已装内存来源数值冲突',
  supplier_fact_missing: '缺少适用的供应商证据',
  requirement_missing: '缺少可用的采购需求',
  coverage_incomplete: '资料处理尚未完成',
  semantics_unclear: '单位、限定语或计价口径待确认',
  requirement_unclear: '采购需求口径待确认',
  extraction_failed: '字段抽取失败',
  supplier_capacity_not_exact: '供应商未提供确定容量',
}

const shownSnapshot = computed(() => props.selectedHistoryId
  ? props.historyDocuments : props.documents)
const snapshotId = computed(() => shownSnapshot.value?.snapshot_id ?? null)
const isHistory = computed(() => props.selectedHistoryId !== null)
const supplierHandles = computed(() => [...new Set(
  (shownSnapshot.value?.documents ?? []).map(item => item.supplier_handle)
    .filter((item): item is string => Boolean(item)),
)])
const runs = ref<RunSummary[]>([])
const nextRunOffset = ref<number | null>(null)
const selectedRunId = ref<string | null>(null)
const checks = ref<RunChecksResult | null>(null)
const facts = ref<RunFactsResult | null>(null)
const events = ref<{ seq: number; kind: string; status: string }[]>([])
const correction = ref<CorrectionRecord | null>(null)
const loading = ref(false)
const busy = ref(false)
const exporting = ref(false)
const error = ref('')
const question = ref('')
const mode = ref<'standard' | 'thorough'>('standard')
const clarification = ref('')
const selectedFactKey = ref('')
const correctedValue = ref('')
const correctedUnit = ref('')
const correctionReason = ref('')
let viewVersion = 0
let runsRequest = 0
let detailRequest = 0
let timer: ReturnType<typeof setInterval> | undefined

const selectedRun = computed(() => runs.value.find(item => item.id === selectedRunId.value) ?? null)
const allFacts = computed(() => (facts.value?.fact_sets ?? []).flatMap(set =>
  set.facts.map(fact => ({ ...fact, factSetId: set.fact_set_id,
    fieldKey: set.field_key, supplierHandle: set.supplier,
    key: `${set.fact_set_id}/${fact.fact_id}` })),
))
const selectedFact = computed(() => allFacts.value.find(item => item.key === selectedFactKey.value))
const canCorrect = computed(() => props.canWrite && !isHistory.value &&
  selectedRun.value && ['completed', 'partial'].includes(selectedRun.value.status) &&
  selectedRun.value.revision === props.documents?.revision && facts.value?.available &&
  !correction.value)
const matrixSuppliers = computed(() => supplierHandles.value.length ? supplierHandles.value
  : [...new Set((checks.value?.checks ?? []).map(item => item.supplier))])

function messageFor(reason: unknown): string {
  const code = reason instanceof Error ? reason.message : 'request_failed'
  return ({ revision_conflict: '资料已更新，请刷新后重试。',
    conversation_busy: '该会话仍有任务运行中。',
    correction_pending_recheck: '事实修正仍在复核中。',
    facts_unavailable: '本次运行没有可修正的事实集。',
    checks_unavailable: '本次运行没有可导出的结构化结果。',
    xlsx_cell_limit: '有一处原文超过 XLSX 单元格长度上限，请改用 JSON 导出。',
    source_run_not_current: '此运行已不是当前资料版本。',
    memory_scope: '结构化结果未通过来源校验。',
    not_found: '资源不存在或无权访问。',
  } as Record<string, string>)[code] ?? `操作未完成（${code}）。`
}

function checkFor(supplier: string, field: FieldKey): CheckResult | undefined {
  return checks.value?.checks.find(item => item.supplier === supplier && item.field_key === field)
}

function openEvidence(evidenceId: string) {
  if (props.caseId && selectedRun.value) emit('openEvidence', {
    caseId: props.caseId, snapshotId: selectedRun.value.snapshot_id, evidenceId,
  })
}

async function exportRun(format: 'json' | 'xlsx') {
  if (!selectedRun.value || !checks.value?.checks.length || exporting.value) return
  exporting.value = true
  error.value = ''
  const version = viewVersion
  try {
    const runId = selectedRun.value.id
    const blob = await downloadRunExport(runId, format, accessToken)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `docucheck-${runId}.${format}`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (reason) {
    if (version === viewVersion) error.value = messageFor(reason)
  } finally {
    if (version === viewVersion) exporting.value = false
  }
}

async function loadRun(runId: string, version = viewVersion) {
  if (!props.caseId) return
  const request = ++detailRequest
  try {
    const [result, factResult, eventResult, runDetail] = await Promise.all([
      apiRequest<RunChecksResult>(`/agent-runs/${runId}/checks`, accessToken),
      apiRequest<RunFactsResult>(`/agent-runs/${runId}/facts`, accessToken),
      apiRequest<{ events: typeof events.value }>(`/agent-runs/${runId}/events`, accessToken),
      apiRequest<{ correction_id: string | null; outgoing_correction_id: string | null }>(
        `/agent-runs/${runId}`, accessToken),
    ])
    if (version !== viewVersion || request !== detailRequest ||
        selectedRunId.value !== runId) return
    checks.value = result
    facts.value = factResult
    events.value = eventResult.events
    const correctionId = runDetail.outgoing_correction_id ?? runDetail.correction_id
    const loadedCorrection = correctionId
      ? await apiRequest<CorrectionRecord>(
        `/cases/${props.caseId}/corrections/${correctionId}`, accessToken)
      : null
    if (version === viewVersion && request === detailRequest &&
        selectedRunId.value === runId) correction.value = loadedCorrection
  } catch (reason) {
    if (version === viewVersion && request === detailRequest &&
        selectedRunId.value === runId) error.value = messageFor(reason)
  }
}

async function loadRuns(preserve = false, append = false) {
  const caseId = props.caseId
  const boundSnapshot = snapshotId.value
  if (!caseId || !boundSnapshot) return
  const version = viewVersion
  const request = ++runsRequest
  loading.value = !append
  try {
    const offset = append ? nextRunOffset.value ?? 0 : 0
    const response = await apiRequest<CaseRunsResult>(
      `/cases/${caseId}/runs?snapshot_id=${encodeURIComponent(boundSnapshot)}&offset=${offset}`,
      accessToken,
    )
    if (version !== viewVersion || request !== runsRequest ||
        snapshotId.value !== boundSnapshot) return
    runs.value = append ? [...runs.value, ...response.runs] : response.runs
    nextRunOffset.value = response.next_offset
    if (!preserve || !runs.value.some(item => item.id === selectedRunId.value)) {
      selectedRunId.value = runs.value[0]?.id ?? null
    }
    if (selectedRunId.value) await loadRun(selectedRunId.value, version)
    else { checks.value = null; facts.value = null; events.value = []; correction.value = null }
  } catch (reason) {
    if (version === viewVersion && request === runsRequest) error.value = messageFor(reason)
  } finally {
    if (version === viewVersion && request === runsRequest) loading.value = false
  }
}

async function selectRun(runId: string) {
  selectedRunId.value = runId
  checks.value = null
  facts.value = null
  events.value = []
  correction.value = null
  await loadRun(runId)
}

async function conversationId(caseId: string, boundSnapshot: string): Promise<string> {
  const preferred = selectedRun.value ?? runs.value[0]
  if (preferred?.snapshot_id === boundSnapshot) {
    return preferred.conversation_id
  }
  const created = await apiRequest<{ id: string }>(
    `/cases/${caseId}/conversations`, accessToken, { method: 'POST' },
  )
  return created.id
}

async function submitQuestion(fullReview = false) {
  const caseId = props.caseId
  const boundSnapshot = props.documents?.snapshot_id
  const revision = props.documents?.revision
  if (!caseId || !boundSnapshot || !revision || busy.value || isHistory.value || !props.canWrite) return
  const version = viewVersion
  const requestedMode = fullReview ? 'thorough' : mode.value
  const content = fullReview
    ? '请完整审查全部供应商的八项指标，并列出每家每项结果与来源'
    : question.value.trim()
  if (!content) { error.value = '请输入问题。'; return }
  error.value = ''
  busy.value = true
  try {
    const id = await conversationId(caseId, boundSnapshot)
    const accepted = await apiRequest<{ run_id: string }>(
      `/conversations/${id}/messages`, accessToken, {
        method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ content, mode: requestedMode,
          expected_revision: revision }),
      },
    )
    if (version !== viewVersion) return
    question.value = ''
    await loadRuns()
    if (runs.value.some(item => item.id === accepted.run_id)) await selectRun(accepted.run_id)
  } catch (reason) { if (version === viewVersion) error.value = messageFor(reason) }
  finally { if (version === viewVersion) busy.value = false }
}

async function act(kind: 'cancel' | 'retry' | 'resume') {
  const run = selectedRun.value
  if (!run || !props.documents || busy.value || isHistory.value || !props.canWrite) return
  const version = viewVersion
  if (kind === 'resume' && !clarification.value.trim()) {
    error.value = '请输入澄清答复。'; return
  }
  busy.value = true
  error.value = ''
  try {
    await apiRequest(`/agent-runs/${run.id}/${kind}`, accessToken, {
      method: 'POST',
      headers: kind === 'cancel' ? {} : { 'Idempotency-Key': crypto.randomUUID() },
      ...(kind === 'cancel' ? {} : { body: JSON.stringify({
        expected_revision: props.documents.revision,
        ...(kind === 'resume' ? { content: clarification.value.trim() } : {}),
      }) }),
    })
    if (version !== viewVersion) return
    clarification.value = ''
    await loadRuns(true)
  } catch (reason) { if (version === viewVersion) error.value = messageFor(reason) }
  finally { if (version === viewVersion) busy.value = false }
}

async function submitCorrection() {
  const caseId = props.caseId
  const run = selectedRun.value
  const fact = selectedFact.value
  if (!caseId || !props.documents || !run || !fact || busy.value || !canCorrect.value) return
  const version = viewVersion
  if (!correctedValue.value || !correctedUnit.value || !correctionReason.value.trim()) {
    error.value = '请填写修正值、单位和原因。'; return
  }
  busy.value = true
  error.value = ''
  try {
    const accepted = await apiRequest<{ correction_id: string }>(
      `/cases/${caseId}/corrections`, accessToken, {
        method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ source_run_id: run.id,
          fact_set_id: fact.factSetId,
          fact_id: fact.fact_id, raw_value: correctedValue.value.trim(),
          raw_unit: correctedUnit.value.trim(), reason: correctionReason.value.trim(),
          expected_revision: props.documents.revision }),
      },
    )
    await apiRequest(`/cases/${caseId}/corrections/${accepted.correction_id}/recheck`,
      accessToken, { method: 'POST' })
    if (version !== viewVersion) return
    const record = await apiRequest<CorrectionRecord>(
      `/cases/${caseId}/corrections/${accepted.correction_id}`, accessToken)
    if (version !== viewVersion) return
    correction.value = record
    emit('changed', { caseId, fromSnapshotId: run.snapshot_id })
  } catch (reason) { if (version === viewVersion) error.value = messageFor(reason) }
  finally { if (version === viewVersion) busy.value = false }
}

watch(selectedFactKey, () => {
  correctedValue.value = selectedFact.value?.raw_value ?? ''
  correctedUnit.value = selectedFact.value?.raw_unit ?? ''
})
watch([() => props.caseId, snapshotId], () => {
  viewVersion += 1
  runs.value = []; nextRunOffset.value = null; selectedRunId.value = null
  checks.value = null; facts.value = null; events.value = []; correction.value = null
  error.value = ''
  busy.value = false; exporting.value = false
  void loadRuns()
}, { immediate: true })
timer = setInterval(() => {
  if (selectedRun.value && ['queued', 'running'].includes(selectedRun.value.status) && !busy.value) {
    void loadRuns(true)
  }
}, 2500)
onUnmounted(() => { if (timer) clearInterval(timer); viewVersion += 1 })
</script>

<template>
  <section class="content-card agent-card" aria-labelledby="agent-title">
    <div class="section-head">
      <div><span class="eyebrow">02 / Agent 审查</span><h2 id="agent-title">问答与核验</h2></div>
      <span v-if="shownSnapshot" class="revision-tag">版本 {{ shownSnapshot.revision }}</span>
    </div>
    <p v-if="isHistory" class="history-notice">正在查看历史快照。此处的结果仅供追溯，不能作为当前正式结论。</p>
    <p v-if="!caseId || !shownSnapshot?.snapshot_id" class="state-note">请选择 Case 并上传资料后开始审查。</p>
    <template v-else>
      <div v-if="!isHistory && canWrite" class="agent-compose">
        <label for="agent-question">向 Agent 提问</label>
        <textarea id="agent-question" v-model="question" class="field-control" rows="3"
                  maxlength="2000" placeholder="例如：供应商 A 的内存是否达标？" :disabled="busy" />
        <div class="compose-actions">
          <label>模式 <select v-model="mode" class="field-control" :disabled="busy">
            <option value="standard">标准</option><option value="thorough">深入</option>
          </select></label>
          <button type="button" class="button button-primary" :disabled="busy" @click="submitQuestion()">发送问题</button>
          <button type="button" class="button button-secondary" :disabled="busy" @click="submitQuestion(true)">完整审查八项</button>
        </div>
        <p class="privacy-note">问题及必要的资料片段会发送到已配置模型服务，可能产生 API 费用；原件仍按 Case 权限保存。</p>
      </div>
      <div v-if="error" class="alert" role="alert"><span>{{ error }}</span><button class="text-button" type="button" @click="error = ''">关闭</button></div>
      <div class="run-area">
        <div class="run-list">
          <div class="run-list-title"><h3>运行记录</h3><button class="text-button" type="button" @click="loadRuns(true)">刷新</button></div>
          <div v-if="loading && !runs.length" class="loading-state" role="status">正在读取运行记录…</div>
          <p v-else-if="!runs.length" class="sidebar-hint">此快照还没有 Agent 运行记录。</p>
          <button v-for="run in runs" :key="run.id" type="button" class="run-item"
                  :class="{ active: selectedRunId === run.id }" @click="selectRun(run.id)">
            <strong>{{ run.question }}</strong>
            <span>{{ statusNames[run.status] ?? run.status }} · {{ run.mode === 'thorough' ? '深入' : '标准' }} · v{{ run.revision }}</span>
          </button>
          <button v-if="nextRunOffset !== null" class="text-button" type="button" @click="loadRuns(true, true)">加载更早运行</button>
        </div>
        <div class="run-detail" aria-live="polite">
          <template v-if="selectedRun">
            <div class="run-detail-head"><h3>本次答复</h3><span class="status-pill" :class="`status-${selectedRun.status}`">{{ statusNames[selectedRun.status] ?? selectedRun.status }}</span></div>
            <p v-if="selectedRun.status !== 'completed' || !checks?.is_current" class="state-note warning">
              {{ !checks?.is_current ? '历史快照：不作为当前正式结论。' : '本次尚未完成，结果仅供查看，不作为正式结论。' }}
            </p>
            <p class="answer-text">{{ selectedRun.answer_text || (['queued', 'running'].includes(selectedRun.status) ? 'Agent 正在处理。' : '本次没有生成答复。') }}</p>
            <p v-if="selectedRun.error_code" class="error-code">状态原因：{{ selectedRun.error_code }}</p>
            <div v-if="!isHistory && canWrite" class="run-actions">
              <button v-if="['queued', 'running', 'waiting_user', 'interrupted'].includes(selectedRun.status)" type="button" class="button button-secondary" :disabled="busy" @click="act('cancel')">取消</button>
              <button v-if="['partial', 'failed', 'interrupted'].includes(selectedRun.status)" type="button" class="button button-secondary" :disabled="busy" @click="act('retry')">重试</button>
              <form v-if="selectedRun.status === 'waiting_user'" class="clarification-form" @submit.prevent="act('resume')">
                <label for="clarification-answer">澄清答复</label>
                <textarea id="clarification-answer" v-model="clarification" class="field-control" rows="2" maxlength="2000" :disabled="busy" />
                <button class="button button-primary" type="submit" :disabled="busy">继续运行</button>
              </form>
            </div>
            <details v-if="events.length" class="event-log"><summary>运行进度与事件（{{ events.length }}）</summary>
              <ol><li v-for="event in events.slice(-8)" :key="event.seq">{{ event.kind }} · {{ event.status }}</li></ol>
            </details>
          </template>
          <p v-else class="sidebar-hint">选择一次运行，查看答复、核验结果与证据。</p>
        </div>
      </div>

      <div class="matrix-section"><div class="section-head"><div><span class="eyebrow">03 / 结构化结果</span><h2>八字段核验矩阵</h2></div>
        <div v-if="selectedRun && checks?.checks.length" class="export-actions">
          <button data-test="export-json" class="button button-secondary" type="button" :disabled="exporting" @click="exportRun('json')">导出 JSON</button>
          <button data-test="export-xlsx" class="button button-secondary" type="button" :disabled="exporting" @click="exportRun('xlsx')">导出 XLSX</button>
        </div></div>
        <p class="section-intro">矩阵直接读取同一 Run 快照的 CheckResult。只有当前快照且运行与字段均完成的格子标为正式。</p>
        <p v-if="checks?.checks.length && (!checks.is_current || checks.checks.some(item => !item.is_formal))" class="state-note warning">导出包含非正式结果；文件保留每项的正式标记，不作为当前正式结论。</p>
        <div v-if="!selectedRun || !matrixSuppliers.length" class="empty-state compact"><strong>暂无核验结果</strong><span>发起完整审查后，这里将显示各供应商的八项结果。</span></div>
        <div v-else class="matrix-scroll"><table class="matrix-table"><thead><tr><th scope="col">指标</th><th v-for="supplier in matrixSuppliers" :key="supplier" scope="col">{{ supplier }}</th></tr></thead>
          <tbody><tr v-for="field in fields" :key="field.key"><th scope="row">{{ field.label }}</th>
            <td v-for="supplier in matrixSuppliers" :key="supplier"><template v-if="checkFor(supplier, field.key)">
              <span class="matrix-status" :class="{ provisional: !checkFor(supplier, field.key)?.is_formal }">
                {{ checkFor(supplier, field.key)?.business_status ?? '处理未完成' }} · {{ checkFor(supplier, field.key)?.is_formal ? '正式' : '非正式' }}
              </span>
              <small>{{ reasonNames[checkFor(supplier, field.key)?.reason_code ?? ''] ?? checkFor(supplier, field.key)?.reason_code }}</small>
              <span class="evidence-links"><button v-for="refId in checkFor(supplier, field.key)?.evidence_refs.slice(0, 4)" :key="refId" type="button" class="text-button" @click="openEvidence(refId)">查看来源</button>
                <details v-if="(checkFor(supplier, field.key)?.evidence_refs.length ?? 0) > 4" class="extra-evidence">
                  <summary>展开全部 {{ checkFor(supplier, field.key)?.evidence_refs.length }} 处来源</summary>
                  <button v-for="refId in checkFor(supplier, field.key)?.evidence_refs" :key="refId" type="button" class="text-button" @click="openEvidence(refId)">来源 {{ refId.slice(0, 8) }}</button>
                </details></span>
            </template><span v-else class="matrix-empty">尚无结果</span></td>
          </tr></tbody></table></div>
      </div>

      <div v-if="selectedRun && (facts?.available || correction)" class="correction-section"><div class="section-head"><div><span class="eyebrow">04 / 人工复核</span><h2>提取事实修正</h2></div></div>
        <p class="section-intro">只可修改原文支持的数值与单位。提交后会生成新快照并重新复核，原始事实与原件保留。</p>
        <div v-if="correction" class="review-card">
          <strong>复核：{{ correction.review_status === 'needs_review' ? '需人工复核' : correction.review_status === 'complete' ? '完成' : '处理中' }}</strong>
          <span>v{{ correction.revision - 1 }} → v{{ correction.revision }} · {{ correction.old_fact.raw_value }} {{ correction.old_fact.raw_unit }} → {{ correction.corrected_candidate.raw_value }} {{ correction.corrected_candidate.raw_unit }}</span>
          <p v-if="correction.review_status === 'needs_review'">旧值与修正值均有原文支持，保留冲突并等待人工复核。</p>
        </div>
        <form v-else-if="canCorrect" class="correction-form" @submit.prevent="submitCorrection">
          <label>选择提取事实<select v-model="selectedFactKey" class="field-control"><option value="">请选择</option>
            <option v-for="fact in allFacts" :key="fact.key" :value="fact.key">{{ fact.supplierHandle }} · {{ fields.find(item => item.key === fact.fieldKey)?.label }} · {{ fact.role }} · {{ fact.raw_value }} {{ fact.raw_unit }}</option>
          </select></label>
          <div v-if="selectedFact" class="fact-source"><button v-for="refId in selectedFact.evidence_refs" :key="refId" type="button" class="text-button" @click="openEvidence(refId)">核对原文来源</button></div>
          <div class="correction-grid"><label>修正数值<input v-model="correctedValue" class="field-control" inputmode="decimal" pattern="[0-9]+(\.[0-9]+)?" maxlength="20" required /></label>
            <label>单位<input v-model="correctedUnit" class="field-control" maxlength="30" required /></label></div>
          <label>修正原因<textarea v-model="correctionReason" class="field-control" rows="2" maxlength="2000" required /></label>
          <button class="button button-secondary" type="submit" :disabled="busy || !selectedFact">提交事实修正并复核</button>
        </form>
        <p v-else-if="facts?.available" class="state-note">此运行属于历史快照或当前权限为只读，无法提交事实修正。</p>
      </div>
    </template>
  </section>
</template>
