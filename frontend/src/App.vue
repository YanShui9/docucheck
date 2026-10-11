<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref } from 'vue'
import { accessToken, identity, signOut } from './auth'
import { apiRequest, uploadDocument } from './api'
import type {
  CaseRecord, DocumentRecord, DocumentsResult,
  Project, SnapshotRecord, SnapshotsResult,
} from './api'
import type { ChecklistTarget } from './checklistCandidate'
import CaseSidebar from './components/CaseSidebar.vue'
import DocumentsPanel from './components/DocumentsPanel.vue'
import AgentWorkbench from './components/AgentWorkbench.vue'
import ChatWorkspace from './components/ChatWorkspace.vue'
const EvidencePanel = defineAsyncComponent(() => import('./components/EvidencePanel.vue'))
const ChecklistPanel = defineAsyncComponent(() => import('./components/ChecklistPanel.vue'))
const ReviewPanel = defineAsyncComponent(() => import('./components/ReviewPanel.vue'))
const MemorySettings = defineAsyncComponent(() => import('./components/MemorySettings.vue'))

const projects = ref<Project[]>([])
const cases = ref<CaseRecord[]>([])
const selectedProjectId = ref<string | null>(null)
const selectedCaseId = ref<string | null>(null)
const documents = ref<DocumentsResult | null>(null)
const snapshots = ref<SnapshotRecord[]>([])
const nextSnapshotBefore = ref<number | null>(null)
const loadingOlder = ref(false)
const selectedHistoryId = ref<string | null>(null)
const historyDocuments = ref<DocumentsResult | null>(null)
const busy = ref(false)
const loading = ref(true)
const error = ref('')
const activeTab = ref<'documents' | 'agent'>('documents')
const panel = ref<'documents' | 'agent' | 'checklist' | 'review' | null>(null)
const sidebarOpen = ref(window.innerWidth > 760)
const taskTitles = ref<Record<string, string>>({})
const evidenceTarget = ref<{ caseId: string; snapshotId: string; evidenceId: string } | null>(null)
const evidenceWidth = ref(390)
const checklistTarget = ref<ChecklistTarget | null>(null)
const checklistChanges = ref<Record<string, number>>({})
const bindingChanges = ref<Record<string, number>>({})
const reviewTarget = ref<{ caseId: string; snapshotId: string; runId: string } | null>(null)
const pendingReportCorrections = ref<Record<string, { caseId: string; snapshotId: string; count: number }>>({})
const memoryOpen = ref(false)
let refreshTimer: ReturnType<typeof setInterval> | undefined
let selectionVersion = 0
let preparingCase: Promise<string | null> | null = null

const checklistSnapshotId = computed(() =>
  checklistTarget.value?.snapshotId ?? documents.value?.snapshot_id ?? snapshots.value[0]?.id ?? null)
const supplierNames = computed(() => Object.fromEntries(
  (documents.value?.documents ?? [])
    .filter((item): item is DocumentRecord & { supplier_handle: string; supplier_name: string } =>
      Boolean(item.supplier_handle && item.supplier_name))
    .map(item => [item.supplier_handle, item.supplier_name])))
const panelTitles: Record<string, string> = {
  documents: '采购资料', agent: '历史核验', checklist: '审查要求', review: '审查报告',
}

function newTask() {
  ++selectionVersion
  selectedCaseId.value = null; documents.value = null; snapshots.value = []
  nextSnapshotBefore.value = null; selectedHistoryId.value = null; historyDocuments.value = null
  evidenceTarget.value = null; panel.value = null; error.value = ''
  checklistTarget.value = null; reviewTarget.value = null
}

async function prepareCase(): Promise<string | null> {
  if (selectedCaseId.value) return selectedCaseId.value
  if (preparingCase) return preparingCase
  const projectId = selectedProjectId.value
  const version = selectionVersion
  if (!projectId || !canWrite.value || busy.value) return null
  preparingCase = (async () => {
    busy.value = true
    try {
      const created = await apiRequest<CaseRecord>('/cases', accessToken, { method: 'POST', body: JSON.stringify({ project_id: projectId }) })
      if (version !== selectionVersion) return null
      cases.value = [created, ...cases.value]
      await selectCase(created.id)
      return selectedCaseId.value === created.id && selectionVersion === version + 1 ? created.id : null
    } catch (reason) { if (version === selectionVersion) error.value = friendlyError(reason); return null }
    finally { busy.value = false; preparingCase = null }
  })()
  return preparingCase
}

