<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import { accessToken } from '../auth'
import { fetchChecklist } from '../api'
import type { ChecklistView, DocumentRecord, EvidenceRecord } from '../api'
import { confirmItemBindings, fetchDocumentEvidence, fetchItemBindings } from '../itemBindings'
import type { ConfirmItemBindings, ItemBindingEntry, ItemBindingView } from '../itemBindings'
import { bindingRequestEntry, buildBindingEntry, draftFromEntry, draftSelector, evidenceInSelector,
  newBindingDraft, rangeLabel } from '../itemBindingForm'

const EvidencePanel = defineAsyncComponent(() => import('./EvidencePanel.vue'))
const props = defineProps<{ caseId: string; snapshotId: string; revision: number; document: DocumentRecord;
  canWrite: boolean; historical: boolean; refreshKey: number; busy?: boolean;
  notifyConfirmed?: (target: { caseId: string; snapshotId: string }) => void }>()
const emit = defineEmits<{ close: []; confirmed: [target: { caseId: string; snapshotId: string }] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const titleId = useId()
let opener: HTMLElement | null = null
const checklist = ref<ChecklistView | null>(null)
const view = ref<ItemBindingView | null>(null)
const working = ref<ItemBindingEntry[]>([])
const saved = ref('[]')
const evidence = ref<EvidenceRecord[]>([])
const parser = ref<string | null>(null)
const more = ref(false)
const loading = ref(false)
const paging = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const accessRejected = ref(false)
const conflicted = ref(false)
const uncertain = ref(false)
const pending = ref<{ body: ConfirmItemBindings; key: string } | null>(null)
const draft = ref(newBindingDraft())
const pristineDraft = ref(JSON.stringify(draft.value))
const editingIndex = ref<number | null>(null)
const confirmed = ref(false)
const reason = ref('已核对品目名称与原文范围，确认资料对应关系')
const originalTarget = ref<{ caseId: string; snapshotId: string; evidenceId: string } | null>(null)
const originalContainer = ref<HTMLElement | null>(null)
let generation = 0
const items = computed(() => checklist.value?.confirmed_version?.definition.items ?? [])
const writable = computed(() => !accessRejected.value && props.canWrite && !props.historical && props.document.role !== 'requirement' && checklist.value?.is_current_snapshot
  && ['confirmed', 'partially_confirmed'].includes(checklist.value.state) && !!checklist.value.confirmed_version
  && checklist.value.revision === props.revision && ['complete', 'partial'].includes(props.document.parse_status))
const locked = computed(() => loading.value || saving.value || props.busy || uncertain.value || conflicted.value || !writable.value)
const documentEntries = computed(() => working.value.map((entry, index) => ({ entry, index }))
  .filter(({ entry }) => entry.document_id === props.document.id))
const historicalEntries = computed(() => view.value?.historical_version?.payload.entries
  .filter(entry => entry.document_id === props.document.id) ?? [])
const dirty = computed(() => JSON.stringify(working.value) !== saved.value)
const formChanged = computed(() => editingIndex.value !== null || JSON.stringify(draft.value) !== pristineDraft.value)
const canSave = computed(() => !locked.value && dirty.value && confirmed.value && !!reason.value.trim() && !formChanged.value)
const rangePreview = computed(() => {
  try { const selector = draftSelector(draft.value); return evidence.value.filter(block => evidenceInSelector(block, selector)).slice(0, 50) }
  catch { return [] }
})
const sheets = computed(() => [...new Set(evidence.value.map(block => block.sheet).filter((sheet): sheet is string => !!sheet))])
const errorTexts: Record<string, string> = {
  validation_error: '输入未通过校验，请核对范围、原文与型号，再重新确认。',
  invalid_request: '请求内容无效，请核对输入或刷新资料。',
  file_limit: '请求超过大小限制，请减少对应范围。',
  item_binding_revision_conflict: '对应关系刚被其他人修改，请刷新并重新核对。',
  item_binding_checklist_changed: '审查清单已变化，请刷新并确认当前清单。',
  item_binding_source_changed: '资料或原文已变化，请刷新并重新核对。',
  revision_conflict: '资料版本已更新，请刷新并重新核对。',
  snapshot_changed: '资料版本已更新，请刷新并重新核对。',
  item_binding_invalid: '品目、范围或原文依据未通过校验，请核对是否存在重叠范围、其他品目或型号冲突。',
  item_binding_range: '范围为空或超出原件，请核对工作表、行列及页码。',
  item_binding_source: '资料尚未解析完成或来源不一致，请刷新后核对。',
  item_binding_confirmation_required: '请明确确认对应关系及整份文件的单一品目声明。',
  item_binding_capacity: '对应清单超过处理上限，请减少范围或拆分采购任务。',
  case_busy: '采购任务正在处理，请等待处理结束后再确认。',
  idempotency_conflict: '请求标识冲突，请刷新后重新核对。',
  not_found: '资料不存在或当前账号没有访问权限。',
  forbidden: '当前账号没有修改权限。',
  session_expired: '登录已过期，请重新登录后刷新。',
  case_deleting: '采购任务正在删除，不能修改对应关系。',
  evidence_page_changed: '原文分页或解析版本不一致，请刷新后重新读取。',
}
function friendly(value: unknown): string { return errorTexts[value instanceof Error ? value.message : ''] ?? '暂时无法读取或保存资料对应关系，请稍后重试。' }
function location(block: EvidenceRecord): string {
  return block.page_idx !== null ? `第 ${block.page_idx + 1} 页` : `${block.sheet}!${block.cell}`
}
function itemName(id: string): string { return items.value.find(item => item.item_id === id)?.name ?? id }
function resetDraft() { draft.value = newBindingDraft(); editingIndex.value = null; pristineDraft.value = JSON.stringify(draft.value) }
function rejectAccess(value: unknown): boolean {
  const code = value instanceof Error ? value.message : ''
  if (!['forbidden', 'not_found', 'session_expired', 'case_deleting'].includes(code)) return false
  // A successful read alone cannot prove that cached editor permissions still apply.
  accessRejected.value = true; generation++
  checklist.value = null; view.value = null; working.value = []; saved.value = '[]'
  evidence.value = []; parser.value = null; more.value = false; originalTarget.value = null
  loading.value = false; saving.value = false; paging.value = false
  pending.value = null; uncertain.value = false; confirmed.value = false; resetDraft()
  error.value = friendly(value); notice.value = ''
  return true
}
async function load() {
  const current = ++generation
  checklist.value = null; view.value = null; working.value = []; saved.value = '[]'
  evidence.value = []; parser.value = null; more.value = false; originalTarget.value = null
  error.value = ''; notice.value = ''; conflicted.value = false; uncertain.value = false; pending.value = null
  saving.value = false; paging.value = false; confirmed.value = false; resetDraft(); loading.value = true
  const { caseId, snapshotId, document } = props
  try {
    const [definition, bindings, page] = await Promise.all([
      fetchChecklist(caseId, snapshotId, accessToken), fetchItemBindings(caseId, snapshotId, accessToken),
      fetchDocumentEvidence(caseId, snapshotId, document.id, -1, accessToken),
    ])
    if (current !== generation) return
    if (definition.case_id !== caseId || definition.snapshot_id !== snapshotId || bindings.case_id !== caseId || bindings.snapshot_id !== snapshotId)
      throw new Error('evidence_page_changed')
    checklist.value = definition; view.value = bindings
    working.value = (bindings.current_version?.payload.entries ?? []).map(bindingRequestEntry)
    saved.value = JSON.stringify(working.value)
    acceptPage(page, -1)
  } catch (value) { if (current === generation && !rejectAccess(value)) error.value = friendly(value) }
  finally { if (current === generation) loading.value = false }
}
function acceptPage(page: Awaited<ReturnType<typeof fetchDocumentEvidence>>, after: number) {
  if (page.case_id !== props.caseId || page.snapshot_id !== props.snapshotId || page.document_id !== props.document.id
    || (parser.value && page.parser_version !== parser.value)
    || page.evidence.some((block, index) => block.document_id !== props.document.id
      || block.ordinal <= (index ? page.evidence[index - 1]!.ordinal : after)
      || evidence.value.some(previous => previous.id === block.id))
    || (page.evidence.length && page.next_ordinal !== page.evidence.at(-1)!.ordinal)) throw new Error('evidence_page_changed')
  parser.value = page.parser_version
  evidence.value = [...evidence.value, ...page.evidence]
  more.value = page.evidence.length === 100 && evidence.value.length < 25000
}
async function loadMore() {
  if (accessRejected.value || paging.value || loading.value || !more.value) return
  const current = generation; const after = evidence.value.at(-1)?.ordinal ?? -1
  paging.value = true
  try {
    const page = await fetchDocumentEvidence(props.caseId, props.snapshotId, props.document.id, after, accessToken)
    if (current === generation) acceptPage(page, after)
  } catch (value) { if (current === generation && !rejectAccess(value)) error.value = friendly(value) }
  finally { if (current === generation) paging.value = false }
}
function chooseAnchor(sku = false) {
  const id = sku ? draft.value.skuAnchorId : draft.value.anchorId
  const text = evidence.value.find(block => block.id === id)?.source_text ?? ''
  if (sku) draft.value.skuQuote = text.length <= 2000 ? text : ''
  else draft.value.anchorQuote = text.length <= 2000 ? text : ''
}
function stage() {
  if (locked.value) return
  error.value = ''; notice.value = ''
  try {
    const entry = buildBindingEntry(draft.value, props.document, items.value, evidence.value, parser.value)
    const next = [...working.value]
    if (editingIndex.value === null) next.push(entry)
    else next[editingIndex.value] = entry
    if (next.length > 140) throw new Error('对应清单不能超过 140 个范围。')
    if (next.filter(value => value.document_id === props.document.id).some(value => value !== entry
      && JSON.stringify(value) === JSON.stringify(entry))) throw new Error('这个范围已在待确认清单中。')
    working.value = next; resetDraft(); confirmed.value = false
  } catch (value) { error.value = value instanceof Error ? value.message : '请检查输入。' }
}
function edit(index: number) { if (!locked.value) { draft.value = draftFromEntry(working.value[index]!); editingIndex.value = index; confirmed.value = false } }
function remove(index: number) { if (!locked.value) { working.value = working.value.filter((_, position) => position !== index); resetDraft(); confirmed.value = false } }
async function preview(id: string) {
  if (accessRejected.value) return
  originalTarget.value = { caseId: props.caseId, snapshotId: props.snapshotId, evidenceId: id }
  await scrollOriginal()
}
async function scrollOriginal() {
  await nextTick()
  originalContainer.value?.scrollIntoView?.({ block: 'start' })
}
async function save(retry = false) {
  if (saving.value || props.busy || !writable.value || loading.value || conflicted.value || (retry ? !uncertain.value || !pending.value : !canSave.value)) return
  const current = generation; const { caseId, snapshotId } = props
  if (!retry) {
    const body: ConfirmItemBindings = { entries: working.value.map(bindingRequestEntry), confirmed: true,
      checklist_version_id: checklist.value!.confirmed_version!.id, expected_revision: props.revision,
      expected_checklist_revision: checklist.value!.checklist_revision, expected_binding_revision: view.value!.binding_revision,
      reason: reason.value.trim() }
    if (new TextEncoder().encode(JSON.stringify(body.entries)).length > 128000) { error.value = errorTexts.item_binding_capacity!; return }
    pending.value = { body, key: crypto.randomUUID() }
  }
  saving.value = true; error.value = ''
  const request = pending.value!
  const notifyConfirmed = props.notifyConfirmed
  try {
    const response = await confirmItemBindings(caseId, snapshotId, request.body, request.key, accessToken)
    if (response.case_id !== caseId || response.snapshot_id !== snapshotId || response.state !== 'confirmed' || !response.current_version
      || response.current_version.snapshot_id !== snapshotId || response.current_version.checklist_version_id !== request.body.checklist_version_id)
      throw new Error('invalid_confirmation_response')
    // Notify the Case owner even if closing this editor has ended its local UI lifetime.
    notifyConfirmed?.({ caseId, snapshotId })
    if (current !== generation) return
    confirmed.value = false; uncertain.value = false; pending.value = null
    notice.value = '资料对应关系已确认。原审查结果保留为历史，请重新发起审查；本次保存不会自动调用模型。'
    emit('confirmed', { caseId, snapshotId })
    // An idempotent receipt may describe an older committed version, never the latest head.
    try {
      const latest = await fetchItemBindings(caseId, snapshotId, accessToken)
      if (current !== generation) return
      if (latest.case_id !== caseId || latest.snapshot_id !== snapshotId) throw new Error('binding_scope')
      view.value = latest; working.value = (latest.current_version?.payload.entries ?? []).map(bindingRequestEntry)
      saved.value = JSON.stringify(working.value)
      if (latest.state !== 'confirmed' || latest.current_version?.checklist_version_id !== checklist.value?.confirmed_version?.id) {
        checklist.value = null; conflicted.value = true
        notice.value += ' 资料或清单已有后续变更，请刷新后继续。'
      }
    } catch (value) {
      if (current === generation && !rejectAccess(value)) {
        view.value = null; conflicted.value = true
        error.value = '已收到保存成功回执，但最新对应状态暂时无法读取。请刷新记录；无需重复保存。'
      }
    }
  } catch (value) {
    if (current !== generation) return
    if (rejectAccess(value)) return
    const code = value instanceof Error ? value.message : ''
    if (errorTexts[code] || /^http_4\d\d$/.test(code)) {
      error.value = friendly(value); pending.value = null; uncertain.value = false; confirmed.value = false
      conflicted.value = ['item_binding_revision_conflict', 'item_binding_checklist_changed', 'item_binding_source_changed', 'revision_conflict', 'snapshot_changed', 'idempotency_conflict'].includes(code)
    } else {
      uncertain.value = true; error.value = '保存结果暂时无法确认。可重试原请求，或刷新读取服务器记录；继续编辑前请先核对保存结果。'
    }
  } finally { if (current === generation) saving.value = false }
}
watch([() => props.caseId, () => props.snapshotId, () => props.document.id, () => props.historical,
  () => props.canWrite], () => { accessRejected.value = false })
watch([() => props.caseId, () => props.snapshotId, () => props.revision, () => props.document.id,
  () => props.document.source_sha256, () => props.document.parse_status, () => props.document.model_name,
  () => props.refreshKey, () => props.historical, () => props.canWrite], () => { void load() }, { immediate: true })
watch(draft, () => { confirmed.value = false }, { deep: true })
watch(reason, () => { confirmed.value = false })
function closeModal() {
  if (dialog.value?.open) dialog.value.close()
  if (opener?.isConnected) opener.focus({ preventScroll: true })
}
function requestClose() {
  if (saving.value) return
  closeModal()
  emit('close')
}
function keepFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !dialog.value) return
  const controls = [...dialog.value.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], [tabindex]')]
    .filter(element => element.tabIndex >= 0 && !element.matches(':disabled') && element.getClientRects().length)
  const first = controls[0]; const last = controls.at(-1)
  const target = event.shiftKey && window.document.activeElement === first ? last
    : !event.shiftKey && window.document.activeElement === last ? first : null
  if (target) { event.preventDefault(); target.focus() }
}
onMounted(() => {
  opener = window.document.activeElement instanceof HTMLElement ? window.document.activeElement : null
  dialog.value?.showModal()
})
onBeforeUnmount(closeModal)
onUnmounted(() => { generation++ })
</script>

