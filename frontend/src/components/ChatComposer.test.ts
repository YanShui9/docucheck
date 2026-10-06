// @vitest-environment jsdom
import { expect, test } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatComposer from './ChatComposer.vue'

test('Chinese candidate confirmation cannot send a message, while Enter and Shift Enter have distinct actions', async () => {
  const wrapper = mount(ChatComposer, { props: { modelValue: '采购苹果', disabled: false, running: false } })
  const input = wrapper.get('textarea')
  await input.trigger('compositionstart')
  await input.trigger('keydown', { key: 'Enter' })
  await input.trigger('compositionend')
  await input.trigger('keydown', { key: 'Enter', isComposing: true })
  await input.trigger('keydown', { key: 'Enter', keyCode: 229 })
  await input.trigger('keydown', { key: 'Enter', shiftKey: true })
  expect(wrapper.emitted('send')).toBeUndefined()
  await input.trigger('keydown', { key: 'Enter' })
  expect(wrapper.emitted('send')).toHaveLength(1)
  wrapper.unmount()
})

test('running and read-only modes prevent duplicate send but stopping remains available', async () => {
  const wrapper = mount(ChatComposer, { props: { modelValue: '问题', disabled: false, running: true } })
  await wrapper.get('textarea').trigger('keydown', { key: 'Enter' })
  expect(wrapper.emitted('send')).toBeUndefined()
  await wrapper.get('[aria-label="停止处理"]').trigger('click')
  expect(wrapper.emitted('stop')).toHaveLength(1)
  await wrapper.setProps({ running: false, disabled: true })
  expect(wrapper.get('[aria-label="发送消息"]').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})
