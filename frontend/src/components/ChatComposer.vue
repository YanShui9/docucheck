<script setup lang="ts">
import { ref } from 'vue'
const props = defineProps<{ modelValue: string; disabled: boolean; running: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; send: []; upload: []; stop: [] }>()
const composing = ref(false)
function send() { if (!props.disabled && !props.running && props.modelValue.trim()) emit('send') }
function keydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || composing.value || event.keyCode === 229) return
  event.preventDefault()
  send()
}
</script>
<template>
  <div class="chat-composer">
    <textarea :value="modelValue" aria-label="消息" rows="2" maxlength="2000"
              placeholder="描述采购需求，或针对资料提问…" :disabled="disabled"
              @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
              @compositionstart="composing = true" @compositionend="composing = false" @keydown="keydown" />
    <div class="composer-tools">
      <button class="composer-attachment" type="button" aria-label="上传采购资料" :disabled="disabled" @click="emit('upload')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m8 12 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l8-8" /></svg>
        <span>上传资料</span>
      </button>
      <span class="composer-key-hint">Enter 发送 · Shift + Enter 换行</span>
      <button v-if="running" class="composer-send" type="button" aria-label="停止处理" @click="emit('stop')">
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1" /></svg>
      </button>
      <button v-else class="composer-send" type="button" aria-label="发送消息" :disabled="disabled || !modelValue.trim()" @click="send">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" /></svg>
      </button>
    </div>
  </div>
</template>