async function openDocuments() {
  if (!selectedCaseId.value && !await prepareCase()) return
  panel.value = 'documents'; activeTab.value = 'documents'; evidenceTarget.value = null
}

function openChecklistPanel() {
  if (!selectedCaseId.value || !checklistSnapshotId.value) return
  checklistTarget.value = null
  panel.value = 'checklist'; evidenceTarget.value = null
}

function openChecklistFromChat(target: ChecklistTarget) {
  if (target.caseId !== selectedCaseId.value) return
  checklistTarget.value = target
  panel.value = 'checklist'; evidenceTarget.value = null
}
function checklistChanged(target: { caseId: string; snapshotId: string }) {
  if (target.caseId !== selectedCaseId.value) return
  checklistChanges.value = { ...checklistChanges.value,
    [target.snapshotId]: (checklistChanges.value[target.snapshotId] ?? 0) + 1 }
}
function bindingsChanged(target: { caseId: string; snapshotId: string }) {
  if (target.caseId !== selectedCaseId.value || target.snapshotId !== documents.value?.snapshot_id) return
  bindingChanges.value = { ...bindingChanges.value,
    [target.snapshotId]: (bindingChanges.value[target.snapshotId] ?? 0) + 1 }
}

function openReviewFromChat(target: { caseId: string; snapshotId: string; runId: string }) {
  if (target.caseId !== selectedCaseId.value) return
  reviewTarget.value = target
  panel.value = 'review'; evidenceTarget.value = null
}

function trackReportCorrection(target: { caseId: string; snapshotId: string; runId: string }, completion: Promise<unknown>) {
  if (target.caseId !== selectedCaseId.value || target.runId !== reviewTarget.value?.runId
    || target.snapshotId !== reviewTarget.value?.snapshotId) return
  pendingReportCorrections.value = { ...pendingReportCorrections.value, [target.runId]: {
    ...target, count: (pendingReportCorrections.value[target.runId]?.count ?? 0) + 1,
  } }
  const reload = () => {
    const next = { ...pendingReportCorrections.value }
    const record = next[target.runId]
    if (!record) return
    next[target.runId] = { ...record, count: Math.max(0, record.count - 1) }
    // Keep completed entries: a fast start/end may be batched into one child update.
    pendingReportCorrections.value = next
  }
  void completion.then(reload, reload)
}

const selectedCase = computed(() => cases.value.find(
  item => item.id === selectedCaseId.value,
) ?? null)
const selectedProject = computed(() => projects.value.find(
  item => item.id === selectedProjectId.value,
) ?? null)
const canWrite = computed(() => selectedProject.value?.role === 'owner' ||
  selectedProject.value?.role === 'editor')
const displayName = computed(() => String(
  identity.tokenParsed?.preferred_username || identity.tokenParsed?.name || '已登录用户',
))

function friendlyError(reason: unknown): string {
  const code = reason instanceof Error ? reason.message : 'request_failed'
  return ({
    not_found: '没有找到该资源，或当前账号无权访问。',
    revision_conflict: '资料已更新，请刷新后重试。',
    invalid_supplier_binding: '供应商标识或名称与已有资料不一致。',
    file_limit: '文件超过上传大小限制。',
    invalid_file: '无法读取该文件，请确认是有效的 PDF 或 XLSX。',
    unsupported_file: '仅支持 PDF 和 XLSX。',
    session_expired: '登录已过期，请重新登录。',
  } as Record<string, string>)[code] ?? `操作未完成（${code}）。`
}

async function loadProjects() {
  const response = await apiRequest<{ projects: Project[] }>('/projects', accessToken)
  projects.value = response.projects
  const nextId = projects.value.some(item => item.id === selectedProjectId.value)
    ? selectedProjectId.value : projects.value[0]?.id ?? null
  if (nextId) await selectProject(nextId)
  else {
    selectedProjectId.value = null
    selectedCaseId.value = null
    cases.value = []
    documents.value = null
    snapshots.value = []
    nextSnapshotBefore.value = null
    loadingOlder.value = false
    selectedHistoryId.value = null
    historyDocuments.value = null
  }
}

