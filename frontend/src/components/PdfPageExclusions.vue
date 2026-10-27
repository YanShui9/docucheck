<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ChecklistItem, DocumentRecord, EvidenceRecord } from '../api'
import type { PdfPageExclusion } from '../itemBindings'

const props = defineProps<{ document: DocumentRecord; items: ChecklistItem[]; evidence: EvidenceRecord[];
  parser: string | null; complete: boolean; loading: boolean; writable: boolean; locked: boolean;
  formBlocked: boolean; uncertain: boolean; current: PdfPageExclusion[]; exclusions: PdfPageExclusion[];
  historical: PdfPageExclusion[]; readonlyHistory: boolean }>()
const emit = defineEmits<{ update: [entries: PdfPageExclusion[]]; draftChange: [changed: boolean];
  preview: [evidenceId: string]; loadAll: [] }>()
const open = ref(false)
const page = ref('')
const sku = ref('')
const anchorId = ref('')
const quote = ref('')
const reason = ref('')
const error = ref('')
const currentRows = computed(() => props.current.filter(entry => entry.document_id === props.document.id))
const pendingRows = computed(() => {
  const originals = currentRows.value
  const staged = props.exclusions.filter(entry => entry.document_id === props.document.id)
  return [...originals.filter(entry => !staged.some(candidate => candidate.page === entry.page)).map(entry => ({ entry, removed: true })),
    ...staged.filter(entry => !originals.some(candidate => candidate.page === entry.page)).map(entry => ({ entry, removed: false }))]
})
const historyRows = computed(() => props.historical.filter(entry => entry.document_id === props.document.id))
const buyerSkus = computed(() => props.items.map(item => {
  const match = /^(?:(?:型号|款号|SKU)\s*[:：]?\s*)?([A-Za-z0-9][A-Za-z0-9._-]{0,79})$/i.exec(item.specification?.trim() ?? '')
  return { item, sku: match?.[1] ?? '' }
}))
const buyerScopeReady = computed(() => buyerSkus.value.length > 0 && buyerSkus.value.every(item => !!item.sku))
const pageBlocks = computed(() => props.evidence.filter(block => block.page_idx === Number(page.value) - 1 && block.sheet === null))
const pages = computed(() => [...new Set(props.evidence.filter(block => block.page_idx !== null && block.sheet === null)
  .map(block => block.page_idx! + 1))].filter(value => value >= 1 && value <= 30).sort((a, b) => a - b))
const disabled = computed(() => props.locked || props.formBlocked)
watch([open, page, sku, anchorId, quote, reason], () => emit('draftChange', open.value))
watch(page, () => { anchorId.value = ''; quote.value = ''; error.value = '' })
function reset() { open.value = false; page.value = ''; sku.value = ''; anchorId.value = ''; quote.value = ''; reason.value = ''; error.value = '' }
function chooseAnchor() { quote.value = pageBlocks.value.find(block => block.id === anchorId.value)?.source_text ?? '' }
function remove(entry: PdfPageExclusion) {
  if (disabled.value || open.value) return
  emit('update', props.exclusions.filter(candidate => candidate.document_id !== entry.document_id || candidate.page !== entry.page))
}
function undo(entry: PdfPageExclusion, removed: boolean) {
  if (disabled.value || open.value) return
  if (removed) emit('update', [...props.exclusions, entry])
  else remove(entry)
}
function stage() {
  if (disabled.value || !props.complete || !buyerScopeReady.value) return
  error.value = ''
  const otherSku = sku.value.trim(); const pageNumber = Number(page.value)
  const anchor = pageBlocks.value.find(block => block.id === anchorId.value)
  if (!Number.isInteger(pageNumber) || pageNumber < 1 || pageNumber > 30 || !pageBlocks.value.length) error.value = '请选择有原文的单个 PDF 页面。'
  else if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(otherSku)) error.value = '其他型号须为不超过 80 字的字母、数字及 . _ - 组合。'
  else if (buyerSkus.value.some(entry => entry.sku.toLowerCase() === otherSku.toLowerCase())) error.value = '本次采购的目标型号不能排除。'
  else if (!anchor || !quote.value.trim() || quote.value.length > 2000 || !anchor.source_text.includes(quote.value) || !quote.value.toLowerCase().includes(otherSku.toLowerCase())) error.value = '请选择该页真实型号依据，连续摘录须包含所填型号且不超过 2000 字。'
  else if (!reason.value.trim() || reason.value.length > 1000) error.value = '请填写不超过 1000 字的排除原因。'
  else if (!props.parser || !/^[a-f0-9]{64}$/.test(props.document.source_sha256)) error.value = '解析信息不可用，请刷新资料。'
  else if (props.exclusions.some(entry => entry.document_id === props.document.id && entry.page === pageNumber)) error.value = '该页已经在排除范围内。'
  else if (props.exclusions.length >= 30) error.value = '排除页面不能超过 30 页。'
  else {
    emit('update', [...props.exclusions, { document_id: props.document.id, source_sha256: props.document.source_sha256,
      parser_version: props.parser, page: pageNumber, other_sku: otherSku,
      sku_anchor: { evidence_refs: [anchor.id], quote: quote.value }, reason: reason.value.trim() }])
    reset()
  }
}
</script>

