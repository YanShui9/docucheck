<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { accessToken } from '../auth'
import { confirmChecklist, fetchChecklist, fetchChecklistVersion, saveChecklistDraft } from '../api'
import type {
  ChecklistCriterion, ChecklistDefinitionPayload, ChecklistRule, ChecklistView,
  CriterionChange, RuleType,
} from '../api'

const props = defineProps<{
  caseId: string
  snapshotId: string
  revision: number
  canWrite: boolean
  candidate?: { candidate: ChecklistDefinitionPayload; changes: CriterionChange[]; runId: string } | null
}>()
const emit = defineEmits<{
  openEvidence: [target: { caseId: string; snapshotId: string; evidenceId: string }]
  confirmed: []
  saved: []
}>()

const view = ref<ChecklistView | null>(null)
const working = ref<ChecklistDefinitionPayload | null>(null)
const selected = ref<Set<string>>(new Set())
const candidateUnsaved = ref(false)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const editingId = ref<string | null>(null)
const adding = ref(false)
const confirmOpen = ref(false)
const draftReason = ref('手工调整审查要求')
const confirmReason = ref('确认当前审查要求')
const editDraft = ref<ChecklistCriterion | null>(null)
const addDraft = ref<{ itemMode: 'existing' | 'new'; itemId: string; newItemName: string; criterion: ChecklistCriterion } | null>(null)
const editError = ref('')
let scope = 0

const stateLabels: Record<string, string> = {
  empty: '未设置', draft: '草稿', needs_resolution: '待处理', confirmed: '已确认',
  partially_confirmed: '部分已确认', needs_reconfirmation: '需重新确认', historical: '历史版本',
}
const operatorLabels: Record<string, string> = {
  eq: '等于', gte: '至少', lte: '最多', gt: '大于', lt: '小于', between: '区间',
}
const calendarLabels: Record<string, string> = {
  natural_days: '自然日', working_days: '工作日', months: '月',
}
const taxLabels: Record<string, string> = {
  included: '含税', excluded: '不含税', unspecified: '口径未定',
}
const issueLabels: Record<string, string> = {
  duplicate_name: '同一品目下存在重名指标', rule_source_unresolved: '原文来源未能定位',
  tax_basis_missing: '待补充口径',
}
const errorTexts: Record<string, string> = {
  checklist_revision_conflict: '审查要求刚被其他人修改，请刷新后重试。',
  checklist_snapshot_changed: '资料版本已更新，请刷新后重试。',
  checklist_source_scope: '部分指标的原文来源无法在当前资料中定位，请核对接算依据。',
  checklist_source_changed: '依据的原文已变化，请重新核对来源。',
  checklist_capacity: '清单规模超出上限，请精简指标。',
  invalid_checklist: '清单定义未通过校验，请检查各字段。',
  invalid_checklist_selection: '确认的指标选择无效，请刷新后重试。',
  not_found: '没有找到该采购任务或资料版本。',
  forbidden: '当前账号无权修改审查要求。',
  session_expired: '登录已过期，请重新登录。',
  case_deleting: '该采购任务正在删除。',
}

function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T }
function friendly(reason: unknown): string {
  const code = reason instanceof Error ? reason.message : ''
  return errorTexts[code] ?? '暂时无法完成操作，请稍后重试。'
}
const issueById = computed(() => {
  const map = new Map<string, string[]>()
  for (const issue of view.value?.issues ?? []) {
    map.set(issue.criterion_id, [...map.get(issue.criterion_id) ?? [], issueLabels[issue.code] ?? issue.code])
  }
  return map
})
const selectedIds = computed(() => [...selected.value].filter(id =>
  (working.value?.criteria ?? []).some(item => item.criterion_id === id && item.enabled)))
const selectedCriteria = computed(() => (working.value?.criteria ?? [])
  .filter(item => selected.value.has(item.criterion_id) && item.enabled))
const confirmGroupCount = computed(() => new Set(selectedCriteria.value.map(item => item.item_id)).size)

