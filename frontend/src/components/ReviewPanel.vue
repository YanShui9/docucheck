<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { accessToken } from '../auth'
import { apiRequest, downloadRunExport, submitDynamicFactCorrection } from '../api'
import type {
  AnyRunChecksResult, DynamicCorrectionAuditRecord, DynamicCorrectionRecord, DynamicFactsResult, DynamicProgressResult,
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
  correctionSubmitted: [target: { caseId: string; snapshotId: string; runId: string }, completion: Promise<DynamicCorrectionRecord>]
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
const correctedValue = ref<string | boolean>('')
const correctedUnit = ref('')
const correctionReason = ref('')
let scope = 0
let mounted = true
let savedDigest = ''
let savedUnrefreshed = false
function identity() { return JSON.stringify([props.caseId, props.runId, props.snapshotId, props.revision, props.canWrite]) }
function active(ticket: number, context: string) { return mounted && ticket === scope && context === identity() }

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
  run_changed: '运行结果已更新，请重试读取最新状态。',
  unsupported_correction: '修正值必须由所引用的同一原文支持，请核对来源后重试。',
  correction_unchanged: '修正值与现有事实相同，无需重复提交。',
  case_busy: '当前采购任务正在处理，请等待完成后重试读取。',
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
  set.facts.map(fact => ({ ...fact, factSetId: set.fact_set_id, rule: set.rule,
    criterionName: fact.criterion_name
      ?? checks.value?.checks.find(item => item.criterion_id === fact.criterion_id)?.criterion_name
      ?? fact.criterion_id,
    key: `${set.fact_set_id}/${fact.fact_id}` }))))
const selectedFact = computed(() => allFacts.value.find(item => item.key === selectedFactKey.value))
const canCorrect = computed(() => props.canWrite && !loading.value && facts.value?.available &&
  facts.value.is_current && checks.value?.is_current && checks.value.revision === props.revision &&
  ['completed', 'partial'].includes(checks.value.run_status))
const correctionType = computed(() => selectedFact.value?.rule?.type)
const supportedCorrection = computed(() => ['numeric', 'money', 'duration', 'enum', 'presence', 'manual']
  .includes(correctionType.value ?? ''))
const hasUnit = computed(() => ['numeric', 'money', 'duration'].includes(correctionType.value ?? ''))

function clearReport() {
  checks.value = null; facts.value = null; progress.value = null; corrections.value = []
  detailSupplier.value = ''; selectedFactKey.value = ''
  correctedValue.value = ''; correctedUnit.value = ''; correctionReason.value = ''
}

async function load() {
  const ticket = ++scope
  const context = identity()
  const { runId, caseId, snapshotId } = props
  const expectedDigest = savedDigest
  savedDigest = ''
  clearReport()
  loading.value = true; busy.value = false; exporting.value = false; error.value = ''
  try {
    const [checksResult, progressResult, factsResult, auditResult] = await Promise.all([
      apiRequest<AnyRunChecksResult>(`/agent-runs/${runId}/checks`, accessToken),
      apiRequest<DynamicProgressResult>(`/agent-runs/${runId}/progress`, accessToken),
      apiRequest<DynamicFactsResult>(`/agent-runs/${runId}/facts`, accessToken),
      apiRequest<{ run_id: string; payload_digest: string | null; corrections: DynamicCorrectionAuditRecord[] }>(
        `/agent-runs/${runId}/fact-corrections`, accessToken),
    ])
    if (!active(ticket, context)) return
    const report = checksResult as RunDynamicChecksResult
    if ([report, factsResult].some(result => result.run_id !== runId || result.case_id !== caseId
        || result.snapshot_id !== snapshotId)
      || progressResult.run_id !== runId || auditResult.run_id !== runId
      || factsResult.revision !== report.revision || factsResult.is_current !== report.is_current
      || factsResult.checklist_version_id !== report.checklist_version_id
      || progressResult.checklist_version_id !== report.checklist_version_id
      || progressResult.run_status !== report.run_status
      || (factsResult.available && !report.payload_digest) || factsResult.payload_digest !== report.payload_digest
      || auditResult.payload_digest !== report.payload_digest
      || (expectedDigest && expectedDigest !== report.payload_digest)) throw new Error('run_changed')
    checks.value = report; facts.value = factsResult; progress.value = progressResult
    corrections.value = auditResult.corrections
    savedUnrefreshed = false
  } catch (reason) {
    if (active(ticket, context)) error.value = (savedUnrefreshed
      ? '修正已保存，但最新报告读取失败。请重试读取，避免重复提交。' : '') + friendly(reason)
  } finally {
    if (active(ticket, context)) loading.value = false
  }
}

