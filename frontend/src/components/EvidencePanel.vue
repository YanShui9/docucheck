<script setup lang="ts">
import { nextTick, onUnmounted, ref, watch } from 'vue'
import * as pdfjs from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { accessToken } from '../auth'
import { apiRequest, readOriginal } from '../api'
import type { DocumentRecord, DocumentsResult, EvidenceRecord } from '../api'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

const props = defineProps<{
  target: { caseId: string; snapshotId: string; evidenceId: string } | null
  width: number
}>()
const emit = defineEmits<{ close: []; resize: [width: number]; settled: [] }>()
const evidence = ref<EvidenceRecord | null>(null)
const document = ref<DocumentRecord | null>(null)
const original = ref<Blob | null>(null)
const loading = ref(false)
const error = ref('')
const canvas = ref<HTMLCanvasElement | null>(null)
const page = ref(1)
const pageCount = ref(0)
const sheetWindow = ref<{ sheet: string; cell: string; rows: {
  number: number; cells: { address: string; text: string; highlight: boolean }[]
}[] } | null>(null)
let pdf: pdfjs.PDFDocumentProxy | null = null
let pdfTask: pdfjs.PDFDocumentLoadingTask | null = null
let renderTask: pdfjs.RenderTask | null = null
let sequence = 0
let renderVersion = 0

async function renderPage() {
  const current = sequence
  const currentRender = ++renderVersion
  if (!pdf || !evidence.value || evidence.value.page_idx === null) return
  await nextTick()
  const surface = canvas.value
  if (!surface) return
  renderTask?.cancel()
  try { await renderTask?.promise } catch { /* A newer page replaced this render. */ }
  if (current !== sequence || currentRender !== renderVersion || !pdf || !canvas.value) return
  const pdfPage = await pdf.getPage(page.value)
  if (current !== sequence || currentRender !== renderVersion) return
  const initial = pdfPage.getViewport({ scale: 1 })
  const scale = Math.min(2, Math.max(0.6, 620 / initial.width))
  const viewport = pdfPage.getViewport({ scale })
  const ratio = Math.min(window.devicePixelRatio || 1, 2)
  surface.width = Math.round(viewport.width * ratio)
  surface.height = Math.round(viewport.height * ratio)
  surface.style.width = `${viewport.width}px`
  surface.style.height = `${viewport.height}px`
  const context = surface.getContext('2d')
  if (!context) throw new Error('canvas_unavailable')
  renderTask = pdfPage.render({ canvas: surface, canvasContext: context, viewport,
    transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] })
  await renderTask.promise
}

async function safelyRenderPage() {
  const current = sequence
  try { await renderPage() } catch (reason) {
    if (current !== sequence) return
    if (reason instanceof Error && reason.name === 'RenderingCancelledException') return
    error.value = reason instanceof Error ? reason.message : String(reason)
  }
}

function changePage(delta: number) {
  page.value = Math.min(pageCount.value, Math.max(1, page.value + delta))
  void safelyRenderPage()
}

async function loadEvidence() {
  const target = props.target
  const current = ++sequence
  renderVersion += 1
  renderTask?.cancel()
  renderTask = null
  if (pdfTask) { void pdfTask.destroy(); pdfTask = null; pdf = null }
  evidence.value = null
  document.value = null
  original.value = null
  sheetWindow.value = null
  pageCount.value = 0
  error.value = ''
  if (!target) return
  loading.value = true
  try {
    const [record, documents] = await Promise.all([
      apiRequest<EvidenceRecord>(
        `/cases/${target.caseId}/snapshots/${target.snapshotId}/evidence/${target.evidenceId}`,
        accessToken),
      apiRequest<DocumentsResult>(
        `/cases/${target.caseId}/snapshots/${target.snapshotId}/documents`, accessToken),
    ])
    if (current !== sequence) return
    const found = documents.documents.find(item => item.id === record.document_id)
    if (!found) throw new Error('document_not_found')
    evidence.value = record
    document.value = found
    const blob = await readOriginal(target.caseId, target.snapshotId, found.id, accessToken)
    if (current !== sequence) return
    original.value = blob
    if (record.page_idx !== null) {
      const bytes = new Uint8Array(await blob.arrayBuffer())
      if (current !== sequence) return
      const task = pdfjs.getDocument({ data: bytes })
      pdfTask = task
      const loaded = await task.promise
      if (current !== sequence) { void task.destroy(); return }
      pdf = loaded
      pageCount.value = loaded.numPages
      page.value = Math.min(record.page_idx + 1, loaded.numPages)
    } else if (record.sheet && record.cell) {
      const query = new URLSearchParams({ sheet: record.sheet, cell: record.cell })
      const windowResult = await apiRequest<typeof sheetWindow.value>(
        `/cases/${target.caseId}/snapshots/${target.snapshotId}/documents/`
          + `${found.id}/original/sheet-window?${query}`, accessToken)
      if (current !== sequence) return
      sheetWindow.value = windowResult
    }
  } catch (reason) {
    if (current === sequence) error.value = reason instanceof Error ? reason.message : '无法读取原件'
  } finally {
    if (current === sequence) {
      loading.value = false
      if (pdf) void safelyRenderPage()
      await nextTick()
      if (current === sequence) emit('settled')
    }
  }
}

