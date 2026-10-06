<script setup lang="ts">
import { ref } from 'vue'
import type { CaseRecord, Project } from '../api'

defineProps<{
  projects: Project[]
  cases: CaseRecord[]
  selectedProjectId: string | null
  selectedCaseId: string | null
  canWrite: boolean
  busy: boolean
  taskTitles?: Record<string, string>
}>()

const emit = defineEmits<{
  selectProject: [id: string]
  selectCase: [id: string]
  createProject: [name: string]
  createCase: []
  newTask: []
  close: []
}>()

const projectName = ref('')

function submitProject() {
  const name = projectName.value.trim()
  if (!name) return
  emit('createProject', name)
  projectName.value = ''
}
</script>

<template>
  <aside class="sidebar" aria-label="项目与采购任务">
    <div class="sidebar-brand">
      <div class="brand-mark" aria-hidden="true">D<span>✓</span></div><strong>DocuCheck</strong>
      <button class="icon-button sidebar-close" aria-label="收起采购任务侧栏" @click="emit('close')">×</button>
    </div>
    <button class="new-task-button" :disabled="busy || !selectedProjectId || !canWrite" @click="emit('newTask')"><span aria-hidden="true">＋</span> 新采购任务</button>
    <label class="field-label" for="project-select">工作区</label>
    <select id="project-select" class="field-control" :value="selectedProjectId ?? ''"
            :disabled="busy || projects.length === 0"
            @change="emit('selectProject', ($event.target as HTMLSelectElement).value)">
      <option value="" disabled>选择项目</option>
      <option v-for="project in projects" :key="project.id" :value="project.id">
        {{ project.name }}
      </option>
    </select>

    <details class="new-workspace" :open="!projects.length"><summary>新建工作区</summary>
    <form class="new-project" @submit.prevent="submitProject">
      <label class="field-label" for="project-name">工作区名称</label>
      <div class="inline-form">
        <input id="project-name" v-model="projectName" class="field-control"
               maxlength="120" placeholder="例如：年度采购" :disabled="busy" />
        <button class="button button-secondary" type="submit" :disabled="busy || !projectName.trim()">
          创建
        </button>
      </div>
    </form>
    </details>

    <div class="sidebar-section-title">
      <h3>最近采购任务</h3>
    </div>
    <p v-if="!selectedProjectId" class="sidebar-hint">先创建工作区，集中保存采购资料与对话。</p>
    <p v-if="selectedProjectId && !canWrite" class="sidebar-hint">当前工作区为只读权限。</p>
    <p v-if="selectedProjectId && cases.length === 0" class="sidebar-hint">还没有采购任务。</p>
    <nav v-if="cases.length" class="case-list" aria-label="采购任务列表">
      <button v-for="item in cases" :key="item.id" type="button" class="case-item"
              :class="{ active: item.id === selectedCaseId }"
              :aria-current="item.id === selectedCaseId ? 'page' : undefined"
              @click="emit('selectCase', item.id)">
        <strong>{{ taskTitles?.[item.id] ?? `采购任务 ${item.id.slice(0, 6)}` }}</strong>
        <span>资料版本 {{ item.revision }} · {{ new Date(item.created_at).toLocaleDateString('zh-CN') }}</span>
      </button>
    </nav>
    <div class="sidebar-footer"><span>采购助手</span><small>需求 · 资料 · 有依据的结论</small></div>
  </aside>
</template>
