<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { accessToken } from '../auth'
import { apiRequest, downloadRunExport, submitDynamicFactCorrection } from '../api'
import type {
  AnyRunChecksResult, DynamicCorrectionAuditRecord, DynamicFactsResult, DynamicProgressResult,
  RunDynamicChecksResult,
} from '../api'

const props = defineProps<{
  caseId: string
  runId: string
  snapshotId: string
  revision: number
  canWrite: boolean
  supplierNames: Record<string, string>
}>()
const emit = defineEmits<{
  openEvidence: [target: { caseId: string; snapshotId: string; evidenceId: string }]
}>()

const checks = ref<RunDynamicChecksResult | null>(null)
const facts = ref<DynamicFactsResult | null>(null)
const progress = ref<DynamicProgressResult | null>(null)
const corrections = ref<DynamicCorrectionAuditRecord[]>([])
const detailSupplier = ref('')
const loading = ref(true)
const busy = ref(false)
const exporting = ref(false)
const error = ref('')
const selectedFactKey = ref('')
const correctedValue = ref('')
const correctedUnit = ref('')
const correctionReason = ref('')
let scope = 0

const statusNames: Record<string, string> = {
  queued: '排队中', running: '正在处理', waiting_user: '等待补充信息', completed: '已完成',
  partial: '部分完成', failed: '处理失败', cancelled: '已停止', interrupted: '处理已中断',
}
const reasonNames: Record<string, string> = {
  threshold_met: '符合要求阈值', threshold_failed: '不符合要求阈值',
  conflicting_supplier_values: '同一口径的供应商资料相互冲突',
  supplier_fact_missing: '缺少适用的供应商证据',
  coverage_incomplete: '资料处理尚未完成',
  semantics_unclear: '单位、限定语或计价口径待确认',
  requirement_manual: '复杂条件需人工复核',
  item_model_unclear: '品目/规格对应关系待确认',
  duration_basis_mismatch: '期限口径与要求不一致',
  money_basis_mismatch: '计价口径与要求不一致',
  unit_basis_mismatch: '单位口径与要求不一致',
}
const errorTexts: Record<string, string> = {
  run_changed: '运行结果已更新，请关闭面板后重新打开查看最新状态。',
  facts_unavailable: '本次运行没有可修正的事实集。',
  not_found: '没有找到该运行或无权访问。',
  forbidden: '当前账号无权查看该报告。',
  session_expired: '登录已过期，请重新登录。',
  case_deleting: '该采购任务正在删除。',
}

function friendly(reason: unknown): string {
  const code = reason instanceof Error ? reason.message : ''
  return errorTexts[code] ?? '暂时无法读取审查报告，请稍后重试。'
}
function supplierLabel(handle: string): string {
  return props.supplierNames[handle] ?? handle
}
const suppliers = computed(() => [...new Set(
  (checks.value?.checks ?? []).map(item => item.supplier))])
const itemGroups = computed(() => {
  const groups: { itemId: string; itemName: string; criteria: string[] }[] = []
  const index = new Map<string, { itemId: string; itemName: string; criteria: string[] }>()
  for (const check of checks.value?.checks ?? []) {
    let group = index.get(check.item_id)
    if (!group) {
      group = { itemId: check.item_id, itemName: check.item_name, criteria: [] }
      index.set(check.item_id, group); groups.push(group)
    }
    if (!group.criteria.includes(check.criterion_id)) group.criteria.push(check.criterion_id)
  }
  return groups
})
function checkFor(supplier: string, criterionId: string) {
  return checks.value?.checks.find(item => item.supplier === supplier
    && item.criterion_id === criterionId)
}
function statusText(check: { processing_status: string; business_status: string | null }): string {
  return check.business_status ?? '处理未完成'
}
function statusClass(check: { processing_status: string; business_status: string | null }): string {
  const status = check.business_status
  if (!status) return 'status-incomplete'
  if (status === '满足') return 'status-pass'
  if (status === '不满足') return 'status-fail'
  if (status === '资料冲突') return 'status-conflict'
  if (status === '证据不足') return 'status-insufficient'
  return 'status-manual'
}
function reasonText(check: { reason_code: string }): string {
  return reasonNames[check.reason_code] ?? check.reason_code
}
function openEvidence(evidenceId: string) {
  emit('openEvidence', { caseId: props.caseId, snapshotId: props.snapshotId, evidenceId })
}

const allFacts = computed(() => (facts.value?.fact_sets ?? []).flatMap(set =>
  set.facts.map(fact => ({ ...fact, factSetId: set.fact_set_id,
    criterionName: fact.criterion_name
      ?? checks.value?.checks.find(item => item.criterion_id === fact.criterion_id)?.criterion_name
      ?? fact.criterion_id,
    key: `${set.fact_set_id}/${fact.fact_id}` }))))
const selectedFact = computed(() => allFacts.value.find(item => item.key === selectedFactKey.value))
const canCorrect = computed(() => props.canWrite && facts.value?.available &&
  checks.value?.is_current && !busy.value)

