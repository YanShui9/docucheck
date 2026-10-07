// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ChecklistPanel from './ChecklistPanel.vue'
const { fetchChecklist, saveChecklistDraft, confirmChecklist, fetchChecklistVersion } = vi.hoisted(() => ({
  fetchChecklist: vi.fn(),
  saveChecklistDraft: vi.fn(),
  confirmChecklist: vi.fn(),
  fetchChecklistVersion: vi.fn(),
}))
vi.mock('../api', () => ({ fetchChecklist, saveChecklistDraft, confirmChecklist, fetchChecklistVersion }))
vi.mock('../auth', () => ({ accessToken: async () => 'token' }))

const numericCriterion = {
  criterion_id: 'c-qty', item_id: 'apple', name: '数量', required: true, enabled: true,
  conditions: [], rule: { type: 'numeric', operator: 'gte', value: '20', unit: '箱' },
  source: { kind: 'document_extracted',
    context: { evidence_refs: ['ev-1'], quote: '苹果 数量25箱' } },
}
const manualCriterion = {
  criterion_id: 'c-cert', item_id: 'apple', name: '检疫证明', required: true, enabled: true,
  conditions: [], rule: { type: 'manual', instruction: '核对植物检疫证明是否齐全' },
  source: { kind: 'user_defined', reason: '用户要求核对证明', context: null },
}
const draftDefinition = { items: [{ item_id: 'apple', name: '苹果', specification: '' }],
  criteria: [numericCriterion, manualCriterion] }

function emptyView(overrides: object = {}) {
  return { case_id: 'case-a', snapshot_id: 'snap-1', revision: 2, is_current_snapshot: true,
    checklist_revision: 0, state: 'empty', draft: null, confirmed_version: null,
    previous_version_id: null, issues: [], execution_available: false, ...overrides }
}

const wrappers: ReturnType<typeof mount>[] = []
function setup(props: Record<string, unknown> = {}) {
  const wrapper = mount(ChecklistPanel, { props: { caseId: 'case-a', snapshotId: 'snap-1',
    revision: 2, canWrite: true, ...props } })
  wrappers.push(wrapper)
  return wrapper
}
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount());
  [fetchChecklist, saveChecklistDraft, confirmChecklist, fetchChecklistVersion].forEach(fn => fn.mockReset()) })

test('renders a saved draft grouped by item with source labels and rule text', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 3, draft: draftDefinition }))
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('苹果')
  expect(wrapper.text()).toContain('数量')
  expect(wrapper.text()).toContain('检疫证明')
  expect(wrapper.text()).toContain('需求原文')
  expect(wrapper.text()).toContain('用户设置')
  expect(wrapper.text()).toContain('至少 20 箱')
  expect(wrapper.text()).toContain('草稿')
})

test('saving an edited draft PUTs the full definition and never marks it confirmed', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 3, draft: draftDefinition }))
  saveChecklistDraft.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 4, draft: draftDefinition }))
  const wrapper = setup()
  await flushPromises()
  const nameInput = wrapper.get('[data-test="criterion-name-c-qty"] input')
  await nameInput.setValue('采购数量')
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  await flushPromises()
  expect(saveChecklistDraft).toHaveBeenCalledTimes(1)
  const [caseId, snapshotId, definition, revision, checklistRevision, reason] =
    saveChecklistDraft.mock.calls[0]
  expect([caseId, snapshotId, revision, checklistRevision]).toEqual(['case-a', 'snap-1', 2, 3])
  expect(definition.criteria[0].name).toBe('采购数量')
  expect(definition.criteria).toHaveLength(2)
  expect(reason.trim().length).toBeGreaterThan(0)
  expect(wrapper.text()).toContain('草稿已保存')
  expect(wrapper.text()).toContain('尚未确认')
  expect(confirmChecklist).not.toHaveBeenCalled()
})