function download() {
  if (!original.value || !document.value) return
  const url = URL.createObjectURL(original.value)
  const link = window.document.createElement('a')
  link.href = url
  link.download = document.value.original_name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function highlightStyle(): Record<string, string> {
  const box = evidence.value?.bbox
  if (!box || evidence.value?.page_idx !== page.value - 1) return { display: 'none' }
  return { left: `${box[0] * 100}%`, top: `${box[1] * 100}%`,
    width: `${(box[2] - box[0]) * 100}%`, height: `${(box[3] - box[1]) * 100}%` }
}

watch(() => props.target, () => { void loadEvidence() }, { immediate: true })
onUnmounted(() => { sequence++; renderVersion++; renderTask?.cancel(); if (pdfTask) void pdfTask.destroy() })
</script>

<template>
  <aside class="context-panel evidence-panel" aria-label="原文证据">
    <div class="evidence-head"><div><span class="eyebrow">原文证据</span><h2>来源核对</h2></div>
      <button v-if="target" class="text-button" type="button" aria-label="关闭证据面板" @click="emit('close')">关闭</button>
    </div>
    <label class="panel-width">面板宽度
      <input type="range" min="320" max="650" step="10" :value="width"
             @input="emit('resize', Number(($event.target as HTMLInputElement).value))" />
    </label>
    <div v-if="!target" class="evidence-empty"><strong>选择一处来源</strong><p>点击矩阵中的“查看来源”，可在这里对照原件和快照位置。</p></div>
    <div v-else-if="loading" class="loading-state" role="status">正在验证并读取原件…</div>
    <div v-else-if="error" class="alert" role="alert">原件读取失败：{{ error }}</div>
    <template v-else-if="evidence && document">
      <div class="evidence-meta"><strong>{{ document.original_name }}</strong>
        <span>快照 {{ target.snapshotId.slice(0, 8) }} · SHA-256 {{ document.source_sha256.slice(0, 12) }}…</span>
        <span v-if="evidence.page_idx !== null">PDF 第 {{ evidence.page_idx + 1 }} 页</span>
        <span v-else>工作表 {{ evidence.sheet }} · 单元格 {{ evidence.cell }}</span>
      </div>
      <blockquote class="evidence-quote">{{ evidence.source_text }}</blockquote>
      <button class="button button-secondary" type="button" :disabled="!original" @click="download">下载已验证原件</button>
      <template v-if="evidence.page_idx !== null && pdf">
        <div class="page-controls"><button type="button" class="text-button" :disabled="page <= 1" @click="changePage(-1)">上一页</button>
          <span>第 {{ page }} / {{ pageCount }} 页</span>
          <button type="button" class="text-button" :disabled="page >= pageCount" @click="changePage(1)">下一页</button></div>
        <div class="pdf-scroll"><div class="pdf-page"><canvas ref="canvas" aria-label="PDF 原件页面" />
          <div class="pdf-highlight" :style="highlightStyle()" aria-hidden="true" /></div></div>
        <p class="context-help">框线标示解析器给出的来源区域；请以原文核对为准。</p>
      </template>
      <div v-else class="sheet-location"><strong>{{ evidence.sheet }}!{{ evidence.cell }}</strong>
        <span>下表从已验证 XLSX 原件读取；高亮的是该证据的原始单元格。</span>
        <table v-if="sheetWindow" class="sheet-window" aria-label="原件单元格邻域">
          <tbody><tr v-for="row in sheetWindow.rows" :key="row.number">
            <th scope="row">{{ row.number }}</th>
            <td v-for="cell in row.cells" :key="cell.address"
                :class="{ 'sheet-cell-target': cell.highlight }"
                :title="cell.address"><small>{{ cell.address }}</small>{{ cell.text }}</td>
          </tr></tbody>
        </table>
      </div>
    </template>
  </aside>
</template>
