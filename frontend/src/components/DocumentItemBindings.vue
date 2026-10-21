<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import { accessToken } from '../auth'
import { fetchChecklist } from '../api'
import type { ChecklistView, DocumentRecord, EvidenceRecord } from '../api'
import { confirmItemBindings, fetchDocumentEvidence, fetchItemBindings } from '../itemBindings'
import type { ConfirmItemBindings, ItemBindingView } from '../itemBindings'
import { bindingRequestEntry, buildBindingEntry, draftFromEntry, draftSelector, evidenceInSelector,
  newBindingDraft, rangeLabel } from '../itemBindingForm'
import { bindingRowState, bindingRowsPayload, createBindingRows } from '../itemBindingEdits'
import type { BindingEditRow } from '../itemBindingEdits'

const EvidencePanel = defineAsyncComponent(() => import('./EvidencePanel.vue'))
const props = defineProps<{ caseId: string; snapshotId: string; revision: number; document: DocumentRecord;
  canWrite: boolean; historical: boolean; refreshKey: number; busy?: boolean;
  notifyConfirmed?: (target: { caseId: string; snapshotId: string }) => void }>()
const emit = defineEmits<{ close: []; confirmed: [target: { caseId: string; snapshotId: string }] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const titleId = useId()
const leaveTitleId = useId()
const keepEditingButton = ref<HTMLButtonElement | null>(null)
let leaveOpener: HTMLElement | null = null
let opener: HTMLElement | null = null
const checklist = ref<ChecklistView | null>(null)
const view = ref<ItemBindingView | null>(null)
const rows = ref<BindingEditRow[]>([])
const working = computed(() => bindingRowsPayload(rows.value))
const evidence = ref<EvidenceRecord[]>([])
const parser = ref<string | null>(null)
const more = ref(false)
const loading = ref(false)
const paging = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const saveAcknowledged = ref(false)
const feedbackContainer = ref<HTMLElement | null>(null)
const accessRejected = ref(false)
const conflicted = ref(false)
const uncertain = ref(false)
const pending = ref<{ body: ConfirmItemBindings; key: string } | null>(null)
const draft = ref(newBindingDraft())
const pristineDraft = ref(JSON.stringify(draft.value))
const editingIndex = ref<number | null>(null)
const formOpen = ref(false)
const leaveAction = ref<'close' | 'refresh' | null>(null)
const confirmed = ref(false)
const reason = ref('已核对商品名称与文件范围，确认本次关联修改')
const originalTarget = ref<{ caseId: string; snapshotId: string; evidenceId: string } | null>(null)
const originalContainer = ref<HTMLElement | null>(null)
let generation = 0
const items = computed(() => checklist.value?.confirmed_version?.definition.items ?? [])
const writable = computed(() => !accessRejected.value && props.canWrite && !props.historical && props.document.role !== 'requirement' && checklist.value?.is_current_snapshot
  && ['confirmed', 'partially_confirmed'].includes(checklist.value.state) && !!checklist.value.confirmed_version
  && checklist.value.revision === props.revision && ['complete', 'partial'].includes(props.document.parse_status))
const locked = computed(() => loading.value || saving.value || props.busy || uncertain.value || conflicted.value || !!leaveAction.value || !writable.value)
const documentRows = computed(() => rows.value.map((row, index) => ({ row, index, state: bindingRowState(row) }))
  .filter(({ row }) => (row.entry ?? row.original)?.document_id === props.document.id))
const currentRows = computed(() => documentRows.value.filter(({ row }) => row.original))
const changedRows = computed(() => documentRows.value.filter(({ state }) => state !== 'unchanged'))
const otherCount = computed(() => working.value.filter(entry => entry.document_id !== props.document.id).length)
const historicalEntries = computed(() => view.value?.historical_version?.payload.entries
  .filter(entry => entry.document_id === props.document.id) ?? [])
const dirty = computed(() => rows.value.some(row => bindingRowState(row) !== 'unchanged'))
const formChanged = computed(() => editingIndex.value !== null || JSON.stringify(draft.value) !== pristineDraft.value)
const canSave = computed(() => !locked.value && dirty.value && confirmed.value && !!reason.value.trim() && !formChanged.value)
const changeCounts = computed(() => ({ added: changedRows.value.filter(row => row.state === 'added').length,
  modified: changedRows.value.filter(row => row.state === 'modified').length,
  removed: changedRows.value.filter(row => row.state === 'removed').length }))
const currentMatches = computed(() => view.value?.state === 'confirmed'
  && view.value.current_version?.snapshot_id === props.snapshotId
  && view.value.current_version?.checklist_version_id === checklist.value?.confirmed_version?.id)
const associationStatus = computed(() => {
  if (props.historical) return { tone: 'neutral', title: '历史关联 · 只读', description: '这里展示该资料版本的记录，不能用于确认当前任务的关联。' }
  if (uncertain.value) return { tone: 'warning', title: '保存结果待核实', description: '请求可能已经保存，请读取服务器记录核对，或重试原保存请求。' }
  if (conflicted.value || (view.value?.state === 'confirmed' && !currentMatches.value))
    return { tone: 'warning', title: '需要刷新核对', description: '当前资料、审查要求或关联状态已变化。请刷新后继续。' }
  if (currentMatches.value && currentRows.value.length) return { tone: 'success', title: '已关联',
    description: dirty.value ? '下方原关联仍然生效；本次修改保存后生效。' : '无需重复关联。核对无误后，可返回对话开始审查。' }
  if (historicalEntries.value.length) return { tone: 'warning', title: '需要重新确认', description: '旧关联不适用于当前资料或审查要求，请核对后重新填写并确认。' }
  return { tone: 'neutral', title: '尚未关联', description: '请选择这份资料用于哪个采购商品，并核对文件中的名称依据。' }
})
const feedback = computed(() => {
  if (saving.value) return { tone: 'pending', title: saveAcknowledged.value ? '已保存，正在刷新对应状态…' : '正在保存资料关联…',
    message: '请稍候，处理完成后将在此处显示结果。' }
  if (error.value) return { tone: saveAcknowledged.value || uncertain.value ? 'warning' : 'error',
    title: saveAcknowledged.value ? '资料关联已保存，后续读取未完成' : uncertain.value ? '保存结果待核对' : '关联操作未完成', message: error.value }
  if (notice.value) return { tone: conflicted.value ? 'warning' : 'success',
    title: conflicted.value ? '资料关联已保存，请重新核对' : '资料关联已确认', message: notice.value }
  return null
})
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
function resetDraft() { draft.value = newBindingDraft(); editingIndex.value = null; pristineDraft.value = JSON.stringify(draft.value); formOpen.value = false }
function startAdding() { if (!locked.value && !formChanged.value) { clearFeedback(); resetDraft(); formOpen.value = true } }
function clearFeedback() { error.value = ''; notice.value = ''; saveAcknowledged.value = false }
function rejectAccess(value: unknown): boolean {
  const code = value instanceof Error ? value.message : ''
  if (!['forbidden', 'not_found', 'session_expired', 'case_deleting'].includes(code)) return false
  // A successful read alone cannot prove that cached editor permissions still apply.
  accessRejected.value = true; generation++
  checklist.value = null; view.value = null; rows.value = []
  evidence.value = []; parser.value = null; more.value = false; originalTarget.value = null
  loading.value = false; saving.value = false; paging.value = false
  pending.value = null; uncertain.value = false; confirmed.value = false; leaveAction.value = null; resetDraft()
  error.value = friendly(value); notice.value = ''
  return true
}
async function load(preserveUnknown = false) {
  const unresolved = preserveUnknown && uncertain.value && pending.value ? {
    pending: pending.value, checklist: checklist.value, view: view.value, rows: rows.value,
    evidence: evidence.value, parser: parser.value, more: more.value,
  } : null
  const current = ++generation
  checklist.value = null; view.value = null; rows.value = []; leaveAction.value = null
  evidence.value = []; parser.value = null; more.value = false; originalTarget.value = null
  clearFeedback(); conflicted.value = false; uncertain.value = false; pending.value = null
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
    rows.value = createBindingRows(bindings.current_version?.payload.entries ?? [])
    formOpen.value = !currentRows.value.length
    if (bindings.state === 'confirmed' && !currentMatches.value) conflicted.value = true
    acceptPage(page, -1)
  } catch (value) {
    if (current === generation && !rejectAccess(value)) {
      if (unresolved) {
        checklist.value = unresolved.checklist; view.value = unresolved.view; rows.value = unresolved.rows
        evidence.value = unresolved.evidence; parser.value = unresolved.parser; more.value = unresolved.more
        pending.value = unresolved.pending; uncertain.value = true; conflicted.value = false
        error.value = '未能读取服务器记录，保存结果仍待核实。可再次刷新，或重试原保存请求。'
      } else error.value = friendly(value)
    }
  }
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
  clearFeedback()
  try {
    const entry = buildBindingEntry(draft.value, props.document, items.value, evidence.value, parser.value)
    const next = rows.value.map(row => ({ ...row }))
    if (editingIndex.value === null) next.push({ key: crypto.randomUUID(), original: null, entry })
    else next[editingIndex.value]!.entry = entry
    const payload = bindingRowsPayload(next)
    if (payload.length > 140) throw new Error('关联不能超过 140 个文件范围。')
    if (next.some((row, index) => index !== (editingIndex.value ?? next.length - 1)
      && row.entry?.document_id === props.document.id
      && JSON.stringify(row.entry) === JSON.stringify(entry))) throw new Error('这个范围已在待确认清单中。')
    rows.value = next; resetDraft(); confirmed.value = false
  } catch (value) { error.value = value instanceof Error ? value.message : '请检查输入。' }
}
function edit(index: number) {
  const entry = rows.value[index]?.entry
  if (locked.value || formChanged.value || !entry) return
  clearFeedback(); draft.value = draftFromEntry(entry); editingIndex.value = index; formOpen.value = true; confirmed.value = false
}
function remove(index: number) {
  if (locked.value || formChanged.value) return
  clearFeedback()
  if (rows.value[index]!.original) rows.value[index]!.entry = null
  else rows.value.splice(index, 1)
  resetDraft(); confirmed.value = false
}
function undo(index: number) {
  if (locked.value || formChanged.value) return
  clearFeedback()
  const row = rows.value[index]!
  if (row.original) row.entry = bindingRequestEntry(row.original)
  else rows.value.splice(index, 1)
  confirmed.value = false
}
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
  clearFeedback(); saving.value = true
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
    saveAcknowledged.value = true
    notice.value = '资料对应关系已确认。原审查结果保留为历史，请重新发起审查。'
    emit('confirmed', { caseId, snapshotId })
    // An idempotent receipt may describe an older committed version, never the latest head.
    try {
      const latest = await fetchItemBindings(caseId, snapshotId, accessToken)
      if (current !== generation) return
      if (latest.case_id !== caseId || latest.snapshot_id !== snapshotId) throw new Error('binding_scope')
      view.value = latest; rows.value = createBindingRows(latest.current_version?.payload.entries ?? []); resetDraft()
      if (latest.state !== 'confirmed' || !currentMatches.value) {
        checklist.value = null; conflicted.value = true
        notice.value += ' 资料或清单已有后续变更，请刷新后继续。'
      }
    } catch (value) {
      if (current === generation && !rejectAccess(value)) {
        view.value = null; rows.value = []; resetDraft(); conflicted.value = true
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
watch(draft, () => {
  confirmed.value = false
  if (saveAcknowledged.value && !saving.value && formChanged.value) clearFeedback()
}, { deep: true })
watch(reason, () => { confirmed.value = false })
watch(feedback, result => {
  if (result && result.tone !== 'pending') feedbackContainer.value?.focus({ preventScroll: true })
}, { flush: 'post' })
function closeModal() {
  if (dialog.value?.open) dialog.value.close()
  if (opener?.isConnected) opener.focus({ preventScroll: true })
}
function requestClose() {
  if (saving.value) return
  if (leaveAction.value) { keepEditing(); return }
  if (dirty.value || formChanged.value || uncertain.value) { askLeave('close'); return }
  closeModal()
  emit('close')
}
function requestRefresh() {
  if (loading.value || saving.value) return
  if (dirty.value || formChanged.value || uncertain.value) { askLeave('refresh'); return }
  void load()
}
function askLeave(action: 'close' | 'refresh') {
  leaveOpener = window.document.activeElement instanceof HTMLElement ? window.document.activeElement : null
  leaveAction.value = action
  void nextTick(() => keepEditingButton.value?.focus({ preventScroll: true }))
}
function keepEditing() { leaveAction.value = null; void nextTick(() => leaveOpener?.focus({ preventScroll: true })) }
function confirmLeave() {
  const action = leaveAction.value; leaveAction.value = null
  if (saving.value) return
  if (action === 'refresh') void load(uncertain.value)
  else if (action === 'close') { closeModal(); emit('close') }
}
function keepFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !dialog.value) return
  const controls = [...dialog.value.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], [tabindex]')]
    .filter(element => element.tabIndex >= 0 && !element.matches(':disabled') && !element.closest('[inert]') && element.getClientRects().length)
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
      <div><h3 :id="titleId">关联采购商品</h3><p class="binding-dialog-file" :title="document.original_name">{{ document.original_name }}</p></div>
      <button class="text-button" type="button" data-test="binding-close" autofocus :disabled="saving || !!leaveAction" @click="requestClose">关闭</button>
    </header>
    <section class="binding-editor binding-dialog-body" aria-label="资料关联采购商品" :inert="leaveAction ? true : undefined">
      <p v-if="loading" role="status">正在读取清单、关联记录和原文…</p>
      <p v-if="accessRejected" class="state-note">访问已被拒绝，编辑已停止。请刷新采购任务或重新登录后，再打开此面板核对权限。</p>
      <template v-if="view && !loading">
        <div class="binding-state" :class="`binding-state-${associationStatus.tone}`" data-test="binding-state" role="status">
          <strong>{{ associationStatus.title }}</strong><p>{{ associationStatus.description }}</p>
        </div>
        <p v-if="historical || !canWrite" class="state-note">当前为只读记录，不能修改或确认关联。</p>
        <p v-else-if="!writable" class="state-note">请先保存并确认当前审查清单，并等待资料完成解析，再关联采购商品。</p>
        <p v-if="busy" class="state-note">采购任务正在处理，结束后可继续修改。</p>
        <p v-if="document.parse_status === 'partial'" class="state-note warning">资料仅部分解析。关联不会补齐缺失原文，也不会把未完成审查变成正式结论。</p>

        <section v-if="historicalEntries.length && !currentMatches" class="binding-group binding-previous" data-test="binding-previous" aria-label="上次关联，仅供参考">
          <h4>上次关联 <span>仅供参考，尚未重新确认</span></h4>
          <p v-for="(entry, index) in historicalEntries" :key="index"><strong>{{ itemName(entry.item_id) }}</strong> · {{ rangeLabel(entry.selector) }}</p>
          <details class="binding-entry-evidence"><summary>查看旧关联的名称依据</summary><blockquote v-for="(entry, index) in historicalEntries" :key="index">{{ entry.anchor.quote }}</blockquote></details>
        </section>

        <section v-if="currentMatches && currentRows.length" class="binding-group" data-test="binding-current" :aria-label="uncertain ? '保存前读取的关联' : historical ? '该版本的已确认关联' : '当前生效的关联'">
          <h4>{{ uncertain ? '保存前读取的关联' : historical ? '该版本的已确认关联' : '当前生效的关联' }} <span>{{ currentRows.length }} 项</span></h4>
          <ul class="binding-entry-list">
            <li v-for="{ row, index, state } in currentRows" :key="row.key">
              <template v-if="row.original">
                <div class="binding-entry-heading"><strong>{{ itemName(row.original.item_id) }}</strong><span class="binding-badge">{{ uncertain ? '保存前记录' : historical ? '历史记录' : '已关联' }}</span></div>
                <p class="binding-entry-range">{{ rangeLabel(row.original.selector) }}</p>
                <p v-if="row.original.supplier_sku" class="state-note">具体型号或货号：{{ row.original.supplier_sku }}</p>
                <details class="binding-entry-evidence"><summary>查看名称依据</summary><blockquote>{{ row.original.anchor.quote }}</blockquote></details>
                <div class="binding-actions">
                  <button class="text-button" type="button" :disabled="!!leaveAction" @click="preview(row.original.anchor.evidence_refs[0]!)">查看原件</button>
                  <div v-if="writable" class="binding-row-actions">
                    <span v-if="state !== 'unchanged'" class="state-note">{{ uncertain ? '本次保存结果待核实' : '下方有待保存修改' }}</span>
                    <template v-else>
                      <button class="text-button" type="button" data-test="binding-edit" :disabled="locked || formChanged" @click="edit(index)">修改关联</button>
                      <button class="text-button binding-danger" type="button" data-test="binding-remove" :disabled="locked || formChanged" @click="remove(index)">取消关联</button>
                    </template>
                  </div>
                </div>
              </template>
            </li>
          </ul>
        </section>

        <section v-if="changedRows.length" class="binding-group binding-pending" data-test="binding-pending" :aria-label="uncertain ? '本次提交的修改，保存结果待核实' : '本次待保存的修改'">
          <h4>{{ uncertain ? '本次提交的修改 · 保存结果待核实' : '本次待保存的修改' }} <span>{{ changedRows.length }} 项</span></h4>
          <p class="state-note">新增 {{ changeCounts.added }} 项 · 修改 {{ changeCounts.modified }} 项 · 取消 {{ changeCounts.removed }} 项。{{ uncertain ? '请求可能已经保存，请读取服务器记录核对。' : saving ? '正在保存，请等待处理结果。' : '尚未生效，保存后生效。' }}</p>
          <ul class="binding-entry-list">
            <li v-for="{ row, index, state } in changedRows" :key="row.key">
              <div class="binding-entry-heading"><strong>{{ itemName((row.entry ?? row.original)!.item_id) }}</strong><span class="binding-badge binding-badge-pending">{{ uncertain ? '保存待核实' : { added: '待新增', modified: '待修改', removed: '待取消', unchanged: '' }[state] }}</span></div>
              <p v-if="row.original" class="binding-entry-range">原关联：{{ itemName(row.original.item_id) }} · {{ rangeLabel(row.original.selector) }}</p>
              <p v-if="row.entry" class="binding-entry-range">{{ row.original ? '改为' : '使用范围' }}：{{ rangeLabel(row.entry.selector) }}</p>
              <p v-if="row.entry?.supplier_sku" class="state-note">具体型号或货号：{{ row.entry.supplier_sku }}</p>
              <details v-if="row.entry" class="binding-entry-evidence"><summary>查看本次名称依据</summary><blockquote>{{ row.entry.anchor.quote }}</blockquote></details>
              <div class="binding-row-actions">
                <button v-if="row.entry" class="text-button" type="button" data-test="binding-edit" :disabled="locked || formChanged" @click="edit(index)">继续修改</button>
                <button class="text-button" type="button" data-test="binding-undo" :disabled="locked || formChanged" @click="undo(index)">{{ state === 'added' ? '移除新增项' : '撤销本次修改' }}</button>
              </div>
            </li>
          </ul>
        </section>
        <p v-if="otherCount && dirty" class="state-note">保存时会保留其他文件的 {{ otherCount }} 个当前对应范围。</p>
        <button v-if="writable && !formOpen" class="button button-secondary binding-add" type="button" data-test="binding-add" :disabled="locked" @click="startAdding">{{ currentRows.length || changedRows.length ? '再关联一个商品或范围' : '添加商品关联' }}</button>

        <form v-if="writable && formOpen" data-test="binding-entry-form" @submit.prevent="stage">
          <fieldset :disabled="locked" class="binding-fields">
            <legend>{{ editingIndex === null ? '添加商品关联' : '修改商品关联' }}</legend>
            <label>采购商品<select v-model="draft.itemId" data-test="binding-item" class="field-control"><option value="">从已确认的审查清单中选择</option><option v-for="item in items" :key="item.item_id" :value="item.item_id">{{ item.name }}</option></select></label>
            <label>使用文件中的哪些内容<select v-model="draft.kind" data-test="binding-kind" class="field-control"><option value="whole">整份文件（只包含这一个商品）</option><option :value="document.original_name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'xlsx'">只使用指定{{ document.original_name.toLowerCase().endsWith('.pdf') ? '页面' : '表格区域' }}</option></select></label>
            <label v-if="draft.kind === 'whole'" class="binding-checkbox"><input v-model="draft.singleItem" data-test="binding-single" type="checkbox" />我已核对，整份文件只描述这个采购商品</label>
            <div v-if="draft.kind === 'xlsx'" class="binding-range-grid">
              <label class="binding-wide">工作表名称<input v-model="draft.sheet" data-test="binding-sheet" class="field-control" :list="`${titleId}-sheets`" maxlength="255" /><datalist :id="`${titleId}-sheets`"><option v-for="sheet in sheets" :key="sheet" :value="sheet" /></datalist></label>
              <label>从第几行开始<input v-model="draft.startRow" data-test="binding-start-row" class="field-control" inputmode="numeric" maxlength="7" /></label>
              <label>到第几行结束<input v-model="draft.endRow" data-test="binding-end-row" class="field-control" inputmode="numeric" maxlength="7" /></label>
              <label>从哪列开始<input v-model="draft.startColumn" data-test="binding-start-column" class="field-control" placeholder="例如 A" maxlength="3" /></label>
              <label>到哪列结束<input v-model="draft.endColumn" data-test="binding-end-column" class="field-control" placeholder="例如 C" maxlength="3" /></label>
            </div>
            <div v-if="draft.kind === 'pdf'" class="binding-range-grid">
              <label>从第几页开始<input v-model="draft.startPage" data-test="binding-start-page" class="field-control" inputmode="numeric" maxlength="2" /></label>
              <label>到第几页结束<input v-model="draft.endPage" data-test="binding-end-page" class="field-control" inputmode="numeric" maxlength="2" /></label>
            </div>
            <label>文件哪里写明了商品名称<select v-model="draft.anchorId" data-test="binding-anchor" class="field-control" @change="chooseAnchor()"><option value="">选择包含商品名称或明确型号的原文</option><option v-for="block in evidence" :key="block.id" :value="block.id">{{ location(block) }} · {{ block.source_text.slice(0, 70) }}</option></select></label>
            <label>引用内容（从原文带入，可缩短）<textarea v-model="draft.anchorQuote" data-test="binding-quote" class="field-control" maxlength="2000" rows="2" placeholder="保留连续原文，不可改写或拼接" /></label>
            <div class="binding-actions"><p class="state-note">用来确认商品名称；即使名称写在表头，也不会把表头之外的内容加入审查范围。</p><button v-if="draft.anchorId" class="text-button" type="button" data-test="binding-original" @click="preview(draft.anchorId)">查看依据原件</button></div>
            <details v-if="document.model_name" class="binding-advanced">
              <summary>核对上传时填写的商品信息</summary>
              <p class="state-note">上传时填写了「{{ document.model_name }}」。请说明它是商品名称还是具体型号；不清楚可保留“暂不确定”。</p>
              <label>这段信息表示什么<select v-model="draft.identifierClass" data-test="binding-identifier" class="field-control"><option value="unknown">暂不确定</option><option value="category">采购商品名称</option><option value="sku">供应商的具体型号或货号</option></select></label>
            </details>
            <details class="binding-advanced" :open="!!draft.sku || draft.identifierClass === 'sku'">
              <summary>具体型号或货号（选填）</summary>
              <p class="state-note">例如手机型号、服装货号；没有具体型号可留空。填写后需选择文件中的对应原文。</p>
              <label>具体型号或货号<input v-model="draft.sku" data-test="binding-sku" class="field-control" maxlength="120" placeholder="没有可留空" /></label>
              <template v-if="draft.sku.trim() || draft.identifierClass === 'sku'">
                <label>文件哪里写明了型号或货号<select v-model="draft.skuAnchorId" data-test="binding-sku-anchor" class="field-control" @change="chooseAnchor(true)"><option value="">选择型号或货号所在原文</option><option v-for="block in evidence" :key="block.id" :value="block.id">{{ location(block) }} · {{ block.source_text.slice(0, 70) }}</option></select></label>
                <label>型号或货号的引用内容<textarea v-model="draft.skuQuote" data-test="binding-sku-quote" class="field-control" maxlength="2000" rows="2" /></label>
              </template>
            </details>
            <details class="binding-preview">
              <summary>查看所选范围的文字</summary>
              <p class="state-note">已加载 {{ evidence.length }} 段，以下最多预览 50 段，不代表完整覆盖。多个商品请分别选择表格范围；同一页包含多个商品的 PDF 请先拆分。</p>
              <ul data-test="binding-range-preview"><li v-for="block in rangePreview" :key="block.id"><span>{{ location(block) }}</span><p>{{ block.source_text }}</p><button class="text-button" type="button" @click="preview(block.id)">查看原件</button></li></ul>
              <p v-if="!rangePreview.length" class="state-note">已加载内容中没有匹配文字，请核对范围或加载后续原文。</p>
            </details>
            <div class="binding-actions"><button class="button button-secondary" type="submit">{{ editingIndex === null ? '加入待保存修改' : '暂存本条修改' }}</button><button class="text-button" type="button" @click="resetDraft">取消本次填写</button></div>
          </fieldset>
        </form>
        <button v-if="more" class="text-button" type="button" data-test="binding-load-more" :disabled="paging || saving || !!leaveAction" @click="loadMore">{{ paging ? '读取中…' : '加载后续原文（每次100段）' }}</button>
        <p v-if="formChanged" class="state-note">请先暂存或取消当前填写，再修改其他关联。</p>
        <div v-if="writable && dirty && !uncertain" class="binding-confirmation">
          <label>修改说明<input v-model="reason" data-test="binding-reason" class="field-control" maxlength="1000" :disabled="locked" /></label>
          <p class="state-note">保存会生成新的关联版本。原审查结果保留为历史，需要重新发起审查。</p>
        </div>
        <details v-if="view.current_version" class="binding-history"><summary>确认记录 · 关联版本 {{ view.current_version.version }}</summary><p>{{ view.current_version.reason }}</p><p>确认时间：{{ new Date(view.current_version.confirmed_at).toLocaleString('zh-CN') }}</p></details>
        <details v-if="historicalEntries.length" class="binding-history">
          <summary>查看旧关联（仅供参考） · 版本 {{ view.historical_version!.version }}</summary>
          <p>旧关联不适用于当前资料或审查要求，不会自动加入本次修改。请重新核对填写。</p>
          <p>确认说明：{{ view.historical_version!.reason }} · {{ new Date(view.historical_version!.confirmed_at).toLocaleString('zh-CN') }}</p>
        </details>
      </template>
      <div v-if="originalTarget" ref="originalContainer" class="binding-original"><EvidencePanel :target="originalTarget" :width="390" @close="originalTarget = null" @settled="scrollOriginal" /></div>
    </section>
    <footer class="binding-dialog-footer">
      <div v-if="leaveAction" class="binding-leave-warning" data-test="binding-leave-warning" role="alertdialog" aria-modal="true" :aria-labelledby="leaveTitleId">
        <strong :id="leaveTitleId">{{ uncertain ? '保存结果还未核实' : '有尚未保存的内容' }}</strong>
        <p>{{ uncertain ? '请求可能已经保存。关闭或刷新不会取消已经发出的请求；请读取服务器记录核对，也可返回重试原保存请求。' : '本次修改尚未生效，直接关闭或刷新会丢失这些填写。' }}</p>
        <div class="binding-dialog-buttons"><button ref="keepEditingButton" class="button button-secondary" type="button" data-test="binding-keep-editing" @click="keepEditing">{{ uncertain ? '返回核对' : '继续编辑' }}</button><button class="button button-primary" type="button" data-test="binding-discard" @click="confirmLeave">{{ uncertain ? (leaveAction === 'refresh' ? '读取服务器记录' : '仍然关闭') : (leaveAction === 'refresh' ? '放弃修改并刷新' : '放弃修改并关闭') }}</button></div>
      </div>
      <template v-else>
        <div v-if="feedback" ref="feedbackContainer" class="binding-feedback" :class="`binding-feedback-${feedback.tone}`"
             :role="feedback.tone === 'error' || feedback.tone === 'warning' ? 'alert' : 'status'" aria-atomic="true" tabindex="-1">
          <strong>{{ feedback.title }}</strong><p>{{ feedback.message }}</p>
        </div>
        <template v-if="view && !loading && writable && dirty && !uncertain">
          <label class="binding-checkbox"><input v-model="confirmed" data-test="binding-confirm" type="checkbox" :disabled="locked || formChanged" />我已核对商品名称和文件范围，确认保存以上修改</label>
          <p v-if="formChanged" class="state-note">还有未加入清单的填写，请先暂存或取消，再保存。</p>
        </template>
        <div class="binding-dialog-buttons">
          <button class="text-button binding-refresh" type="button" data-test="binding-refresh" :disabled="loading || saving" @click="requestRefresh">刷新关联记录</button>
          <button v-if="uncertain" class="button button-secondary" type="button" data-test="binding-retry" :disabled="saving || busy || !writable" @click="save(true)">重试原保存请求</button>
          <button class="button button-secondary" type="button" :disabled="saving" @click="requestClose">{{ dirty || formChanged || uncertain ? '关闭' : '完成' }}</button>
          <button v-if="view && !loading && writable && dirty && !uncertain" class="button button-primary" type="button" data-test="binding-save" :disabled="!canSave" @click="save()">{{ saving ? '保存中…' : '确认保存修改' }}</button>
        </div>
      </template>
    </footer>
  </dialog>
</template>