async function exportRun(format: 'json' | 'xlsx') {
  if (!checks.value?.checks.length || exporting.value || busy.value || loading.value) return
  const ticket = scope, context = identity(), runId = props.runId
  exporting.value = true; error.value = ''
  try {
    const blob = await downloadRunExport(runId, format, accessToken)
    if (!active(ticket, context)) return
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `docucheck-${runId}.${format}`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (reason) { if (active(ticket, context)) error.value = friendly(reason) }
  finally { if (active(ticket, context)) exporting.value = false }
}

watch(selectedFactKey, () => {
  correctedValue.value = selectedFact.value?.raw_value ?? ''
  correctedUnit.value = selectedFact.value?.raw_unit ?? ''
})

async function submitCorrection() {
  const fact = selectedFact.value
  if (!fact || !canCorrect.value || !supportedCorrection.value || busy.value || exporting.value) return
  const value = typeof correctedValue.value === 'boolean' ? correctedValue.value : correctedValue.value.trim()
  const unit = hasUnit.value || correctionType.value === 'manual' ? correctedUnit.value.trim() : ''
  if ((correctionType.value === 'presence' ? typeof value !== 'boolean' : typeof value !== 'string' || !value)
    || (hasUnit.value && !unit) || !correctionReason.value.trim()) {
    error.value = '请填写有效的修正值、必要单位和原因。'; return
  }
  const ticket = scope, context = identity(), runId = props.runId
  busy.value = true; error.value = ''
  try {
    const completion = submitDynamicFactCorrection(runId, {
      fact_id: fact.fact_id,
      candidate: {
        criterion_id: String(fact.criterion_id ?? ''),
        item_id: String(fact.item_id ?? ''),
        supplier: String(fact.supplier ?? ''),
        value,
        unit,
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
    emit('correctionSubmitted', { caseId: props.caseId, snapshotId: props.snapshotId, runId }, completion)
    const record = await completion
    if (!active(ticket, context)) return
    savedDigest = record.payload_digest
    savedUnrefreshed = true
    if (record.run_id !== runId || !savedDigest) throw new Error('run_changed')
    await load()
  } catch (reason) {
    if (active(ticket, context)) {
      clearReport()
      error.value = friendly(reason) + '请先重试读取最新事实；网络异常时提交可能已保存。'
    }
  } finally { if (active(ticket, context)) busy.value = false }
}

watch(identity, () => { savedDigest = ''; savedUnrefreshed = false; void load() }, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { mounted = false; ++scope })
</script>

<template>
  <section class="review-panel" aria-label="采购审查报告">
    <div class="panel-state-row">
      <span v-if="checks" class="state-pill">{{ statusNames[checks.run_status] ?? checks.run_status }}</span>
      <span v-if="checks && !checks.is_current" class="state-pill status-historical">历史结果</span>
      <span class="panel-revision" v-if="checks">资料版本 v{{ checks.revision }}</span>
    </div>
    <p v-if="checks && !checks.is_current" class="state-note warning">
      这份报告的资料、审查清单或品目对应已变化，仅供追溯，不作为当前正式结论。
    </p>
    <p v-if="checks && ['partial', 'queued', 'running', 'waiting_user'].includes(checks.run_status)"
       class="state-note warning">本次处理尚未完成，未完成部分不会算作业务结论。</p>
    <p v-if="progress && progress.planned_batches" class="state-note">
      处理批次：已核验 {{ progress.validated_batches }} / {{ progress.planned_batches }}
    </p>
    <div v-if="error" class="chat-error" role="alert">{{ error }} <button class="text-button" @click="load()">重试读取</button></div>
    <div v-if="loading" class="chat-loading" role="status">正在读取审查报告…</div>
    <template v-else-if="checks">
      <div class="review-toolbar">
        <label v-if="suppliers.length > 1" class="detail-toggle">供应商明细
          <select v-model="detailSupplier" class="field-control" aria-label="选择供应商明细">
            <option value="">全部供应商</option>
            <option v-for="supplier in suppliers" :key="supplier" :value="supplier">{{ supplierLabel(supplier) }}</option>
          </select>
        </label>
        <button class="button button-secondary" data-test="export-xlsx" :disabled="exporting || busy || !checks.checks.length"
                @click="exportRun('xlsx')">导出 Excel</button>
        <details class="audit-menu">
          <summary>更多</summary>
          <button class="text-button" data-test="export-json" :disabled="exporting || busy" @click="exportRun('json')">导出报告 JSON</button>
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
        报告包含非正式结果（处理未完成、历史来源或事实修正待复核）；导出文件保留每项的正式标记。
      </div>

      <div v-if="canCorrect || corrections.length" class="correction-section">
        <h3 class="review-subhead">事实修正与审计</h3>
        <p class="section-intro">只可提交由同一原文支持的事实备选；提交后进入人工复核，不自动改写正式结论。</p>
        <div v-if="corrections.length" class="correction-audit">
          <div v-for="record in corrections" :key="record.correction_id" class="review-card">
            <strong>需人工复核</strong>
            <span v-if="record.corrected_fact">
              {{ supplierLabel(record.corrected_fact.supplier ?? '') }} · {{ record.corrected_fact.criterion_name ?? record.corrected_fact.criterion_id }}：
              <template v-if="record.old_fact">{{ record.old_fact.raw_value ?? record.old_fact.value }} {{ record.old_fact.raw_unit ?? record.old_fact.unit }}</template>
              <template v-else>历史记录未保存唯一的旧事实，无法确认旧值</template>
              → {{ record.corrected_fact.raw_value ?? record.corrected_fact.value }} {{ record.corrected_fact.raw_unit ?? record.corrected_fact.unit }}
            </span>
            <p>理由：{{ record.reason }}</p>
          </div>
        </div>
        <form v-if="canCorrect && allFacts.length" class="correction-form" @submit.prevent="submitCorrection">
          <label>选择提取事实<select v-model="selectedFactKey" class="field-control" data-test="fact-select" :disabled="busy || exporting">
            <option value="">请选择</option>
            <option v-for="fact in allFacts" :key="fact.key" :value="fact.key">
              {{ supplierLabel(fact.supplier ?? '') }} · {{ fact.criterionName }} · {{ typeof fact.raw_value === 'boolean' ? (fact.raw_value ? '有' : '无') : fact.raw_value }} {{ fact.raw_unit }}
            </option>
          </select></label>
          <div v-if="selectedFact" class="fact-source">
            <button v-for="refId in selectedFact.evidence_refs" :key="refId" type="button" class="text-button"
                    @click="openEvidence(refId)">核对原文来源</button>
          </div>
          <div v-if="supportedCorrection" class="correction-grid">
            <label v-if="correctionType === 'presence'">修正为<select v-model="correctedValue" class="field-control" data-test="correction-value" :disabled="busy || exporting">
              <option :value="true">有（原文明确提供）</option><option :value="false">无（原文明确未提供）</option>
            </select></label>
            <label v-else-if="correctionType === 'enum'">原文中的选项值<input v-model="correctedValue" class="field-control" data-test="correction-value" maxlength="500" :disabled="busy || exporting" /></label>
            <label v-else>{{ correctionType === 'manual' ? '修正内容' : '修正数值' }}<input v-model="correctedValue" class="field-control" data-test="correction-value" maxlength="500" :disabled="busy || exporting" /></label>
            <label v-if="hasUnit || correctionType === 'manual'">{{ hasUnit ? '单位' : '单位（选填）' }}<input v-model="correctedUnit" class="field-control" data-test="correction-unit" maxlength="120" :disabled="busy || exporting" /></label>
          </div>
          <p v-if="correctionType === 'enum'" class="state-note">填写原文中的实际选项；允许填写要求范围以外的值，由后端核验是否符合要求。</p>
          <p v-if="correctionType === 'manual'" class="state-note">只修正原文提取内容，复杂规则仍需人工判断。</p>
          <p v-if="selectedFact && !supportedCorrection" class="state-note">缺少已确认的规则类型，暂不支持在此修正事实。</p>
          <label>修正原因<textarea v-model="correctionReason" class="field-control" data-test="correction-reason" rows="2" maxlength="1000" :disabled="busy || exporting" /></label>
          <button class="button button-secondary" type="submit" data-test="correction-submit" :disabled="busy || exporting || !supportedCorrection">{{ busy ? '正在保存并刷新…' : '提交事实修正' }}</button>
        </form>
        <p v-else-if="canWrite && !facts?.available" class="state-note">本次运行没有可修正的事实集。</p>
      </div>
      <p v-else-if="!canWrite" class="state-note">当前为只读权限，可查看报告与来源，不能提交修正。</p>
    </template>
  </section>
</template>