function operatorLabel(rule: ChecklistRule): string {
  return operatorLabels[rule.operator ?? ''] ?? ''
}
function amountText(rule: ChecklistRule): string {
  if (rule.operator === 'between') return `${rule.lower ?? '?'} ~ ${rule.upper ?? '?'}`
  return String(rule.value ?? '?')
}
function ruleSummary(rule: ChecklistRule): string {
  switch (rule.type) {
    case 'numeric': return `${operatorLabel(rule)} ${amountText(rule)} ${rule.unit ?? ''}`.trim()
    case 'money':
      return `${operatorLabel(rule)} ${amountText(rule)} ${rule.currency ?? ''} / ${rule.per_unit ?? ''}（${taxLabels[rule.tax_basis ?? 'unspecified']}）`
    case 'enum': return `${rule.operator === 'in' ? '属于' : '等于'} ${(rule.values ?? []).join('、')}`
    case 'presence': return `${rule.must_be_present === false ? '不得出现' : '须提供'} ${rule.description ?? ''}`
    case 'duration': return `${operatorLabel(rule)} ${amountText(rule)} ${calendarLabels[rule.calendar ?? 'natural_days']}（自 ${rule.start_event ?? '?'} 起）`
    case 'manual': return rule.instruction ?? ''
  }
}
function sourceLabel(criterion: ChecklistCriterion): string {
  return criterion.source.kind === 'document_extracted' ? '需求原文' : '用户设置'
}
function criteriaOf(itemId: string): ChecklistCriterion[] {
  return (working.value?.criteria ?? []).filter(item => item.item_id === itemId)
}
function itemName(itemId: string): string {
  return working.value?.items.find(item => item.item_id === itemId)?.name ?? itemId
}
function contextOf(criterion: ChecklistCriterion) {
  return criterion.source.context ?? null
}

async function load() {
  const ticket = ++scope
  const { caseId, snapshotId } = props
  loading.value = true; error.value = ''; notice.value = ''
  try {
    const response = await fetchChecklist(caseId, snapshotId, accessToken)
    if (ticket !== scope) return
    view.value = response
    if (props.candidate && props.candidate.candidate) {
      working.value = clone(props.candidate.candidate)
      candidateUnsaved.value = true
    } else if (response.state === 'needs_resolution' || response.state === 'draft'
        || response.state === 'partially_confirmed') {
      working.value = response.draft ? clone(response.draft) : null
      candidateUnsaved.value = false
    } else if (response.state === 'confirmed') {
      working.value = response.confirmed_version ? clone(response.confirmed_version.definition) : null
      candidateUnsaved.value = false
    } else {
      working.value = null
      candidateUnsaved.value = false
    }
    reselect()
  } catch (reason) {
    if (ticket === scope) { error.value = friendly(reason); working.value = null }
  } finally { if (ticket === scope) loading.value = false }
}
function reselect() {
  selected.value = new Set((working.value?.criteria ?? [])
    .filter(item => item.enabled).map(item => item.criterion_id))
}
function toggleSelect(criterionId: string, event: Event) {
  const next = new Set(selected.value)
  if ((event.target as HTMLInputElement).checked) next.add(criterionId)
  else next.delete(criterionId)
  selected.value = next
}
async function adoptPrevious() {
  const versionId = view.value?.previous_version_id
  if (!versionId) return
  busy.value = true; error.value = ''
  try {
    const version = await fetchChecklistVersion(props.caseId, versionId, accessToken)
    working.value = clone(version.definition)
    candidateUnsaved.value = false
    reselect()
  } catch (reason) { error.value = friendly(reason) }
  finally { busy.value = false }
}