async function selectProject(projectId: string) {
  const version = ++selectionVersion
  selectedProjectId.value = projectId
  selectedCaseId.value = null
  cases.value = []
  documents.value = null
  snapshots.value = []
  nextSnapshotBefore.value = null
  loadingOlder.value = false
  selectedHistoryId.value = null
  historyDocuments.value = null
  error.value = ''
  evidenceTarget.value = null
  panel.value = null
  checklistTarget.value = null; reviewTarget.value = null
  try {
    const response = await apiRequest<{ cases: CaseRecord[] }>(
      `/projects/${projectId}/cases`, accessToken,
    )
    if (version !== selectionVersion) return
    cases.value = response.cases
    if (cases.value.length) await selectCase(cases.value[0].id)
  } catch (reason) {
    if (version === selectionVersion) error.value = friendlyError(reason)
  }
}

async function selectCase(caseId: string) {
  const version = ++selectionVersion
  selectedCaseId.value = caseId
  documents.value = null
  snapshots.value = []
  nextSnapshotBefore.value = null
  loadingOlder.value = false
  selectedHistoryId.value = null
  historyDocuments.value = null
  error.value = ''
  evidenceTarget.value = null
  checklistTarget.value = null; reviewTarget.value = null
  try {
    const [current, history] = await Promise.all([
      apiRequest<DocumentsResult>(`/cases/${caseId}/documents`, accessToken),
      apiRequest<SnapshotsResult>(`/cases/${caseId}/snapshots`, accessToken),
    ])
    if (version === selectionVersion) {
      documents.value = current
      cases.value = cases.value.map(item => item.id === caseId
        ? { ...item, revision: current.revision } : item)
      snapshots.value = history.snapshots
      nextSnapshotBefore.value = history.next_before_revision
    }
  } catch (reason) {
    if (version === selectionVersion) error.value = friendlyError(reason)
  }
}

async function refreshAfterCorrection(target: { caseId: string; fromSnapshotId: string }) {
  const { caseId, fromSnapshotId } = target
  if (selectedCaseId.value !== caseId) return
  const version = selectionVersion + 1
  await selectCase(caseId)
  if (selectedCaseId.value !== caseId || version !== selectionVersion) return
  await selectHistory(fromSnapshotId)
  activeTab.value = 'agent'
  panel.value = 'agent'
}

async function selectHistory(snapshotId: string | null) {
  const caseId = selectedCaseId.value
  evidenceTarget.value = null
  selectedHistoryId.value = snapshotId
  historyDocuments.value = null
  if (!caseId || !snapshotId) return
  const version = selectionVersion
  try {
    const result = await apiRequest<DocumentsResult>(
      `/cases/${caseId}/snapshots/${snapshotId}/documents`, accessToken,
    )
    if (version === selectionVersion && selectedHistoryId.value === snapshotId) {
      historyDocuments.value = result
    }
  } catch (reason) {
    if (version === selectionVersion && selectedHistoryId.value === snapshotId) {
      error.value = friendlyError(reason)
    }
  }
}

async function loadOlderSnapshots() {
  const caseId = selectedCaseId.value
  const before = nextSnapshotBefore.value
  if (!caseId || before === null || loadingOlder.value) return
  const version = selectionVersion
  loadingOlder.value = true
  try {
    const result = await apiRequest<SnapshotsResult>(
      `/cases/${caseId}/snapshots?before_revision=${before}`, accessToken,
    )
    if (version === selectionVersion && before === nextSnapshotBefore.value) {
      snapshots.value.push(...result.snapshots)
      nextSnapshotBefore.value = result.next_before_revision
    }
  } catch (reason) {
    if (version === selectionVersion) error.value = friendlyError(reason)
  } finally {
    if (version === selectionVersion) loadingOlder.value = false
  }
}

async function createProject(name: string) {
  if (busy.value) return
  const version = selectionVersion
  busy.value = true
  error.value = ''
  try {
    const created = await apiRequest<Project>('/projects', accessToken, {
      method: 'POST', body: JSON.stringify({ name }),
    })
    const response = await apiRequest<{ projects: Project[] }>('/projects', accessToken)
    if (version !== selectionVersion) return
    projects.value = response.projects
    await selectProject(created.id)
  } catch (reason) {
    if (version === selectionVersion) error.value = friendlyError(reason)
  } finally {
    busy.value = false
  }
}

