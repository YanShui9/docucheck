<script setup lang="ts">
import { computed, defineAsyncComponent, onUnmounted, ref, watch } from 'vue'
import { accessToken } from '../auth'
import { fetchItemBindings } from '../itemBindings'
import type { ItemBindingView } from '../itemBindings'
import type {
  CaseRecord, DocumentRecord, DocumentsResult, SnapshotRecord,
} from '../api'

const DocumentItemBindings = defineAsyncComponent(() => import('./DocumentItemBindings.vue'))

const props = defineProps<{
  selectedCase: CaseRecord | null
  documents: DocumentsResult | null
  snapshots: SnapshotRecord[]
  nextSnapshotBefore: number | null
  loadingOlder: boolean
  selectedHistoryId: string | null
  historyDocuments: DocumentsResult | null
  historyLoading: boolean
  historyError: string
  canWrite: boolean
  busy: boolean
  refreshKey?: number
  bindingRefreshKey?: number
  notifyBindingsConfirmed?: (target: { caseId: string; snapshotId: string }) => void
}>()

const emit = defineEmits<{
  upload: [payload: {
    file: File
    role: DocumentRecord['role']
    supplierHandle: string
    supplierName: string
    modelName: string
  }]
  selectHistory: [snapshotId: string | null]
  loadOlderSnapshots: []
  refreshDocuments: []
  bindingsConfirmed: [target: { caseId: string; snapshotId: string }]
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const role = ref<DocumentRecord['role']>('requirement')
const supplierHandle = ref('')
const supplierName = ref('')
const modelName = ref('')
const localError = ref('')
const needsSupplier = computed(() => role.value !== 'requirement')
const shownDocuments = computed(() =>
  props.selectedHistoryId ? props.historyDocuments : props.documents)
const isHistorical = computed(() => props.selectedHistoryId !== null)
const shownRevision = computed(() => isHistorical.value
  ? props.snapshots.find(snapshot => snapshot.id === props.selectedHistoryId)?.revision ?? props.historyDocuments?.revision ?? '…'
  : props.documents?.revision)
const bindingView = ref<ItemBindingView | null>(null)
const bindingLoading = ref(false)
const bindingError = ref(false)
const bindingDocumentId = ref<string | null>(null)
const bindingDocument = computed(() => shownDocuments.value?.documents.find(document => document.id === bindingDocumentId.value) ?? null)
let bindingGeneration = 0

async function refreshBindings() {
  const current = ++bindingGeneration
  const data = shownDocuments.value
  bindingView.value = null; bindingError.value = false; bindingLoading.value = false
  if (!data?.snapshot_id || !data.documents.some(document => document.role !== 'requirement') || !props.selectedCase) return
  bindingLoading.value = true
  try {
    const result = await fetchItemBindings(props.selectedCase.id, data.snapshot_id, accessToken)
    if (current === bindingGeneration) {
      if (result.case_id !== props.selectedCase?.id || result.snapshot_id !== data.snapshot_id) throw new Error('binding_scope')
      bindingView.value = result
    }
  } catch { if (current === bindingGeneration) bindingError.value = true }
  finally { if (current === bindingGeneration) bindingLoading.value = false }
}
function bindingLabel(documentId: string): string {
  if (bindingLoading.value) return '正在读取对应状态…'
  if (bindingError.value) return '对应状态暂时无法读取'
  if (isHistorical.value || bindingView.value?.state === 'stale') return '历史对应关系，仅供追溯'
  const count = bindingView.value?.current_version?.payload.entries.filter(entry => entry.document_id === documentId).length ?? 0
  return count ? `已确认 ${count} 个对应范围` : '尚未确认对应品目'
}
function bindingsConfirmed(target: { caseId: string; snapshotId: string }) {
  if (target.caseId !== props.selectedCase?.id || target.snapshotId !== props.documents?.snapshot_id) return
  void refreshBindings()
  emit('bindingsConfirmed', target)
}
watch([() => props.selectedCase?.id, () => shownDocuments.value?.snapshot_id, () => shownDocuments.value?.revision,
  () => props.refreshKey],
() => { bindingDocumentId.value = null; void refreshBindings() }, { immediate: true })
watch(() => shownDocuments.value?.documents.map(document => document.parse_status).join(','), () => { void refreshBindings() })
watch(() => props.bindingRefreshKey, () => { void refreshBindings() })
onUnmounted(() => { bindingGeneration++ })

watch(() => props.documents?.revision, (revision, previous) => {
  if (revision !== previous && fileInput.value) fileInput.value.value = ''
})

function changeHistory(event: Event) {
  const target = event.target as HTMLSelectElement
  emit('selectHistory', target.value || null)
}

function submitUpload() {
  localError.value = ''
  const file = fileInput.value?.files?.[0]
  if (!file) {
    localError.value = '请选择 PDF 或 XLSX 文件。'
    return
  }
  if (needsSupplier.value && (!supplierHandle.value.trim() ||
      !supplierName.value.trim())) {
    localError.value = '供应商资料请填写供应商编号和名称。'
    return
  }
  emit('upload', {
    file, role: role.value,
    supplierHandle: supplierHandle.value,
    supplierName: supplierName.value,
    modelName: modelName.value,
  })
}

const statusLabels: Record<string, string> = {
  pending: '待解析', processing: '解析中', complete: '解析完成',
  partial: '部分解析', failed: '解析失败',
}
const roleLabels: Record<string, string> = {
  requirement: '需求或订单', quote: '供应商报价', spec: '产品说明或资质',
}

function statusLabel(status: string): string {
  return statusLabels[status] ?? status
}

function roleLabel(value: string): string {
  return roleLabels[value] ?? value
}
</script>

<template>
  <section class="content-card" aria-labelledby="documents-title">
    <div class="section-head">
      <div>
        <span class="eyebrow">资料准备</span>
        <h2 id="documents-title">上传采购资料</h2>
      </div>
      <span v-if="shownRevision !== undefined" class="revision-tag">资料版本 {{ shownRevision }}</span>
    </div>
    <p class="section-intro">先上传需求单或订单，再添加供应商报价、产品说明及资质。每次上传都会保存新的资料版本，原件保留以便核对。</p>

    <div v-if="!selectedCase" class="empty-state">
      <strong>先创建或选择采购任务</strong>
      <span>资料、对话和审查结果会保存在所选采购任务中。</span>
    </div>
    <template v-else>
      <p v-if="!canWrite" class="state-note">当前项目为只读权限，不能上传或替换资料。</p>
      <form v-else class="upload-grid" @submit.prevent="submitUpload">
        <div class="form-field">
          <label for="document-role">资料类型</label>
          <select id="document-role" v-model="role" class="field-control" :disabled="busy">
            <option value="requirement">需求单或订单</option>
            <option value="quote">供应商报价</option>
            <option value="spec">产品说明或资质</option>
          </select>
        </div>
        <div v-if="needsSupplier" class="form-field">
          <label for="supplier-handle">供应商编号</label>
          <input id="supplier-handle" v-model="supplierHandle" class="field-control"
                 placeholder="例如 vendor-a，使用字母、数字或短横线" maxlength="64" :disabled="busy" />
        </div>
        <div v-if="needsSupplier" class="form-field">
          <label for="supplier-name">供应商名称</label>
          <input id="supplier-name" v-model="supplierName" class="field-control"
                 placeholder="例如 供应商 A" maxlength="255" :disabled="busy" />
        </div>
        <div v-if="needsSupplier" class="form-field">
          <label for="model-name">产品标识（选填）</label>
          <input id="model-name" v-model="modelName" class="field-control"
                 placeholder="例如品种、款号或型号；同一产品保持一致" maxlength="120" :disabled="busy" />
        </div>
        <div class="form-field file-field">
          <label for="document-file">PDF / XLSX 文件</label>
          <input id="document-file" ref="fileInput" type="file" class="field-control file-control"
                 accept=".pdf,.xlsx,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                 :disabled="busy" />
        </div>
        <div class="upload-actions">
          <button class="button button-primary" type="submit" :disabled="busy">
            {{ busy ? '正在上传…' : '上传并解析' }}
          </button>
          <span>单文件上限 10 MiB</span>
        </div>
        <p v-if="localError" class="form-error" role="alert">{{ localError }}</p>
      </form>

      <div class="document-list-head">
        <div>
          <h3>{{ isHistorical ? '历史资料' : '当前资料' }}</h3>
          <span>{{ shownDocuments?.documents.length ?? '—' }} 份</span>
        </div>
        <button class="text-button" type="button" :disabled="busy || (isHistorical && historyLoading)"
                @click="emit('refreshDocuments')">刷新状态</button>
        <label v-if="snapshots.length" class="history-picker">
          <span>资料版本</span>
          <select class="field-control" :value="selectedHistoryId ?? ''"
                  @change="changeHistory">
            <option value="">当前版本 {{ documents?.revision ?? 0 }}</option>
            <option v-for="snapshot in snapshots.filter(item => item.revision !== documents?.revision)"
                    :key="snapshot.id" :value="snapshot.id">
              历史版本 {{ snapshot.revision }} · {{ snapshot.document_count }} 份
            </option>
          </select>
        </label>
      </div>
      <p v-if="isHistorical" class="history-notice">
        正在查看历史版本 {{ shownRevision }}。这里仅供追溯；上传和审查始终使用当前版本。
      </p>
      <button v-if="nextSnapshotBefore !== null" class="text-button history-more"
              type="button" :disabled="loadingOlder"
              @click="emit('loadOlderSnapshots')">
        {{ loadingOlder ? '读取中…' : '加载更早版本' }}
      </button>
      <div v-if="isHistorical && historyLoading" class="loading-state" role="status">
        正在读取历史资料…
      </div>
      <div v-else-if="isHistorical && historyError" class="alert" role="alert">
        <span>{{ historyError }}</span>
        <button class="text-button" type="button" :disabled="busy" @click="emit('refreshDocuments')">重试</button>
        <button class="text-button" type="button" @click="emit('selectHistory', null)">返回当前版本</button>
      </div>
      <div v-else-if="!shownDocuments?.documents.length" class="empty-state compact">
        <strong>{{ isHistorical ? '该历史版本没有资料' : '还没有资料' }}</strong>
        <span v-if="!isHistorical">上传采购需求后即可继续添加供应商资料。</span>
      </div>
      <ul v-else class="document-list">
        <li v-for="document in shownDocuments?.documents" :key="document.id" class="document-row">
          <div class="document-icon" aria-hidden="true">{{ document.original_name.toLowerCase().endsWith('.pdf') ? 'PDF' : 'XLS' }}</div>
          <div class="document-details">
            <strong>{{ document.original_name }}</strong>
            <span>{{ roleLabel(document.role) }}<template v-if="document.supplier_handle"> · {{ document.supplier_handle }}</template><template v-if="document.model_name"> · 产品标识 {{ document.model_name }}</template><template v-else-if="document.supplier_handle"> · 未设置产品标识</template></span>
            <span v-if="document.role !== 'requirement'">{{ bindingLabel(document.id) }}</span>
            <span v-else>买方资料用于整理采购要求</span>
            <button v-if="document.role !== 'requirement'" class="text-button binding-document-action" type="button" :disabled="busy"
                    @click="bindingDocumentId = document.id">{{ isHistorical || !canWrite ? '查看品目对应' : '关联采购品目' }}</button>
          </div>
          <span class="status-pill" :class="`status-${document.parse_status}`">
            {{ statusLabel(document.parse_status) }}
          </span>
        </li>
      </ul>
      <DocumentItemBindings v-if="bindingDocument && shownDocuments?.snapshot_id && selectedCase"
                            :key="`${selectedCase.id}/${shownDocuments.snapshot_id}/${bindingDocument.id}`"
                            :case-id="selectedCase.id" :snapshot-id="shownDocuments.snapshot_id"
                            :revision="shownDocuments.revision" :document="bindingDocument" :can-write="canWrite" :busy="busy"
                            :historical="isHistorical" :refresh-key="refreshKey ?? 0"
                            :notify-confirmed="notifyBindingsConfirmed"
                            @close="bindingDocumentId = null" @confirmed="bindingsConfirmed" />
      <p v-if="!isHistorical && documents?.documents.some(item => item.parse_status === 'pending')" class="state-note">
        有资料等待后台解析。请确认解析服务已启动；页面会自动刷新状态。
      </p>
      <p v-if="!isHistorical && documents?.documents.some(item => item.parse_status === 'partial' || item.parse_status === 'failed')" class="state-note warning">
        部分资料尚未完整解析，后续审查不能把它们当作完整证据。
      </p>
    </template>
  </section>
</template>
