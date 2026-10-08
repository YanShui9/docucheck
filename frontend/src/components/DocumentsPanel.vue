<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  CaseRecord, DocumentRecord, DocumentsResult, SnapshotRecord,
} from '../api'

const props = defineProps<{
  selectedCase: CaseRecord | null
  documents: DocumentsResult | null
  snapshots: SnapshotRecord[]
  nextSnapshotBefore: number | null
  loadingOlder: boolean
  selectedHistoryId: string | null
  historyDocuments: DocumentsResult | null
  canWrite: boolean
  busy: boolean
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
      <span v-if="documents" class="revision-tag">资料版本 {{ documents.revision }}</span>
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
          <span>{{ shownDocuments?.documents.length ?? 0 }} 份</span>
        </div>
        <button class="text-button" type="button" :disabled="busy"
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
        正在查看历史版本 {{ historyDocuments?.revision ?? '…' }}。这里仅供追溯；上传和审查始终使用当前版本。
      </p>
      <button v-if="nextSnapshotBefore !== null" class="text-button history-more"
              type="button" :disabled="loadingOlder"
              @click="emit('loadOlderSnapshots')">
        {{ loadingOlder ? '读取中…' : '加载更早版本' }}
      </button>
      <div v-if="isHistorical && !historyDocuments" class="loading-state" role="status">
        正在读取历史资料…
      </div>
      <div v-else-if="!shownDocuments?.documents.length" class="empty-state compact">
        <strong>还没有资料</strong>
        <span>上传采购需求后即可继续添加供应商资料。</span>
      </div>
      <ul v-else class="document-list">
        <li v-for="document in shownDocuments?.documents" :key="document.id" class="document-row">
          <div class="document-icon" aria-hidden="true">{{ document.original_name.toLowerCase().endsWith('.pdf') ? 'PDF' : 'XLS' }}</div>
          <div class="document-details">
            <strong>{{ document.original_name }}</strong>
            <span>{{ roleLabel(document.role) }}<template v-if="document.supplier_handle"> · {{ document.supplier_handle }}</template><template v-if="document.model_name"> · 产品标识 {{ document.model_name }}</template><template v-else-if="document.supplier_handle"> · 未设置产品标识</template></span>
          </div>
          <span class="status-pill" :class="`status-${document.parse_status}`">
            {{ statusLabel(document.parse_status) }}
          </span>
        </li>
      </ul>
      <p v-if="!isHistorical && documents?.documents.some(item => item.parse_status === 'pending')" class="state-note">
        有资料等待后台解析。请确认解析服务已启动；页面会自动刷新状态。
      </p>
      <p v-if="!isHistorical && documents?.documents.some(item => item.parse_status === 'partial' || item.parse_status === 'failed')" class="state-note warning">
        部分资料尚未完整解析，后续审查不能把它们当作完整证据。
      </p>
    </template>
  </section>
</template>