const numberPattern = /^-?[0-9]{1,12}(?:\.[0-9]{1,6})?$/
function validateRule(rule: ChecklistRule): string | null {
  const numeric = (text: unknown) => typeof text === 'string' && numberPattern.test(text)
  if (rule.type === 'numeric' || rule.type === 'money' || rule.type === 'duration') {
    if (rule.operator === 'between') {
      if (!numeric(rule.lower) || !numeric(rule.upper)) return '区间上下限必须为数字。'
    } else if (!numeric(rule.value)) return '数值格式不正确，请填写数字。'
  }
  if (rule.type === 'numeric' && !rule.unit?.trim()) return '请填写数值单位。'
  if (rule.type === 'money' && !rule.per_unit?.trim()) return '请填写计价单位。'
  if (rule.type === 'enum') {
    const values = rule.values ?? []
    if (!values.length || new Set(values).size !== values.length) return '枚举值不能为空且不能重复。'
    if (values.some(value => !value.trim())) return '枚举值不能为空。'
  }
  if (rule.type === 'presence' && !rule.description?.trim()) return '请填写资料存在性说明。'
  if (rule.type === 'duration' && !rule.start_event?.trim()) return '请填写期限起算事件。'
  if (rule.type === 'manual' && !rule.instruction?.trim()) return '请填写人工复核说明。'
  return null
}
function validateCriterion(criterion: ChecklistCriterion): string | null {
  if (!criterion.name.trim() || criterion.name !== criterion.name.trim()) return '指标名称不能为空且首尾不能有空格。'
  if (!working.value?.items.some(item => item.item_id === criterion.item_id)) return '指标必须归属一个品目。'
  return validateRule(criterion.rule)
}

async function save() {
  if (!props.canWrite || busy.value || !working.value) return
  if (!working.value.items.length || !working.value.criteria.length) {
    error.value = '请至少设置一个品目和一条指标。'; return
  }
  for (const criterion of working.value.criteria) {
    const problem = validateCriterion(criterion)
    if (problem) { error.value = `${itemName(criterion.item_id)} · ${criterion.name}：${problem}`; return }
  }
  busy.value = true; error.value = ''
  try {
    const response = await saveChecklistDraft(props.caseId, props.snapshotId, clone(working.value),
      props.revision, view.value?.checklist_revision ?? 0, draftReason.value.trim() || '手工调整审查要求',
      accessToken)
    view.value = response
    working.value = response.draft ? clone(response.draft) : working.value
    candidateUnsaved.value = false
    reselect()
    notice.value = `草稿已保存（清单版本 ${response.checklist_revision}）。尚未确认，确认后才会用于正式审查。`
    emit('saved')
  } catch (reason) { error.value = friendly(reason) }
  finally { busy.value = false }
}

async function submitConfirm() {
  if (!props.canWrite || busy.value) return
  const ids = selectedIds.value
  if (!ids.length) { error.value = '请至少选择一条要确认的指标。'; return }
  busy.value = true; error.value = ''
  try {
    const response = await confirmChecklist(props.caseId, props.snapshotId, ids, props.revision,
      view.value?.checklist_revision ?? 0, confirmReason.value.trim() || '确认当前审查要求', accessToken)
    view.value = response
    working.value = response.draft ? clone(response.draft) : working.value
    reselect()
    confirmOpen.value = false
    const excluded = response.excluded_criterion_ids?.length ?? 0
    notice.value = `已确认 ${ids.length} 项指标。` +
      (excluded ? `未纳入本次确认的 ${excluded} 项保留为草稿。` : '本次采购的正式审查将按这些指标执行。')
    emit('confirmed')
  } catch (reason) { error.value = friendly(reason) }
  finally { busy.value = false }
}

function startEdit(criterion: ChecklistCriterion) {
  editingId.value = criterion.criterion_id
  adding.value = false; addDraft.value = null
  editError.value = ''
  editDraft.value = clone(criterion)
}
async function applyEdit() {
  const draft = editDraft.value
  if (!draft || !working.value) return
  const problem = validateCriterion(draft)
  if (problem) { editError.value = problem; return }
  const index = working.value.criteria.findIndex(item => item.criterion_id === draft.criterion_id)
  if (index >= 0) working.value.criteria[index] = clone(draft)
  editingId.value = null; editDraft.value = null
  if (!draft.enabled) { const next = new Set(selected.value); next.delete(draft.criterion_id); selected.value = next }
}
function cancelEdit() { editingId.value = null; editDraft.value = null; editError.value = '' }
function removeCriterion(criterionId: string) {
  if (!working.value) return
  working.value.criteria = working.value.criteria.filter(item => item.criterion_id !== criterionId)
  if (!working.value.criteria.length) working.value.items = []
  else {
    const used = new Set(working.value.criteria.map(item => item.item_id))
    working.value.items = working.value.items.filter(item => used.has(item.item_id))
  }
  const next = new Set(selected.value); next.delete(criterionId); selected.value = next
  if (editingId.value === criterionId) cancelEdit()
}