test('confirm posts only the selected criterion ids and reports exclusions', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 3, draft: draftDefinition }))
  confirmChecklist.mockResolvedValue(emptyView({ state: 'partially_confirmed', checklist_revision: 4,
    draft: draftDefinition, execution_available: true, excluded_criterion_ids: ['c-cert'] }))
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="select-c-cert"]').setValue(false)
  await wrapper.get('[data-test="open-confirm"]').trigger('click')
  expect(wrapper.text()).toContain('1 项指标')
  await wrapper.get('[data-test="confirm-submit"]').trigger('click')
  await flushPromises()
  expect(confirmChecklist).toHaveBeenCalledTimes(1)
  const [caseId, snapshotId, ids, revision, checklistRevision] = confirmChecklist.mock.calls[0]
  expect([caseId, snapshotId, ids, revision, checklistRevision]).toEqual(
    ['case-a', 'snap-1', ['c-qty'], 2, 3])
  expect(wrapper.text()).toContain('未纳入本次确认')
})

test('an assistant candidate prefills the editor and stays unsaved until explicit save', async () => {
  fetchChecklist.mockResolvedValue(emptyView())
  const wrapper = setup({ candidate: { candidate: draftDefinition,
    changes: [{ op: 'add', criterion_id: 'c-qty', reason: '来自需求原文',
      before: null, after: numericCriterion }] } })
  await flushPromises()
  expect(wrapper.text()).toContain('候选要求尚未保存确认')
  expect(wrapper.text()).toContain('数量')
  expect(saveChecklistDraft).not.toHaveBeenCalled()
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  await flushPromises()
  expect(saveChecklistDraft).toHaveBeenCalledTimes(1)
  expect(saveChecklistDraft.mock.calls[0][2].criteria).toHaveLength(2)
})

test('stale snapshots require reconfirmation and can adopt the last confirmed version', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'needs_reconfirmation', checklist_revision: 5,
    revision: 9, is_current_snapshot: true, previous_version_id: 'ver-9' }))
  fetchChecklistVersion.mockResolvedValue({ id: 'ver-9', version: 3, snapshot_id: 'snap-0',
    revision: 1, definition: draftDefinition, confirmed_by: 'alice', reason: '旧版确认',
    manual_review_ids: [] })
  const wrapper = setup({ revision: 9 })
  await flushPromises()
  expect(wrapper.text()).toContain('资料已更新')
  expect(wrapper.get('[data-test="save-draft"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-test="adopt-draft"]').trigger('click')
  await flushPromises()
  expect(fetchChecklistVersion).toHaveBeenCalledWith('case-a', 'ver-9', expect.anything())
  expect(wrapper.get('[data-test="save-draft"]').attributes('disabled')).toBeUndefined()
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  await flushPromises()
  expect(saveChecklistDraft.mock.calls[0][2]).toEqual(draftDefinition)
})

test('resolution issues are listed in Chinese and block nothing but are visible', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'needs_resolution', checklist_revision: 3,
    draft: draftDefinition, issues: [{ criterion_id: 'c-qty', code: 'tax_basis_missing' }] }))
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('待补充口径')
})

test('invalid numeric input is rejected locally without a save request', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 3, draft: draftDefinition }))
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="edit-c-qty"]').trigger('click')
  await wrapper.get('[data-test="rule-value"]').setValue('二十箱')
  await wrapper.get('[data-test="rule-save"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('数值格式')
  expect(saveChecklistDraft).not.toHaveBeenCalled()
})

test('read-only members cannot save, confirm, or edit', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 3, draft: draftDefinition }))
  const wrapper = setup({ canWrite: false })
  await flushPromises()
  expect(wrapper.find('[data-test="save-draft"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="open-confirm"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="edit-c-qty"]').exists()).toBe(false)
  expect(wrapper.text()).toContain('只读')
})

test('confirmed state shows the effective version and execution availability', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'confirmed', checklist_revision: 4,
    execution_available: true,
    confirmed_version: { id: 'ver-1', version: 2, snapshot_id: 'snap-1', revision: 2,
      definition: draftDefinition, confirmed_by: 'alice', reason: '首次确认',
      manual_review_ids: ['c-cert'] } }))
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.text()).toContain('已确认')
  expect(wrapper.text()).toContain('可执行完整审查')
  expect(wrapper.text()).toContain('人工复核')
})