async function createCase() {
  const projectId = selectedProjectId.value
  const version = selectionVersion
  if (!projectId || !canWrite.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const created = await apiRequest<CaseRecord>('/cases', accessToken, {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId }),
    })
    if (version !== selectionVersion) return
    const response = await apiRequest<{ cases: CaseRecord[] }>(
      `/projects/${projectId}/cases`, accessToken,
    )
    if (version !== selectionVersion) return
    cases.value = response.cases
    await selectCase(created.id)
  } catch (reason) {
    if (version === selectionVersion) error.value = friendlyError(reason)
  } finally {
    busy.value = false
  }
}

async function handleUpload(payload: {
  file: File
  role: DocumentRecord['role']
  supplierHandle: string
  supplierName: string
  modelName: string
}) {
  const caseId = selectedCaseId.value
  const projectId = selectedProjectId.value
  const version = selectionVersion
  const revision = documents.value?.revision
  if (!caseId || !projectId || revision === undefined || !canWrite.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await uploadDocument(caseId, payload.file, payload.role,
                         payload.supplierHandle, payload.supplierName,
                         revision, accessToken,
                         payload.modelName)
    if (version !== selectionVersion) return
    const response = await apiRequest<{ cases: CaseRecord[] }>(
      `/projects/${projectId}/cases`, accessToken,
    )
    if (version !== selectionVersion) return
    cases.value = response.cases
    await selectCase(caseId)
  } catch (reason) {
    if (version === selectionVersion) error.value = friendlyError(reason)
  } finally {
    busy.value = false
  }
}

async function refreshDocuments(force = false) {
  const caseId = selectedCaseId.value
  const version = selectionVersion
  if (!caseId || busy.value) return
  if (!force && !documents.value?.documents.some(item =>
    item.parse_status === 'pending' || item.parse_status === 'processing')) return
  try {
    const response = await apiRequest<DocumentsResult>(
      `/cases/${caseId}/documents`, accessToken,
    )
    if (caseId === selectedCaseId.value && version === selectionVersion &&
        (!documents.value || response.revision >= documents.value.revision)) {
      documents.value = response
      if (force && selectedHistoryId.value) await selectHistory(selectedHistoryId.value)
    }
  } catch (reason) {
    if (version === selectionVersion) error.value = friendlyError(reason)
  }
}

onMounted(async () => {
  try {
    await loadProjects()
  } catch (reason) {
    error.value = friendlyError(reason)
  } finally {
    loading.value = false
  }
  refreshTimer = setInterval(() => { void refreshDocuments() }, 4000)
})

onUnmounted(() => { if (refreshTimer) clearInterval(refreshTimer) })
</script>