function blankRule(): ChecklistRule {
  return { type: 'numeric', operator: 'gte', value: '', unit: '' }
}
function startAdd() {
  editingId.value = null; editDraft.value = null; editError.value = ''
  adding.value = true
  addDraft.value = { itemMode: 'existing', itemId: working.value?.items[0]?.item_id ?? '',
    newItemName: '', criterion: { criterion_id: `c${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`,
      item_id: '', name: '', rule: blankRule(), required: true, enabled: true,
      conditions: [], source: { kind: 'user_defined', reason: '用户手动设置', context: null } } }
}
async function applyAdd() {
  const draft = addDraft.value
  if (!draft || !working.value) return
  const criterion = draft.criterion
  if (draft.itemMode === 'new') {
    const name = draft.newItemName.trim()
    if (!name) { editError.value = '请填写新品目名称。'; return }
    const newItem = { item_id: `item${crypto.randomUUID().replaceAll('-', '').slice(0, 10)}`, name, specification: '' }
    working.value.items = [...working.value.items, newItem]
    criterion.item_id = newItem.item_id
  } else {
    if (!draft.itemId) { editError.value = '请选择品目。'; return }
    criterion.item_id = draft.itemId
  }
  const problem = validateCriterion(criterion)
  if (problem) {
    if (draft.itemMode === 'new') working.value.items = working.value.items.filter(item => item.item_id !== criterion.item_id)
    editError.value = problem; return
  }
  working.value.criteria = [...working.value.criteria, clone(criterion)]
  if (criterion.enabled) selected.value = new Set([...selected.value, criterion.criterion_id])
  adding.value = false; addDraft.value = null
}

function openContext(criterion: ChecklistCriterion) {
  const context = contextOf(criterion)
  if (context) emit('openEvidence', { caseId: props.caseId, snapshotId: props.snapshotId,
    evidenceId: context.evidence_refs[0] })
}