async function load() {
  const ticket = ++scope
  const { runId } = props
  loading.value = true; error.value = ''
  try {
    const checksResult = await apiRequest<AnyRunChecksResult>(`/agent-runs/${runId}/checks`, accessToken)
    if (ticket !== scope) return
    checks.value = checksResult as RunDynamicChecksResult
  } catch (reason) {
    if (ticket === scope) { error.value = friendly(reason); loading.value = false }
    return
  }
  const [progressResult, factsResult, correctionResult] = await Promise.allSettled([
    apiRequest<DynamicProgressResult>(`/agent-runs/${runId}/progress`, accessToken),
    apiRequest<DynamicFactsResult>(`/agent-runs/${runId}/facts`, accessToken),
    apiRequest<{ run_id: string; corrections: DynamicCorrectionAuditRecord[] }>(
      `/agent-runs/${runId}/fact-corrections`, accessToken),
  ])
  if (ticket !== scope) return
  if (progressResult.status === 'fulfilled') progress.value = progressResult.value
  if (factsResult.status === 'fulfilled') facts.value = factsResult.value
  if (correctionResult.status === 'fulfilled') corrections.value = correctionResult.value.corrections
  loading.value = false
}

async function exportRun(format: 'json' | 'xlsx') {
  if (!checks.value?.checks.length || exporting.value) return
  exporting.value = true; error.value = ''
  try {
    const blob = await downloadRunExport(props.runId, format, accessToken)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `docucheck-${props.runId}.${format}`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (reason) { error.value = friendly(reason) }
  finally { exporting.value = false }
}

watch(selectedFactKey, () => {
  correctedValue.value = selectedFact.value?.raw_value ?? ''
  correctedUnit.value = selectedFact.value?.raw_unit ?? ''
})

async function submitCorrection() {
  const fact = selectedFact.value
  if (!fact || !canCorrect.value) return
  if (!correctedValue.value.trim() || !correctedUnit.value.trim() || !correctionReason.value.trim()) {
    error.value = '请填写修正值、单位和原因。'; return
  }
  busy.value = true; error.value = ''
  try {
    const record = await submitDynamicFactCorrection(props.runId, {
      fact_id: fact.fact_id,
      candidate: {
        criterion_id: String(fact.criterion_id ?? ''),
        item_id: String(fact.item_id ?? ''),
        supplier: String(fact.supplier ?? ''),
        value: correctedValue.value.trim(),
        unit: correctedUnit.value.trim(),
        evidence_refs: fact.evidence_refs,
        quote: fact.quote,
        currency: fact.currency ?? null,
        per_unit: fact.per_unit ?? null,
        tax_basis: fact.tax_basis ?? null,
        calendar: fact.calendar ?? null,
        start_event: fact.start_event ?? null,
      },
      reason: correctionReason.value.trim(),
      expected_revision: props.revision,
      expected_payload_digest: facts.value?.payload_digest ?? '',
    }, accessToken)
    corrections.value = [...corrections.value, {
      correction_id: record.correction_id,
      reason: correctionReason.value.trim(),
      old_fact: { ...fact, criterion_name: fact.criterionName },
      corrected_fact: { ...fact, criterion_name: fact.criterionName,
        value: correctedValue.value.trim(), unit: correctedUnit.value.trim(),
        raw_value: correctedValue.value.trim(), raw_unit: correctedUnit.value.trim() },
      review_status: record.review_status,
    }]
    correctionReason.value = ''
  } catch (reason) { error.value = friendly(reason) }
  finally { busy.value = false }
}

watch([() => props.runId, () => props.caseId], () => { void load() }, { immediate: true })
</script>

<template>
  <section class="review-panel" aria-label="采购审查报告">
    <div class="panel-state-row">
      <span v-if="checks" class="state-pill">{{ statusNames[checks.run_status] ?? checks.run_status }}</span>
      <span v-if="checks && !checks.is_current" class="state-pill status-historical">历史结果</span>
      <span class="panel-revision" v-if="checks">资料版本 v{{ checks.revision }}</span>
    </div>
    <p v-if="checks && !checks.is_current" class="state-note warning">
      这份报告属于历史资料版本（v{{ checks.revision }}），仅供追溯，不作为当前正式结论。
    </p>
    <p v-if="checks && ['partial', 'queued', 'running', 'waiting_user'].includes(checks.run_status)"
       class="state-note warning">本次处理尚未完成，未完成部分不会算作业务结论。</p>
    <p v-if="progress && progress.planned_batches" class="state-note">
      处理批次：已核验 {{ progress.validated_batches }} / {{ progress.planned_batches }}
    </p>
    <div v-if="error" class="chat-error" role="alert">{{ error }} <button class="text-button" @click="load">重试读取</button></div>
    <div v-if="loading" class="chat-loading" role="status">正在读取审查报告…</div>
    <template v-else-if="checks">
      <div class="review-toolbar">
        <label v-if="suppliers.length > 1" class="detail-toggle">供应商明细
          <select v-model="detailSupplier" class="field-control" aria-label="选择供应商明细">
            <option value="">全部供应商</option>
            <option v-for="supplier in suppliers" :key="supplier" :value="supplier">{{ supplierLabel(supplier) }}</option>
          </select>
        </label>
        <button class="button button-secondary" data-test="export-xlsx" :disabled="exporting || !checks.checks.length"
                @click="exportRun('xlsx')">导出 Excel</button>
        <details class="audit-menu">
          <summary>更多</summary>
          <button class="text-button" data-test="export-json" :disabled="exporting" @click="exportRun('json')">导出审计 JSON</button>
        </details>
      </div>

      <div v-if="!checks.checks.length" class="empty-state compact">
        <strong>暂无审查结果</strong><span>正式审查完成后，这里会显示各供应商的逐项结果。</span>
      </div>
      <div v-else class="matrix-scroll review-matrix">
        <table class="matrix-table">
          <thead><tr>
            <th scope="col">指标</th>
            <th v-for="supplier in (detailSupplier ? [detailSupplier] : suppliers)" :key="supplier" scope="col">{{ supplierLabel(supplier) }}</th>
          </tr></thead>
          <tbody>
            <template v-for="group in itemGroups" :key="group.itemId">
              <tr class="matrix-group"><th :colspan="(detailSupplier ? 1 : suppliers.length) + 1">{{ group.itemName }}</th></tr>
              <tr v-for="criterionId in group.criteria" :key="criterionId">
                <th scope="row">{{ checks!.checks.find(item => item.criterion_id === criterionId)?.criterion_name ?? criterionId }}</th>
                <td v-for="supplier in (detailSupplier ? [detailSupplier] : suppliers)" :key="supplier">
                  <template v-if="checkFor(supplier, criterionId)">
                    <span class="matrix-status" :class="[statusClass(checkFor(supplier, criterionId)!),
                      { provisional: !checkFor(supplier, criterionId)!.is_formal }]">
                      {{ statusText(checkFor(supplier, criterionId)!) }} · {{ checkFor(supplier, criterionId)!.is_formal ? '正式' : '非正式' }}
                    </span>
                    <small>{{ reasonText(checkFor(supplier, criterionId)!) }}</small>
                    <span v-if="checkFor(supplier, criterionId)!.evidence_refs.length" class="evidence-links">
                      <button v-for="refId in checkFor(supplier, criterionId)!.evidence_refs.slice(0, 4)"
                              :key="refId" class="text-button" @click="openEvidence(refId)">查看来源</button>
                    </span>
                  </template>
                  <span v-else class="matrix-empty">尚无结果</span>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <div v-if="checks.checks.some(item => !item.is_formal)" class="state-note warning">
        报告包含非正式结果（处理未完成或历史来源）；导出文件保留每项的正式标记。
      </div>

      <div v-if="canCorrect || corrections.length" class="correction-section">
        <h3 class="review-subhead">事实修正与审计</h3>
        <p class="section-intro">只可提交由同一原文支持的事实备选；提交后进入人工复核，不自动改写正式结论。</p>
        <div v-if="corrections.length" class="correction-audit">
          <div v-for="record in corrections" :key="record.correction_id" class="review-card">
            <strong>需人工复核</strong>
            <span v-if="record.old_fact && record.corrected_fact">
              {{ record.old_fact.supplier }} · {{ record.old_fact.criterion_name ?? record.old_fact.criterion_id }}：
              {{ record.old_fact.raw_value ?? record.old_fact.value }} {{ record.old_fact.raw_unit ?? record.old_fact.unit }}
              → {{ record.corrected_fact.raw_value ?? record.corrected_fact.value }} {{ record.corrected_fact.raw_unit ?? record.corrected_fact.unit }}
            </span>
            <p>理由：{{ record.reason }}</p>
          </div>
        </div>
        <form v-if="canCorrect && allFacts.length" class="correction-form" @submit.prevent="submitCorrection">
          <label>选择提取事实<select v-model="selectedFactKey" class="field-control" data-test="fact-select">
            <option value="">请选择</option>
            <option v-for="fact in allFacts" :key="fact.key" :value="fact.key">
              {{ supplierLabel(fact.supplier ?? '') }} · {{ fact.criterionName }} · {{ fact.raw_value }} {{ fact.raw_unit }}
            </option>
          </select></label>
          <div v-if="selectedFact" class="fact-source">
            <button v-for="refId in selectedFact.evidence_refs" :key="refId" type="button" class="text-button"
                    @click="openEvidence(refId)">核对原文来源</button>
          </div>
          <div class="correction-grid">
            <label>修正数值<input v-model="correctedValue" class="field-control" data-test="correction-value" maxlength="500" /></label>
            <label>单位<input v-model="correctedUnit" class="field-control" data-test="correction-unit" maxlength="120" /></label>
          </div>
          <label>修正原因<textarea v-model="correctionReason" class="field-control" data-test="correction-reason" rows="2" maxlength="1000" /></label>
          <button class="button button-secondary" type="submit" data-test="correction-submit" :disabled="busy || !selectedFact">提交事实修正</button>
        </form>
        <p v-else-if="canWrite && !facts?.available" class="state-note">本次运行没有可修正的事实集。</p>
      </div>
      <p v-else-if="!canWrite" class="state-note">当前为只读权限，可查看报告与来源，不能提交修正。</p>
    </template>
  </section>
</template>