<template>
  <section class="binding-group" aria-label="其他型号页面排除">
    <h4>其他型号页面</h4>
    <p class="state-note">仅可明确排除属于其他型号的 PDF 整页。混合采购内容、共同条款或适用关系不清的页面不能排除；最终由服务器核对。</p>
    <section v-if="currentRows.length" data-test="exclusion-current" class="binding-group">
      <h4>{{ uncertain ? '保存前读取的排除页' : readonlyHistory ? '该版本已确认的排除页' : '当前生效的排除页' }}</h4>
      <ul class="binding-entry-list"><li v-for="entry in currentRows" :key="entry.page">
        <div class="binding-entry-heading"><strong>第 {{ entry.page }} 页 · {{ entry.other_sku }}</strong><span class="binding-badge">已确认排除</span></div>
        <p class="state-note">{{ entry.reason }}</p><blockquote>{{ entry.sku_anchor.quote }}</blockquote>
        <div class="binding-actions"><button class="text-button" type="button" @click="emit('preview', entry.sku_anchor.evidence_refs[0]!)">查看依据原件</button>
          <button v-if="writable && exclusions.some(candidate => candidate.document_id === entry.document_id && candidate.page === entry.page)" data-test="exclusion-remove" class="text-button binding-danger" type="button" :disabled="disabled || open" @click="remove(entry)">取消排除</button></div>
      </li></ul>
    </section>
    <section v-if="pendingRows.length" data-test="exclusion-pending" class="binding-group binding-pending">
      <h4>{{ uncertain ? '本次排除修改 · 保存结果待核实' : '本次待保存的排除修改' }}</h4>
      <ul class="binding-entry-list"><li v-for="{ entry, removed } in pendingRows" :key="entry.page">
        <div class="binding-entry-heading"><strong>第 {{ entry.page }} 页 · {{ entry.other_sku }}</strong><span class="binding-badge binding-badge-pending">{{ removed ? '待取消' : '待新增' }}</span></div>
        <p class="state-note">{{ entry.reason }} · {{ uncertain ? '请求可能已经保存' : '保存后生效' }}</p><blockquote>{{ entry.sku_anchor.quote }}</blockquote>
        <div class="binding-actions"><button class="text-button" type="button" @click="emit('preview', entry.sku_anchor.evidence_refs[0]!)">查看依据原件</button>
          <button data-test="exclusion-undo" class="text-button" type="button" :disabled="disabled || open" @click="undo(entry, removed)">{{ removed ? '撤销取消' : '移除新增排除页' }}</button></div>
      </li></ul>
    </section>
    <details v-if="historyRows.length" data-test="exclusion-history" class="binding-history">
      <summary>上次排除页 · 需要重新确认</summary><p class="state-note">旧排除不适用于当前资料或审查清单，仅供参考。请按当前采购型号重新核对并填写。</p>
      <p v-for="entry in historyRows" :key="entry.page">第 {{ entry.page }} 页 · {{ entry.other_sku }} · {{ entry.reason }}</p>
    </details>
    <button v-if="writable && !open" data-test="exclusion-add" class="button button-secondary binding-add" type="button" :disabled="disabled" @click="open = true">排除其他型号页面</button>
    <form v-if="writable && open" data-test="exclusion-form" @submit.prevent="stage">
      <fieldset class="binding-fields" :disabled="disabled">
        <legend>排除其他型号页面</legend>
        <p class="state-note">本次已确认采购型号：<span v-for="entry in buyerSkus" :key="entry.item.item_id">{{ entry.item.name }} · {{ entry.sku || '尚无明确具体型号' }}；</span></p>
        <p v-if="!buyerScopeReady" class="state-note warning">当前清单须为每个采购品目明确具体型号，才能排除其他型号页面。</p>
        <p v-if="!complete" class="state-note warning">尚未读取完整原文，不能暂存排除页。请先读取所有后续原文并核对整页。</p>
        <button v-if="!complete" class="text-button" type="button" data-test="exclusion-load-all" :disabled="loading" @click="emit('loadAll')">{{ loading ? '正在读取完整原文…' : '读取完整原文' }}</button>
        <label>排除的单个页面<select v-model="page" data-test="exclusion-page" class="field-control"><option value="">选择一个页面</option><option v-for="value in pages" :key="value" :value="String(value)">第 {{ value }} 页</option></select></label>
        <details v-if="page" data-test="exclusion-page-text" class="binding-preview" open><summary>该页已解析原文{{ complete ? '' : '（尚未读全）' }}</summary><p v-for="block in pageBlocks" :key="block.id" class="exclusion-source">{{ block.source_text }}</p></details>
        <label>该页所属的其他型号<input v-model="sku" data-test="exclusion-sku" class="field-control" maxlength="80" /></label>
        <label>其他型号的原文依据<select v-model="anchorId" data-test="exclusion-anchor" class="field-control" @change="chooseAnchor"><option value="">选择该页明确声明型号的原文</option><option v-for="block in pageBlocks" :key="block.id" :value="block.id">{{ block.source_text.slice(0, 100) }}</option></select></label>
        <label>型号依据的连续原文<textarea v-model="quote" data-test="exclusion-quote" class="field-control" rows="2" maxlength="2000" /></label>
        <button v-if="anchorId" class="text-button" type="button" @click="emit('preview', anchorId)">查看依据原件</button>
        <label>排除原因<textarea v-model="reason" data-test="exclusion-reason" class="field-control" rows="2" maxlength="1000" /></label>
        <p v-if="error" class="state-note warning" role="alert">{{ error }}</p>
        <div class="binding-actions"><button data-test="exclusion-stage" class="button button-secondary" type="submit" :disabled="!complete || !buyerScopeReady">加入待保存修改</button><button class="text-button" type="button" data-test="exclusion-cancel" @click="reset">取消本次填写</button></div>
      </fieldset>
    </form>
  </section>
</template>

<style scoped>
.exclusion-source { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; line-height: 1.7; }
</style>