watch([() => props.caseId, () => props.snapshotId, () => props.candidate?.runId], () => { void load() }, { immediate: true })

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && confirmOpen.value) confirmOpen.value = false
}
window.addEventListener('keydown', onKeydown)
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <section class="checklist-panel" aria-label="审查要求清单">
    <div class="panel-state-row">
      <span v-if="view" class="state-pill" :class="`checklist-state-${view.state}`">{{ stateLabels[view.state] ?? view.state }}</span>
      <span v-if="view?.execution_available" class="state-pill status-complete">可执行完整审查</span>
      <span class="panel-revision" v-if="view">资料版本 v{{ view.revision }}</span>
    </div>
    <div v-if="error" class="chat-error" role="alert">{{ error }} <button class="text-button" @click="load">重试读取</button></div>
    <div v-if="loading" class="chat-loading" role="status">正在读取审查要求…</div>
    <template v-else-if="view">
      <p v-if="view.state === 'needs_reconfirmation'" class="state-note warning">
        资料已更新，之前的审查要求不能直接生效。
        <button v-if="canWrite && view.previous_version_id" class="text-button" data-test="adopt-draft" :disabled="busy" @click="adoptPrevious">以上一版确认为底稿</button>
      </p>
      <p v-else-if="view.state === 'historical'" class="state-note">历史资料版本的审查要求仅供追溯，不能在当前版本执行。</p>
      <p v-if="candidateUnsaved" class="state-note warning">助手候选要求尚未保存确认。请检查内容后保存草稿，再明确确认。</p>
      <div v-if="view.issues.length" class="state-note warning" role="alert">
        <strong>清单待处理：</strong>
        <ul><li v-for="issue in view.issues" :key="`${issue.criterion_id}-${issue.code}`">
          {{ itemName(issue.criterion_id) ? '' : '' }}{{ (working?.criteria ?? []).find(item => item.criterion_id === issue.criterion_id)?.name ?? issue.criterion_id }} · {{ issueLabels[issue.code] ?? issue.code }}
        </li></ul>
      </div>
      <p v-if="notice" class="state-note" role="status">{{ notice }}</p>

      <div v-if="!working" class="empty-state compact">
        <strong>还没有审查要求</strong>
        <span>可以在聊天中让助手从需求资料整理候选，或手动设置指标。</span>
        <button v-if="canWrite" class="button button-secondary" data-test="start-add" @click="startAdd">手动添加指标</button>
      </div>

      <template v-else>
        <div v-for="item in working.items" :key="item.item_id" class="checklist-item-group">
          <h3 class="checklist-item-name">{{ item.name }}<span v-if="item.specification" class="item-spec">{{ item.specification }}</span></h3>
          <div v-for="criterion in criteriaOf(item.item_id)" :key="criterion.criterion_id" class="criterion-row"
               :class="{ 'criterion-disabled': !criterion.enabled }">
            <div class="criterion-main">
              <div class="criterion-title">
                <span class="criterion-name" :data-test="`criterion-name-${criterion.criterion_id}`">
                  <input v-if="canWrite && editingId !== criterion.criterion_id" v-model="criterion.name"
                         :aria-label="`${criterion.name}的指标名称`" />
                  <strong v-else>{{ criterion.name }}</strong>
                </span>
                <span class="source-badge" :class="criterion.source.kind">{{ sourceLabel(criterion) }}</span>
                <span v-if="criterion.rule.type === 'manual' || criterion.conditions.length" class="manual-badge">人工复核</span>
                <span v-if="!criterion.enabled" class="disabled-badge">已停用</span>
                <span v-if="!criterion.required" class="optional-badge">可选</span>
              </div>
              <p class="rule-summary">{{ ruleSummary(criterion.rule) }}</p>
              <details v-if="contextOf(criterion)" class="criterion-context">
                <summary>依据原文</summary>
                <p>{{ contextOf(criterion)?.quote }}</p>
                <button class="text-button" @click="openContext(criterion)">查看原文 ↗</button>
              </details>
              <p v-if="criterion.source.kind === 'user_defined' && criterion.source.reason" class="criterion-reason">{{ criterion.source.reason }}</p>
              <p v-if="issueById.get(criterion.criterion_id)?.length" class="criterion-issues">
                {{ issueById.get(criterion.criterion_id)!.join('；') }}
              </p>
            </div>
            <div class="criterion-side">
              <label v-if="canWrite && view?.state !== 'historical'" class="criterion-select">
                <input type="checkbox" :data-test="`select-${criterion.criterion_id}`"
                       :checked="selected.has(criterion.criterion_id)" :disabled="!criterion.enabled"
                       @change="toggleSelect(criterion.criterion_id, $event)" />纳入确认
              </label>
              <label v-if="canWrite && editingId !== criterion.criterion_id" class="criterion-enabled">
                <input v-model="criterion.enabled" type="checkbox" />启用
              </label>
              <button v-if="canWrite && editingId !== criterion.criterion_id" class="text-button"
                      :data-test="`edit-${criterion.criterion_id}`" @click="startEdit(criterion)">编辑规则</button>
              <button v-if="canWrite && editingId !== criterion.criterion_id" class="text-button criterion-remove"
                      @click="removeCriterion(criterion.criterion_id)">删除</button>
            </div>

            <div v-if="editingId === criterion.criterion_id && editDraft" class="rule-edit-form" data-test="rule-form">
              <div class="rule-grid">
                <label>规则类型<select v-model="editDraft.rule.type" class="field-control">
                  <option value="numeric">数值</option><option value="money">金额</option>
                  <option value="enum">枚举</option><option value="presence">资料存在</option>
                  <option value="duration">期限</option><option value="manual">人工复核</option>
                </select></label>
                <template v-if="editDraft.rule.type === 'numeric' || editDraft.rule.type === 'money' || editDraft.rule.type === 'duration'">
                  <label>比较<select v-model="editDraft.rule.operator" class="field-control">
                    <option value="gte">至少</option><option value="lte">最多</option><option value="eq">等于</option>
                    <option value="gt">大于</option><option value="lt">小于</option><option value="between">区间</option>
                  </select></label>
                  <label v-if="editDraft.rule.operator !== 'between'">阈值<input v-model="editDraft.rule.value" class="field-control" data-test="rule-value" /></label>
                  <template v-else>
                    <label>下限<input v-model="editDraft.rule.lower" class="field-control" data-test="rule-lower" /></label>
                    <label>上限<input v-model="editDraft.rule.upper" class="field-control" data-test="rule-upper" /></label>
                  </template>
                  <label v-if="editDraft.rule.type === 'numeric'">单位<input v-model="editDraft.rule.unit" class="field-control" /></label>
                  <template v-if="editDraft.rule.type === 'money'">
                    <label>币种<select v-model="editDraft.rule.currency" class="field-control">
                      <option value="CNY">人民币</option><option value="USD">美元</option><option value="EUR">欧元</option>
                    </select></label>
                    <label>计价单位<input v-model="editDraft.rule.per_unit" class="field-control" /></label>
                    <label>含税口径<select v-model="editDraft.rule.tax_basis" class="field-control">
                      <option value="included">含税</option><option value="excluded">不含税</option><option value="unspecified">口径未定</option>
                    </select></label>
                  </template>
                  <template v-if="editDraft.rule.type === 'duration'">
                    <label>期限口径<select v-model="editDraft.rule.calendar" class="field-control">
                      <option value="natural_days">自然日</option><option value="working_days">工作日</option><option value="months">月</option>
                    </select></label>
                    <label>起算事件<input v-model="editDraft.rule.start_event" class="field-control" /></label>
                  </template>
                </template>
                <template v-else-if="editDraft.rule.type === 'enum'">
                  <label>匹配<select v-model="editDraft.rule.operator" class="field-control">
                    <option value="eq">等于</option><option value="in">属于</option>
                  </select></label>
                  <label>可选值（顿号分隔）<input :value="(editDraft.rule.values ?? []).join('、')"
                    class="field-control" @change="editDraft.rule.values = ($event.target as HTMLInputElement).value.split('、').map(item => item.trim()).filter(Boolean)" /></label>
                </template>
                <template v-else-if="editDraft.rule.type === 'presence'">
                  <label>资料说明<input v-model="editDraft.rule.description" class="field-control" /></label>
                  <label class="rule-checkbox"><input v-model="editDraft.rule.must_be_present" type="checkbox" />必须提供</label>
                </template>
                <label v-else-if="editDraft.rule.type === 'manual'" class="rule-wide">人工复核说明<textarea v-model="editDraft.rule.instruction" class="field-control" rows="2" /></label>
                <label>限定条件（顿号分隔）<input :value="editDraft.conditions.join('、')" class="field-control"
                  @change="editDraft.conditions = ($event.target as HTMLInputElement).value.split('、').map(item => item.trim()).filter(Boolean)" /></label>
                <label class="rule-checkbox"><input v-model="editDraft.required" type="checkbox" />必审指标</label>
              </div>
              <p v-if="editError" class="form-error">{{ editError }}</p>
              <div class="rule-actions">
                <button class="button button-primary" data-test="rule-save" @click="applyEdit">保存修改</button>
                <button class="button button-secondary" @click="cancelEdit">取消</button>
              </div>
            </div>
          </div>
        </div>

        <div v-if="adding && addDraft" class="rule-edit-form add-form">
          <div class="rule-grid">
            <label>归属品目<select v-model="addDraft.itemMode" class="field-control">
              <option value="existing">已有品目</option><option value="new">新建品目</option>
            </select></label>
            <label v-if="addDraft.itemMode === 'existing'">品目<select v-model="addDraft.itemId" class="field-control">
              <option v-for="item in working.items" :key="item.item_id" :value="item.item_id">{{ item.name }}</option>
            </select></label>
            <label v-else>新品目名称<input v-model="addDraft.newItemName" class="field-control" /></label>
            <label>指标名称<input v-model="addDraft.criterion.name" class="field-control" /></label>
            <template v-if="addDraft.criterion.rule.type === 'numeric' || addDraft.criterion.rule.type === 'money' || addDraft.criterion.rule.type === 'duration'">
              <label>比较<select v-model="addDraft.criterion.rule.operator" class="field-control">
                <option value="gte">至少</option><option value="lte">最多</option><option value="eq">等于</option>
                <option value="between">区间</option>
              </select></label>
              <label v-if="addDraft.criterion.rule.operator !== 'between'">阈值<input v-model="addDraft.criterion.rule.value" class="field-control" /></label>
              <template v-else>
                <label>下限<input v-model="addDraft.criterion.rule.lower" class="field-control" /></label>
                <label>上限<input v-model="addDraft.criterion.rule.upper" class="field-control" /></label>
              </template>
              <label v-if="addDraft.criterion.rule.type === 'numeric'">单位<input v-model="addDraft.criterion.rule.unit" class="field-control" /></label>
              <template v-if="addDraft.criterion.rule.type === 'money'">
                <label>币种<select v-model="addDraft.criterion.rule.currency" class="field-control">
                  <option value="CNY">人民币</option><option value="USD">美元</option><option value="EUR">欧元</option>
                </select></label>
                <label>计价单位<input v-model="addDraft.criterion.rule.per_unit" class="field-control" /></label>
              </template>
              <template v-if="addDraft.criterion.rule.type === 'duration'">
                <label>期限口径<select v-model="addDraft.criterion.rule.calendar" class="field-control">
                  <option value="natural_days">自然日</option><option value="working_days">工作日</option><option value="months">月</option>
                </select></label>
                <label>起算事件<input v-model="addDraft.criterion.rule.start_event" class="field-control" /></label>
              </template>
            </template>
            <template v-else-if="addDraft.criterion.rule.type === 'enum'">
              <label>匹配<select v-model="addDraft.criterion.rule.operator" class="field-control">
                <option value="eq">等于</option><option value="in">属于</option>
              </select></label>
              <label>可选值（顿号分隔）<input :value="(addDraft.criterion.rule.values ?? []).join('、')"
                class="field-control" @change="addDraft.criterion.rule.values = ($event.target as HTMLInputElement).value.split('、').map(item => item.trim()).filter(Boolean)" /></label>
            </template>
            <template v-else-if="addDraft.criterion.rule.type === 'presence'">
              <label>资料说明<input v-model="addDraft.criterion.rule.description" class="field-control" /></label>
              <label class="rule-checkbox"><input v-model="addDraft.criterion.rule.must_be_present" type="checkbox" />必须提供</label>
            </template>
            <label v-else-if="addDraft.criterion.rule.type === 'manual'" class="rule-wide">人工复核说明<textarea v-model="addDraft.criterion.rule.instruction" class="field-control" rows="2" /></label>
          </div>
          <p v-if="editError" class="form-error">{{ editError }}</p>
          <div class="rule-actions">
            <button class="button button-primary" data-test="add-save" @click="applyAdd">添加指标</button>
            <button class="button button-secondary" @click="adding = false; addDraft = null">取消</button>
          </div>
        </div>
        <div v-else-if="canWrite && view.state !== 'historical'" class="checklist-footer">
          <button class="text-button" data-test="start-add-row" @click="startAdd">＋ 添加指标</button>
        </div>
      </template>

      <div v-if="view" class="checklist-footer">
        <template v-if="canWrite && view.state !== 'historical'">
          <label class="draft-reason">保存说明<input v-model="draftReason" class="field-control" maxlength="200" /></label>
          <button class="button button-primary" data-test="save-draft" :disabled="busy || !working?.criteria.length" @click="save">保存草稿</button>
          <button class="button button-secondary" data-test="open-confirm" :disabled="busy || !selectedIds.length || !view?.checklist_revision" @click="confirmOpen = true">确认审查指标</button>
        </template>
        <span v-else class="readonly-note">当前为只读权限或历史版本，仅可查看。</span>
      </div>

      <div v-if="confirmOpen" class="confirm-overlay" role="dialog" aria-label="确认审查指标">
        <div class="confirm-card">
          <h3>确认审查指标</h3>
          <p>将确认 <strong data-test="confirm-count">{{ selectedIds.length }} 项指标</strong>、
            {{ confirmGroupCount }} 个品目，
            按资料版本 v{{ view.revision }} 执行。</p>
          <p class="confirm-detail">确认后，未勾选的指标保留为草稿，不会进入正式审查；正式审查会覆盖全部已确认且启用的指标。</p>
          <label class="draft-reason">确认说明<input v-model="confirmReason" class="field-control" maxlength="200" /></label>
          <div class="rule-actions">
            <button class="button button-primary" data-test="confirm-submit" :disabled="busy" @click="submitConfirm">确认生效</button>
            <button class="button button-secondary" @click="confirmOpen = false">再看看</button>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>
