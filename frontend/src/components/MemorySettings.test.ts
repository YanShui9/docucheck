// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MemorySettings from './MemorySettings.vue'
const { fetchMemoryProfile, saveMemoryPreference, deleteMemoryPreference } = vi.hoisted(() => ({
  fetchMemoryProfile: vi.fn(),
  saveMemoryPreference: vi.fn(),
  deleteMemoryPreference: vi.fn(),
}))
vi.mock('../api', () => ({ fetchMemoryProfile, saveMemoryPreference, deleteMemoryPreference,
  MEMORY_KEY_LABELS: { answer_length: '答复长度', answer_format: '答复格式', language: '答复语言' },
  MEMORY_PREFERENCE_LABELS: {
    answer_length: { concise: '简洁', standard: '标准', detailed: '详细' },
    answer_format: { paragraph: '段落', bullets: '分条', table: '表格' },
    language: { 'zh-CN': '中文', en: 'English' },
  } }))
vi.mock('../auth', () => ({ accessToken: async () => 'token' }))

const profile = { revision: 4, records: [
  { key: 'answer_length', value: 'concise', expires_at: '2026-11-01T00:00:00Z' },
] }

const wrappers: ReturnType<typeof mount>[] = []
function setup() {
  const wrapper = mount(MemorySettings)
  wrappers.push(wrapper)
  return wrapper
}
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount());
  [fetchMemoryProfile, saveMemoryPreference, deleteMemoryPreference].forEach(fn => fn.mockReset()) })

test('loads the profile, shows limited keys with Chinese labels and expiry', async () => {
  fetchMemoryProfile.mockResolvedValue(profile)
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('答复长度')
  expect(wrapper.text()).toContain('答复格式')
  expect(wrapper.text()).toContain('答复语言')
  const lengthSelect = wrapper.get('[data-test="pref-answer_length"]').element as HTMLSelectElement
  expect(lengthSelect.value).toBe('concise')
  expect((wrapper.get('[data-test="pref-answer_format"]').element as HTMLSelectElement).value).toBe('')
  expect(wrapper.text()).toContain('2026年11月1日')
  expect(wrapper.text()).toContain('到期')
  expect(wrapper.text()).toContain('不会自动记住')
})

test('saving posts the confirmed value with profile revision and expiry', async () => {
  fetchMemoryProfile.mockResolvedValue(profile)
  saveMemoryPreference.mockResolvedValue({ revision: 5, records: [
    { key: 'answer_length', value: 'concise', expires_at: '2026-11-01T00:00:00Z' },
    { key: 'language', value: 'zh-CN', expires_at: null },
  ] })
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="pref-language"]').setValue('zh-CN')
  await wrapper.get('[data-test="expiry-language"]').setValue('30')
  await wrapper.get('[data-test="save-language"]').trigger('click')
  await flushPromises()
  expect(saveMemoryPreference).toHaveBeenCalledTimes(1)
  const [key, body] = saveMemoryPreference.mock.calls[0]
  expect(key).toBe('language')
  expect(body).toMatchObject({ value: 'zh-CN', expected_revision: 4, confirmed: true })
  expect(body.expires_at).not.toBeNull()
  const expires = new Date(body.expires_at as string)
  const in30days = new Date(Date.now() + 30 * 24 * 3600 * 1000)
  expect(Math.abs(expires.getTime() - in30days.getTime())).toBeLessThan(24 * 3600 * 1000)
  expect(wrapper.text()).toContain('已保存')
})

test('delete removes a stored preference with the revision guard', async () => {
  fetchMemoryProfile.mockResolvedValue(profile)
  deleteMemoryPreference.mockResolvedValue({ revision: 5, records: [] })
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="delete-answer_length"]').trigger('click')
  await flushPromises()
  expect(deleteMemoryPreference).toHaveBeenCalledWith('answer_length', 4, expect.anything())
  expect(wrapper.text()).toContain('已删除')
})

test('a revision conflict is reported in Chinese without losing the dialog', async () => {
  fetchMemoryProfile.mockResolvedValue(profile)
  saveMemoryPreference.mockRejectedValue(new Error('memory_revision_conflict'))
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="pref-answer_length"]').setValue('detailed')
  await wrapper.get('[data-test="save-answer_length"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('偏好已被其他人修改')
})

test('escape closes the dialog', async () => {
  fetchMemoryProfile.mockResolvedValue(profile)
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="memory-dialog"]').trigger('keydown.esc')
  expect(wrapper.emitted('close')).toHaveLength(1)
})

test.each(['30', ''])('expiry alone can be renewed or made permanent: %s', async choice => {
  fetchMemoryProfile.mockResolvedValue(profile)
  saveMemoryPreference.mockImplementation(async (_key, body) => ({ revision: 5,
    records: [{ ...profile.records[0], expires_at: body.expires_at }] }))
  const wrapper = setup()
  await flushPromises()
  expect((wrapper.get('[data-test="expiry-answer_length"]').element as HTMLSelectElement).value).toBe('keep')
  expect(wrapper.get('[data-test="save-answer_length"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-test="expiry-answer_length"]').setValue(choice)
  expect(wrapper.get('[data-test="save-answer_length"]').attributes('disabled')).toBeUndefined()
  await wrapper.get('[data-test="save-answer_length"]').trigger('click')
  await flushPromises()
  expect(saveMemoryPreference).toHaveBeenCalledTimes(1)
  const body = saveMemoryPreference.mock.calls[0][1]
  expect(body.value).toBe('concise')
  if (choice === '') expect(body.expires_at).toBeNull()
  else expect(Math.abs(Date.parse(body.expires_at) - Date.now() - 30 * 86400000)).toBeLessThan(10000)
  expect(wrapper.get('[data-test="save-answer_length"]').attributes('disabled')).toBeDefined()
})

test('changing only the value preserves the exact expiry and other unsaved preferences', async () => {
  fetchMemoryProfile.mockResolvedValue(profile)
  saveMemoryPreference.mockImplementation(async (_key, body) => ({ revision: 5,
    records: [{ ...profile.records[0], value: body.value, expires_at: body.expires_at }] }))
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="pref-language"]').setValue('zh-CN')
  await wrapper.get('[data-test="expiry-language"]').setValue('7')
  await wrapper.get('[data-test="pref-answer_length"]').setValue('detailed')
  await wrapper.get('[data-test="save-answer_length"]').trigger('click')
  await flushPromises()
  expect(saveMemoryPreference.mock.calls[0][1].expires_at).toBe(profile.records[0].expires_at)
  expect((wrapper.get('[data-test="pref-language"]').element as HTMLSelectElement).value).toBe('zh-CN')
  expect((wrapper.get('[data-test="expiry-language"]').element as HTMLSelectElement).value).toBe('7')
})
