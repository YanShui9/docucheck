<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { accessToken } from '../auth'
import {
  deleteMemoryPreference, fetchMemoryProfile, MEMORY_KEY_LABELS, MEMORY_PREFERENCE_LABELS,
  saveMemoryPreference,
} from '../api'
import type { MemoryProfile } from '../api'

const emit = defineEmits<{ close: [] }>()

const KEYS = ['answer_length', 'answer_format', 'language'] as const
const EXPIRY_OPTIONS = [
  { value: '', label: '长期有效' },
  { value: '7', label: '保存 7 天' },
  { value: '30', label: '保存 30 天' },
]
const errorTexts: Record<string, string> = {
  memory_revision_conflict: '偏好已被其他人修改，请重新读取后再保存。',
  invalid_memory_preference: '偏好取值无效，请重新选择。',
  invalid_memory_expiration: '到期时间无效。',
  memory_confirmation_required: '需要明确确认才能保存偏好。',
  session_expired: '登录已过期，请重新登录。',
  forbidden: '当前账号无法修改偏好。',
}

const profile = ref<MemoryProfile | null>(null)
const draft = ref<Record<string, string>>({})
const expiryChoice = ref<Record<string, string>>({})
const busy = ref(false)
const error = ref('')
const notice = ref('')
let generation = 0

function valueOf(key: string): string {
  return profile.value?.records.find(item => item.key === key)?.value ?? ''
}
function expiryOf(key: string): string | null {
  return profile.value?.records.find(item => item.key === key)?.expires_at ?? null
}
function formatExpiry(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
}
function friendly(reason: unknown): string {
  const code = reason instanceof Error ? reason.message : ''
  return errorTexts[code] ?? '暂时无法完成操作，请稍后重试。'
}

async function load() {
  const ticket = ++generation
  busy.value = true; error.value = ''
  try {
    const response = await fetchMemoryProfile(accessToken)
    if (ticket !== generation) return
    profile.value = response
    draft.value = Object.fromEntries(KEYS.map(key => [key, valueOf(key)]))
    expiryChoice.value = Object.fromEntries(KEYS.map(key => [key, valueOf(key) ? 'keep' : '']))
  } catch (reason) {
    if (ticket === generation) error.value = friendly(reason)
  } finally { if (ticket === generation) busy.value = false }
}
void load()

function expiresAtFor(key: string): string | null {
  const days = expiryChoice.value[key]
  if (days === 'keep') return expiryOf(key)
  if (!days) return null
  return new Date(Date.now() + Number(days) * 24 * 3600 * 1000).toISOString()
}
function changed(key: string): boolean {
  return !!draft.value[key] && (draft.value[key] !== valueOf(key)
    || ['7', '30'].includes(expiryChoice.value[key] ?? '')
    || (expiryChoice.value[key] === '' && expiryOf(key) !== null))
}

async function save(key: string) {
  const value = draft.value[key]
  if (!changed(key) || busy.value || !profile.value) return
  const ticket = generation
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const response = await saveMemoryPreference(key, {
      value, expected_revision: profile.value.revision, confirmed: true,
      expires_at: expiresAtFor(key),
    }, accessToken)
    if (ticket !== generation) return
    profile.value = response
    draft.value = { ...draft.value, [key]: valueOf(key) }
    expiryChoice.value = { ...expiryChoice.value, [key]: 'keep' }
    notice.value = `${MEMORY_KEY_LABELS[key] ?? key}已保存。`
  } catch (reason) { if (ticket === generation) error.value = friendly(reason) }
  finally { if (ticket === generation) busy.value = false }
}

async function remove(key: string) {
  if (!profile.value || busy.value) return
  const ticket = generation
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const response = await deleteMemoryPreference(key, profile.value.revision, accessToken)
    if (ticket !== generation) return
    profile.value = response
    draft.value = { ...draft.value, [key]: '' }
    expiryChoice.value = { ...expiryChoice.value, [key]: '' }
    notice.value = `${MEMORY_KEY_LABELS[key] ?? key}已删除。`
  } catch (reason) { if (ticket === generation) error.value = friendly(reason) }
  finally { if (ticket === generation) busy.value = false }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') emit('close')
}
window.addEventListener('keydown', onKeydown)
onUnmounted(() => { ++generation; window.removeEventListener('keydown', onKeydown) })
</script>

<template>
  <div class="confirm-overlay" data-test="memory-dialog" tabindex="-1" @keydown.esc="emit('close')">
    <div class="confirm-card memory-card" role="dialog" aria-label="记忆偏好设置">
      <h3>记忆偏好</h3>
      <p class="memory-note">
        这里只保存你明确设置的有限偏好，助手不会自动记住对话内容；历史消息与本次处理的上下文分开保存。
      </p>
      <div v-if="error" class="chat-error" role="alert">{{ error }}</div>
      <p v-if="notice" class="state-note" role="status">{{ notice }}</p>
      <div v-if="!profile && busy" class="chat-loading" role="status">正在读取偏好…</div>
      <template v-else-if="profile">
        <div v-for="key in KEYS" :key="key" class="memory-row" :data-test="`row-${key}`">
          <label class="memory-key">
            <span>{{ MEMORY_KEY_LABELS[key] }}</span>
            <select v-model="draft[key]" class="field-control" :disabled="busy" :data-test="`pref-${key}`">
              <option value="">未设置</option>
              <option v-for="(label, value) in MEMORY_PREFERENCE_LABELS[key]" :key="value" :value="value">{{ label }}</option>
            </select>
          </label>
          <p v-if="expiryOf(key)" class="memory-expiry">
            当前偏好 {{ formatExpiry(expiryOf(key)) }} 到期
          </p>
          <p v-else-if="valueOf(key)" class="memory-expiry">当前偏好长期有效</p>
          <div class="memory-actions">
            <label class="memory-expiry-choice">
              有效期
              <select v-model="expiryChoice[key]" class="field-control" :disabled="busy" :data-test="`expiry-${key}`">
                <option v-if="valueOf(key)" value="keep">保持当前有效期</option>
                <option v-for="option in EXPIRY_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </label>
            <button class="button button-primary" :data-test="`save-${key}`"
                    :disabled="busy || !changed(key)" @click="save(key)">保存</button>
            <button v-if="valueOf(key)" class="button button-secondary" :data-test="`delete-${key}`"
                    :disabled="busy" @click="remove(key)">删除</button>
          </div>
        </div>
      </template>
      <div class="rule-actions">
        <button v-if="error" class="button button-secondary" :disabled="busy" @click="load">重新读取偏好</button>
        <button class="button button-secondary" data-test="memory-close" @click="emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>