<template>
  <div class="chat-app" :class="{ 'sidebar-collapsed': !sidebarOpen, 'work-panel-open': panel || evidenceTarget }">
    <div v-if="sidebarOpen" class="sidebar-scrim" @click="sidebarOpen = false" />
    <CaseSidebar v-if="sidebarOpen" :projects="projects" :cases="cases" :task-titles="taskTitles"
                 :selected-project-id="selectedProjectId" :selected-case-id="selectedCaseId"
                 :can-write="canWrite" :busy="busy || loading"
                 @select-project="selectProject" @select-case="selectCase"
                 @create-project="createProject" @create-case="createCase" @new-task="newTask" @close="sidebarOpen = false" />
    <main class="chat-main">
      <header class="chat-topbar">
        <button class="icon-button" aria-label="切换采购任务侧栏" :aria-expanded="sidebarOpen" @click="sidebarOpen = !sidebarOpen">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/></svg>
        </button>
        <div class="chat-page-title"><strong>{{ selectedCase ? taskTitles[selectedCase.id] ?? '采购任务' : '新采购任务' }}</strong><span>{{ selectedProject?.name ?? '创建一个工作区，开始采购对话' }}</span></div>
        <nav class="chat-header-tools" aria-label="采购工作面板">
          <button class="text-button" :disabled="!selectedProjectId || busy" :aria-pressed="panel === 'documents'" @click="openDocuments">资料<span v-if="documents?.documents.length" class="file-count">{{ documents.documents.length }}</span></button>
          <button class="text-button" :disabled="!selectedCaseId || !checklistSnapshotId" :aria-pressed="panel === 'checklist'" @click="openChecklistPanel">审查清单</button>
          <button class="text-button" :disabled="!selectedCaseId" :aria-pressed="panel === 'agent'" @click="panel = panel === 'agent' ? null : 'agent'; activeTab = 'agent'; evidenceTarget = null">历史核验</button>
        </nav>
        <details class="account-menu"><summary :aria-label="`${displayName}的账号菜单`">{{ displayName.slice(0, 1).toUpperCase() }}</summary><div><strong>{{ displayName }}</strong><button class="text-button" @click="memoryOpen = true">记忆偏好</button><button class="text-button" @click="signOut">退出登录</button></div></details>
      </header>
      <div v-if="error" class="alert" role="alert"><span>{{ error }}</span><button class="text-button" @click="error = ''">关闭</button></div>
      <div v-if="loading" class="loading-state" role="status">正在读取工作区…</div>
      <ChatWorkspace v-else :case-id="selectedCaseId" :scope-key="selectedProjectId ?? ''" :revision="documents?.revision ?? selectedCase?.revision ?? 0" :pending-report-corrections="pendingReportCorrections"
                     :checklist-changes="checklistChanges" :binding-changes="bindingChanges"
                     :can-write="canWrite" :busy="busy" :prepare-case="prepareCase"
                     @upload="openDocuments" @title="taskTitles[$event.caseId] = $event.title"
                     @open-evidence="evidenceTarget = $event; panel = null"
                     @open-checklist="openChecklistFromChat" @open-review="openReviewFromChat" />
    </main>
    <aside v-if="panel && !evidenceTarget" class="chat-work-panel" :class="{ 'legacy-panel': panel === 'agent' }" aria-label="采购工作面板">
      <header class="work-panel-heading"><h2>{{ panelTitles[panel] ?? '采购工作面板' }}</h2><button class="icon-button" aria-label="关闭工作面板" @click="panel = null">×</button></header>
      <div class="work-panel-content">
        <DocumentsPanel v-if="panel === 'documents'" :selected-case="selectedCase" :documents="documents"
                        :snapshots="snapshots" :next-snapshot-before="nextSnapshotBefore" :loading-older="loadingOlder"
                        :selected-history-id="selectedHistoryId" :history-documents="historyDocuments" :can-write="canWrite" :busy="busy"
                        :refresh-key="checklistChanges[documents?.snapshot_id ?? ''] ?? 0"
                        :binding-refresh-key="bindingChanges[documents?.snapshot_id ?? ''] ?? 0" :notify-bindings-confirmed="bindingsChanged"
                        @upload="handleUpload" @select-history="selectHistory" @load-older-snapshots="loadOlderSnapshots" @refresh-documents="refreshDocuments(true)" />
        <ChecklistPanel v-else-if="panel === 'checklist' && checklistSnapshotId"
                        :case-id="selectedCaseId!" :snapshot-id="checklistSnapshotId"
                        :revision="documents?.revision ?? 0" :can-write="canWrite && !busy"
                        :candidate="checklistTarget?.candidate ?? null"
                        :candidate-read-only="checklistTarget?.readOnly ?? false"
                        @saved="checklistChanged" @confirmed="checklistChanged"
                        @open-evidence="evidenceTarget = $event; panel = null" />
        <ReviewPanel v-else-if="panel === 'review' && reviewTarget"
                     :case-id="reviewTarget.caseId" :run-id="reviewTarget.runId" :snapshot-id="reviewTarget.snapshotId"
                     :revision="documents?.revision ?? 0" :can-write="canWrite && !busy" :supplier-names="supplierNames"
                     @open-evidence="evidenceTarget = $event; panel = null" @correction-submitted="trackReportCorrection" />
        <template v-else-if="panel === 'agent'">
          <label v-if="snapshots.length" class="workspace-history">查看资料版本<select class="field-control" :value="selectedHistoryId ?? ''" @change="selectHistory(($event.target as HTMLSelectElement).value || null)"><option value="">当前版本 {{ documents?.revision ?? 0 }}</option><option v-for="snapshot in snapshots.filter(item => item.revision !== documents?.revision)" :key="snapshot.id" :value="snapshot.id">历史版本 {{ snapshot.revision }}</option></select></label>
          <AgentWorkbench :case-id="selectedCaseId" :documents="documents" :history-documents="historyDocuments" :selected-history-id="selectedHistoryId" :can-write="canWrite" history-only @open-evidence="evidenceTarget = $event; panel = null" @open-review="openReviewFromChat" @changed="refreshAfterCorrection" />
        </template>
      </div>
    </aside>
    <EvidencePanel v-if="evidenceTarget" :target="evidenceTarget" :width="evidenceWidth" @close="evidenceTarget = null" @resize="evidenceWidth = $event" />
    <MemorySettings v-if="memoryOpen" @close="memoryOpen = false" />
  </div>
</template>