<template>
  <dialog ref="dialog" class="binding-dialog" :aria-labelledby="titleId" aria-modal="true" @cancel.prevent="requestClose" @keydown="keepFocus">
    <header class="binding-dialog-header">
      <div><h3 :id="titleId">关联采购品目</h3><p class="binding-dialog-file" :title="document.original_name">{{ document.original_name }}</p></div>
      <button class="text-button" type="button" data-test="binding-close" autofocus :disabled="saving" @click="requestClose">关闭</button>
    </header>
    <section class="binding-editor binding-dialog-body" aria-label="资料关联采购品目">
    <p class="section-intro">明确这份资料的哪些原文用于哪个采购品目。对应关系不代替供应商承诺或达标证据。</p>
    <p v-if="loading" role="status">正在读取清单、对应关系和原文…</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="state-note" role="status">{{ notice }}</p>
    <p v-if="accessRejected" class="state-note">访问已被拒绝，编辑已停止。请刷新采购任务或重新登录后，再打开此面板核对权限。</p>
    <button class="text-button" :disabled="loading || saving" @click="load">刷新对应记录{{ dirty || formChanged ? '（舍弃本地未确认变更）' : '' }}</button>
    <button v-if="uncertain" class="button button-secondary" data-test="binding-retry" :disabled="saving || busy || !writable" @click="save(true)">重试原保存请求</button>
    <template v-if="view && !loading">
      <p v-if="historical || !canWrite" class="state-note">当前为只读记录，不能修改或确认对应关系。</p>
      <p v-else-if="!writable" class="state-note">请先保存并确认当前审查清单，并等待资料完成解析，再关联采购品目。</p>
      <p v-if="document.parse_status === 'partial'" class="state-note warning">资料仅部分解析。确认对应关系不会补齐缺失原文，也不会把未完成审查变成正式结论。</p>
      <details v-if="view.historical_version" class="binding-history">
        <summary>历史对应关系（只读） · 版本 {{ view.historical_version.version }}</summary>
        <p>最近确认记录属于其原资料与清单版本，已失效或不适用于当前选择；不会自动写入本次清单。</p>
        <p v-for="(entry, index) in historicalEntries" :key="index">{{ rangeLabel(entry.selector) }} · 原文依据：{{ entry.anchor.quote }}</p>
        <p>确认理由：{{ view.historical_version.reason }} · {{ new Date(view.historical_version.confirmed_at).toLocaleString('zh-CN') }}</p>
      </details>
      <h4>{{ dirty ? '待确认变更' : '当前对应关系' }}</h4>
      <details v-if="view.current_version" class="binding-history"><summary>确认记录 · 对应版本 {{ view.current_version.version }}</summary><p>{{ view.current_version.reason }}</p><p>确认时间：{{ new Date(view.current_version.confirmed_at).toLocaleString('zh-CN') }}</p></details>
      <p v-if="!documentEntries.length" class="state-note">此文件尚未确认对应品目。</p>
      <ul class="binding-entry-list">
        <li v-for="{ entry, index } in documentEntries" :key="index">
          <strong>{{ itemName(entry.item_id) }}</strong><span>{{ rangeLabel(entry.selector) }}</span>
          <blockquote>{{ entry.anchor.quote }}</blockquote>
          <span>旧标识分类：{{ { category: '品目名称', sku: '具体型号／SKU', unknown: '暂不确定' }[entry.identifier_class] }}<template v-if="entry.supplier_sku"> · SKU：{{ entry.supplier_sku }}</template></span>
          <button class="text-button" @click="preview(entry.anchor.evidence_refs[0]!)">核对对应依据原件</button>
          <div v-if="writable"><button class="text-button" :disabled="locked" @click="edit(index)">编辑范围</button><button class="text-button" :disabled="locked" @click="remove(index)">移出待确认清单</button></div>
        </li>
      </ul>
      <p v-if="working.length > documentEntries.length" class="state-note">保存时会保留其他文件的 {{ working.length - documentEntries.length }} 个当前对应范围。</p>
      <form v-if="writable" data-test="binding-entry-form" @submit.prevent="stage">
        <fieldset :disabled="locked" class="binding-fields">
          <legend>{{ editingIndex === null ? '添加对应范围' : '编辑对应范围' }}</legend>
          <label>采购品目<select v-model="draft.itemId" data-test="binding-item" class="field-control"><option value="">从已确认清单选择</option><option v-for="item in items" :key="item.item_id" :value="item.item_id">{{ item.name }}</option></select></label>
          <label>原文范围<select v-model="draft.kind" data-test="binding-kind" class="field-control"><option value="whole">整份文件为单一品目</option><option :value="document.original_name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'xlsx'">仅指定{{ document.original_name.toLowerCase().endsWith('.pdf') ? '连续页面' : '表格范围' }}</option></select></label>
          <label v-if="draft.kind === 'whole'" class="binding-checkbox"><input v-model="draft.singleItem" data-test="binding-single" type="checkbox" />我已核对，整份文件只描述此单一品目</label>
          <div v-if="draft.kind === 'xlsx'" class="binding-range-grid">
            <label class="binding-wide">工作表<input v-model="draft.sheet" data-test="binding-sheet" class="field-control" list="binding-sheets" maxlength="255" /><datalist id="binding-sheets"><option v-for="sheet in sheets" :key="sheet" :value="sheet" /></datalist></label>
            <label>起始行<input v-model="draft.startRow" data-test="binding-start-row" class="field-control" inputmode="numeric" maxlength="7" /></label>
            <label>结束行<input v-model="draft.endRow" data-test="binding-end-row" class="field-control" inputmode="numeric" maxlength="7" /></label>
            <label>起始列<input v-model="draft.startColumn" data-test="binding-start-column" class="field-control" placeholder="A" maxlength="3" /></label>
            <label>结束列<input v-model="draft.endColumn" data-test="binding-end-column" class="field-control" placeholder="C" maxlength="3" /></label>
          </div>
          <div v-if="draft.kind === 'pdf'" class="binding-range-grid">
            <label>起始页<input v-model="draft.startPage" data-test="binding-start-page" class="field-control" inputmode="numeric" maxlength="2" /></label>
            <label>结束页<input v-model="draft.endPage" data-test="binding-end-page" class="field-control" inputmode="numeric" maxlength="2" /></label>
          </div>
          <label>品目原文依据<select v-model="draft.anchorId" data-test="binding-anchor" class="field-control" @change="chooseAnchor()"><option value="">选择包含品目名称或明确型号的原文</option><option v-for="block in evidence" :key="block.id" :value="block.id">{{ location(block) }} · {{ block.source_text.slice(0, 70) }}</option></select></label>
          <label>对应依据摘录<textarea v-model="draft.anchorQuote" data-test="binding-quote" class="field-control" maxlength="2000" rows="3" placeholder="保留原文中的连续文字，不能编造或拼接" /></label>
          <button v-if="draft.anchorId" class="text-button" type="button" data-test="binding-original" @click="preview(draft.anchorId)">核对依据原件</button>
          <p class="state-note">依据可在数值范围之外，但必须来自同一文件；它不会扩大审查的原文范围。</p>
          <p class="state-note">上传时的旧产品标识：{{ document.model_name || '未填写' }}</p>
          <label>旧产品标识如何理解<select v-model="draft.identifierClass" data-test="binding-identifier" class="field-control"><option value="unknown">暂不确定（保留型号核对）</option><option value="category">采购品目名称</option><option value="sku">供应商具体型号／SKU</option></select></label>
          <label>供应商具体型号／SKU（选填）<input v-model="draft.sku" data-test="binding-sku" class="field-control" maxlength="120" placeholder="例如具体款号；必须有原文支持" /></label>
          <template v-if="draft.sku.trim() || draft.identifierClass === 'sku'">
            <label>型号／SKU原文依据<select v-model="draft.skuAnchorId" data-test="binding-sku-anchor" class="field-control" @change="chooseAnchor(true)"><option value="">选择型号所在原文</option><option v-for="block in evidence" :key="block.id" :value="block.id">{{ location(block) }} · {{ block.source_text.slice(0, 70) }}</option></select></label>
            <label>型号／SKU摘录<textarea v-model="draft.skuQuote" data-test="binding-sku-quote" class="field-control" maxlength="2000" rows="2" /></label>
          </template>
          <div class="binding-actions"><button class="button button-secondary" type="submit">{{ editingIndex === null ? '加入待确认清单' : '更新待确认范围' }}</button><button v-if="formChanged" class="text-button" type="button" @click="resetDraft">取消本次填写</button></div>
        </fieldset>
      </form>
      <details class="binding-preview" open>
        <summary>所选范围的解析原文预览</summary>
        <p class="state-note">已加载 {{ evidence.length }} 段原文。以下只显示已加载范围内的前 50 段，不能据此判断完整覆盖。多品目表格请划分范围；同页混合 PDF 需要拆分资料。</p>
        <ul data-test="binding-range-preview"><li v-for="block in rangePreview" :key="block.id"><span>{{ location(block) }}</span><p>{{ block.source_text }}</p><button class="text-button" @click="preview(block.id)">核对原件</button></li></ul>
        <p v-if="!rangePreview.length" class="state-note">已加载内容中没有匹配的原文，请核对范围或继续加载。</p>
      </details>
      <button v-if="more" class="text-button" data-test="binding-load-more" :disabled="paging || saving" @click="loadMore">{{ paging ? '读取中…' : '加载后续原文（每次100段）' }}</button>
      <div v-if="writable && dirty" class="binding-confirmation">
        <p>待确认清单共 {{ working.length }} 个范围。保存后原审查结果保留为历史，需重新发起审查。</p>
        <label>确认理由<input v-model="reason" data-test="binding-reason" class="field-control" maxlength="1000" :disabled="locked" /></label>
      </div>
    </template>
    <div v-if="originalTarget" ref="originalContainer" class="binding-original"><EvidencePanel :target="originalTarget" :width="390" @close="originalTarget = null" @settled="scrollOriginal" /></div>
    </section>
    <footer class="binding-dialog-footer">
      <template v-if="view && !loading && writable && dirty">
        <label class="binding-checkbox"><input v-model="confirmed" data-test="binding-confirm" type="checkbox" :disabled="locked || formChanged" />我已核对当前清单及原文，确认保存这些对应关系</label>
        <p v-if="formChanged" class="state-note">还有未加入清单的填写，请先加入或取消，再确认保存。</p>
      </template>
      <div class="binding-dialog-buttons">
        <button class="button button-secondary" type="button" :disabled="saving" @click="requestClose">关闭</button>
        <button v-if="view && !loading && writable && dirty" class="button button-primary" type="button" data-test="binding-save" :disabled="!canSave" @click="save()">{{ saving ? '保存中…' : '确认资料对应关系' }}</button>
      </div>
    </footer>
  </dialog>
</template>
