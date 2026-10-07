// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ChecklistPanel from './ChecklistPanel.vue'
import type { ChecklistCriterion, ChecklistDefinitionPayload } from '../api'
const { fetchChecklist, saveChecklistDraft, confirmChecklist, fetchChecklistVersion, previewChecklistRebase } = vi.hoisted(() => ({
  fetchChecklist: vi.fn(),
  saveChecklistDraft: vi.fn(),
  confirmChecklist: vi.fn(),
  fetchChecklistVersion: vi.fn(),
  previewChecklistRebase: vi.fn(),
}))
vi.mock('../api', () => ({ fetchChecklist, saveChecklistDraft, confirmChecklist, fetchChecklistVersion, previewChecklistRebase }))
vi.mock('../auth', () => ({ accessToken: async () => 'token' }))

const numericCriterion: ChecklistCriterion = {
  criterion_id: 'c-qty', item_id: 'apple', name: '数量', required: true, enabled: true,
  conditions: [], rule: { type: 'numeric', operator: 'gte', value: '20', unit: '箱' },
  source: { kind: 'document_extracted',
    context: { evidence_refs: ['ev-1'], quote: '苹果 数量至少20箱' } },
}
const manualCriterion: ChecklistCriterion = {
  criterion_id: 'c-cert', item_id: 'apple', name: '检疫证明', required: true, enabled: true,
  conditions: [], rule: { type: 'manual', instruction: '核对植物检疫证明是否齐全' },
  source: { kind: 'user_defined', reason: '用户要求核对证明', context: null },
}
const draftDefinition: ChecklistDefinitionPayload = { items: [{ item_id: 'apple', name: '苹果', specification: '' }],
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

test('a historical candidate cannot be saved into a current checklist', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'draft', draft: draftDefinition }))
  const wrapper = setup({ candidateReadOnly: true,
    candidate: { candidate: draftDefinition, changes: [], runId: 'old-run' } })
  await flushPromises()
  expect(wrapper.text()).toContain('历史候选')
  expect(wrapper.find('[data-test="save-draft"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="start-add"]').exists()).toBe(false)
  expect(saveChecklistDraft).not.toHaveBeenCalled()
})

test('a candidate made stale before opening is read-only even with a current snapshot', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 5,
    draft: { ...draftDefinition, criteria: [manualCriterion] } }))
  const wrapper = setup({ candidate: { candidate: draftDefinition, changes: [], runId: 'old-run', baseChecklistRevision: 4 } })
  await flushPromises()
  expect(wrapper.text()).toContain('历史候选')
  expect(wrapper.find('[data-test="save-draft"]').exists()).toBe(false)
})

test('editing and saving a current candidate does not lock its confirmation controls', async () => {
  fetchChecklist.mockResolvedValue(emptyView())
  saveChecklistDraft.mockImplementation(async (_caseId, _snapshotId, definition) => emptyView({
    state: 'draft', checklist_revision: 1, draft: definition }))
  const wrapper = setup({ candidate: { candidate: draftDefinition, changes: [], runId: 'new-run', baseChecklistRevision: 0 } })
  await flushPromises()
  await wrapper.get('[data-test="criterion-name-c-qty"] input').setValue('采购总数量')
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).not.toContain('历史候选')
  expect(wrapper.get('[data-test="open-confirm"]').attributes('disabled')).toBeUndefined()
  expect(wrapper.emitted('saved')).toEqual([[{ caseId: 'case-a', snapshotId: 'snap-1' }]])
})

test('a candidate already saved before opening uses the persisted checklist without requiring another save', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 1, draft: draftDefinition }))
  const wrapper = setup({ candidate: { candidate: draftDefinition, changes: [], runId: 'run-a', baseChecklistRevision: 0 } })
  await flushPromises()
  expect(wrapper.text()).not.toContain('尚未保存确认')
  expect(wrapper.get('[data-test="open-confirm"]').attributes('disabled')).toBeUndefined()
  expect(saveChecklistDraft).not.toHaveBeenCalled()
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount());
  [fetchChecklist, saveChecklistDraft, confirmChecklist, fetchChecklistVersion, previewChecklistRebase].forEach(fn => fn.mockReset()) })

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
    draft: { ...draftDefinition, criteria: [manualCriterion] },
    confirmed_version: { id: 'ver-1', definition: { ...draftDefinition, criteria: [numericCriterion] } },
    execution_available: true, excluded_criterion_ids: ['c-cert'] }))
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
  saveChecklistDraft.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 1, draft: draftDefinition }))
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
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})

test('stale snapshots require reconfirmation and can adopt the last confirmed version', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'needs_reconfirmation', checklist_revision: 5,
    revision: 9, is_current_snapshot: true, previous_version_id: 'ver-9' }))
  const rebound = structuredClone(draftDefinition)
  rebound.criteria[0].source.context!.evidence_refs = ['ev-new']
  previewChecklistRebase.mockResolvedValue({ case_id: 'case-a', snapshot_id: 'snap-1',
    revision: 9, definition: rebound, source_version_id: 'ver-9' })
  saveChecklistDraft.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 6, revision: 9, draft: rebound }))
  const wrapper = setup({ revision: 9 })
  await flushPromises()
  expect(wrapper.text()).toContain('资料已更新')
  expect(wrapper.get('[data-test="save-draft"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-test="adopt-draft"]').trigger('click')
  await flushPromises()
  expect(previewChecklistRebase).toHaveBeenCalledWith('case-a', 'snap-1', 'ver-9', 9, expect.anything())
  expect(wrapper.get('[data-test="save-draft"]').attributes('disabled')).toBeUndefined()
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  await flushPromises()
  expect(saveChecklistDraft.mock.calls[0][2]).toEqual(rebound)
  await wrapper.findAll('button').find(b => b.text().includes('查看原文'))!.trigger('click')
  expect(wrapper.emitted('openEvidence')?.[0]).toEqual([{ caseId: 'case-a', snapshotId: 'snap-1', evidenceId: 'ev-new' }])
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

function savedDraft() {
  const editable = structuredClone(draftDefinition)
  editable.criteria[0].source = { kind: 'user_defined', reason: '买方明确设置', context: null }
  fetchChecklist.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 3, draft: editable }))
  saveChecklistDraft.mockImplementation(async (_case, _snap, definition) => emptyView({
    state: 'draft', checklist_revision: 4, draft: structuredClone(definition),
  }))
}

test('edited thresholds cannot confirm until the edited definition has been saved', async () => {
  savedDraft()
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="edit-c-qty"]').trigger('click')
  await wrapper.get('[data-test="rule-value"]').setValue('200')
  expect(wrapper.get('[data-test="open-confirm"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-test="rule-save"]').trigger('click')
  expect(wrapper.get('[data-test="open-confirm"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-test="open-confirm"]').trigger('click')
  expect(confirmChecklist).not.toHaveBeenCalled()
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  await flushPromises()
  expect(saveChecklistDraft.mock.calls[0][2].criteria[0].rule.value).toBe('200')
  expect(wrapper.get('[data-test="open-confirm"]').attributes('disabled')).toBeUndefined()
  confirmChecklist.mockResolvedValue(emptyView({ state: 'confirmed', checklist_revision: 5,
    confirmed_version: { id: 'v2', definition: saveChecklistDraft.mock.calls[0][2] } }))
  await wrapper.get('[data-test="open-confirm"]').trigger('click')
  await wrapper.get('[data-test="confirm-submit"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('至少 200 箱')
  expect(wrapper.get('[data-test="open-confirm"]').attributes('disabled')).toBeDefined()
})

test('an unsaved candidate cannot confirm a previously saved draft with the same ids', async () => {
  savedDraft()
  const wrapper = setup({ candidate: { candidate: draftDefinition, changes: [], runId: 'new-candidate' } })
  await flushPromises()
  expect(wrapper.get('[data-test="open-confirm"]').attributes('disabled')).toBeDefined()
})

test('partial confirmation keeps confirmed rows visible and saving retains the full definition', async () => {
  savedDraft()
  fetchChecklist.mockResolvedValue(emptyView({ state: 'partially_confirmed', checklist_revision: 3,
    draft: { ...draftDefinition, criteria: [manualCriterion] },
    confirmed_version: { id: 'v1', definition: { ...draftDefinition, criteria: [numericCriterion] } } }))
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.find('[data-test="criterion-name-c-qty"]').exists()).toBe(true)
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  await flushPromises()
  expect(saveChecklistDraft.mock.calls[0][2].criteria.map((c: any) => c.criterion_id)).toEqual(['c-qty', 'c-cert'])
  confirmChecklist.mockResolvedValue(emptyView({ state: 'confirmed', checklist_revision: 5,
    confirmed_version: { id: 'v2', definition: saveChecklistDraft.mock.calls[0][2] } }))
  await wrapper.get('[data-test="open-confirm"]').trigger('click')
  await wrapper.get('[data-test="confirm-submit"]').trigger('click')
  expect(confirmChecklist.mock.calls[0][2]).toEqual(['c-qty', 'c-cert'])
})

test('confirming the remainder directly only submits draft ids and preserves the accumulated base', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'partially_confirmed', checklist_revision: 3,
    draft: { ...draftDefinition, criteria: [manualCriterion] },
    confirmed_version: { id: 'v1', definition: { ...draftDefinition, criteria: [numericCriterion] } } }))
  confirmChecklist.mockResolvedValue(emptyView({ state: 'confirmed', checklist_revision: 4,
    confirmed_version: { id: 'v2', definition: draftDefinition } }))
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="open-confirm"]').trigger('click')
  await wrapper.get('[data-test="confirm-submit"]').trigger('click')
  await flushPromises()
  expect(confirmChecklist.mock.calls[0][2]).toEqual(['c-cert'])
  expect(wrapper.find('[data-test="criterion-name-c-qty"]').exists()).toBe(true)
  expect(wrapper.find('[data-test="criterion-name-c-cert"]').exists()).toBe(true)
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})

test('replacement confirmation lists the previously confirmed metrics that will be removed', async () => {
  savedDraft()
  fetchChecklist.mockResolvedValue(emptyView({ state: 'draft', checklist_revision: 3,
    draft: { ...draftDefinition, criteria: [manualCriterion] },
    confirmed_version: { id: 'v1', definition: { ...draftDefinition, criteria: [numericCriterion] } } }))
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="open-confirm"]').trigger('click')
  expect(wrapper.get('[data-test="removed-confirmed"]').text()).toContain('数量')
})

test.each(['save', 'confirm', 'adopt'] as const)('late %s responses cannot overwrite a switched snapshot', async operation => {
  let resolve!: (value: unknown) => void
  const pending = new Promise(yes => { resolve = yes })
  fetchChecklist.mockImplementation(async (_case, snapshot) => snapshot === 'snap-1'
    ? emptyView({ state: operation === 'adopt' ? 'needs_reconfirmation' : 'draft',
        previous_version_id: 'v0', checklist_revision: 3, draft: operation === 'adopt' ? null : draftDefinition })
    : emptyView({ snapshot_id: 'snap-2', revision: 3, state: 'draft', checklist_revision: 4,
        draft: { ...draftDefinition, items: [{ item_id: 'apple', name: '新版本橙子' }] } }))
  const wrapper = setup()
  await flushPromises()
  if (operation === 'save') {
    saveChecklistDraft.mockReturnValue(pending)
    await wrapper.get('[data-test="save-draft"]').trigger('click')
  } else if (operation === 'confirm') {
    confirmChecklist.mockReturnValue(pending)
    await wrapper.get('[data-test="open-confirm"]').trigger('click')
    await wrapper.get('[data-test="confirm-submit"]').trigger('click')
  } else {
    previewChecklistRebase.mockReturnValue(pending)
    fetchChecklistVersion.mockReturnValue(pending)
    await wrapper.get('[data-test="adopt-draft"]').trigger('click')
  }
  await wrapper.setProps({ snapshotId: 'snap-2', revision: 3 })
  await flushPromises()
  resolve(operation === 'adopt' ? { definition: draftDefinition } : emptyView({
    state: 'draft', checklist_revision: 4, draft: draftDefinition }))
  await flushPromises()
  expect(wrapper.text()).toContain('新版本橙子')
  expect(wrapper.find('[data-test="confirm-submit"]').exists()).toBe(false)
  expect(wrapper.emitted('saved')).toBeUndefined()
  expect(wrapper.emitted('confirmed')).toBeUndefined()
  expect(wrapper.get('[data-test="save-draft"]').attributes('disabled')).toBeUndefined()
})

test('a missing or changed source cannot silently adopt old evidence into the new snapshot', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'needs_reconfirmation', previous_version_id: 'v0' }))
  previewChecklistRebase.mockRejectedValue(new Error('checklist_rebase_unresolved'))
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="adopt-draft"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('重新整理')
  expect(wrapper.get('[data-test="save-draft"]').attributes('disabled')).toBeDefined()
})

test('an empty checklist can create an item and metric without an assistant candidate', async () => {
  fetchChecklist.mockResolvedValue(emptyView())
  saveChecklistDraft.mockImplementation(async (_case, _snapshot, definition) => emptyView({
    state: 'draft', checklist_revision: 1, draft: structuredClone(definition),
  }))
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="start-add"]').trigger('click')
  const form = wrapper.get('.add-form')
  const inputs = form.findAll('input')
  await inputs[0].setValue('苹果')
  await inputs[1].setValue('采购数量')
  await inputs[2].setValue('20')
  await inputs[3].setValue('箱')
  await wrapper.get('[data-test="add-save"]').trigger('click')
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  await flushPromises()
  expect(saveChecklistDraft.mock.calls[0][2]).toMatchObject({ items: [{ name: '苹果' }],
    criteria: [{ name: '采购数量', rule: { type: 'numeric', value: '20', unit: '箱' } }] })
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})

test('switching rule types removes obsolete numeric fields', async () => {
  savedDraft()
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="edit-c-qty"]').trigger('click')
  await wrapper.get('[data-test="rule-form"] select').setValue('manual')
  await wrapper.get('[data-test="rule-form"] textarea').setValue('检查果品外观')
  await wrapper.get('[data-test="rule-save"]').trigger('click')
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  expect(saveChecklistDraft.mock.calls[0][2].criteria[0].rule).toEqual({ type: 'manual', instruction: '检查果品外观' })
})

test('switching between a threshold and a range clears mutually exclusive values', async () => {
  savedDraft()
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="edit-c-qty"]').trigger('click')
  await wrapper.findAll('[data-test="rule-form"] select')[1].setValue('between')
  await wrapper.get('[data-test="rule-lower"]').setValue('10')
  await wrapper.get('[data-test="rule-upper"]').setValue('30')
  await wrapper.get('[data-test="rule-save"]').trigger('click')
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  await flushPromises()
  expect(saveChecklistDraft.mock.calls[0][2].criteria[0].rule.value).toBeUndefined()
  await wrapper.get('[data-test="edit-c-qty"]').trigger('click')
  await wrapper.findAll('[data-test="rule-form"] select')[1].setValue('gte')
  await wrapper.get('[data-test="rule-value"]').setValue('15')
  await wrapper.get('[data-test="rule-save"]').trigger('click')
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  expect(saveChecklistDraft.mock.calls[1][2].criteria[0].rule.lower).toBeUndefined()
  expect(saveChecklistDraft.mock.calls[1][2].criteria[0].rule.upper).toBeUndefined()
})

test.each([
  ['money', { value: '6000', per_unit: '箱' }, { type: 'money', operator: 'lte', value: '6000', currency: 'CNY', per_unit: '箱', tax_basis: 'included' }],
  ['duration', { value: '10', start_event: '订单确认' }, { type: 'duration', operator: 'lte', value: '10', calendar: 'natural_days', start_event: '订单确认' }],
  ['enum', { values: '一级' }, { type: 'enum', operator: 'eq', values: ['一级'] }],
  ['presence', { description: '检疫合格证明' }, { type: 'presence', description: '检疫合格证明', must_be_present: true }],
  ['manual', { instruction: '核对外观和运输包装' }, { type: 'manual', instruction: '核对外观和运输包装' }],
] as const)('manual creation supports the %s rule contract', async (type, fields, expected) => {
  fetchChecklist.mockResolvedValue(emptyView())
  saveChecklistDraft.mockImplementation(async (_case, _snapshot, definition) => emptyView({
    state: 'draft', checklist_revision: 1, draft: structuredClone(definition),
  }))
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="start-add"]').trigger('click')
  const form = wrapper.get('.add-form')
  await form.findAll('input')[0].setValue('苹果')
  await form.findAll('input')[1].setValue('采购要求')
  await wrapper.get('[data-test="add-rule-type"]').setValue(type)
  const labels = { value: '阈值', per_unit: '计价单位', start_event: '起算事件',
    values: '可选值', description: '资料说明', instruction: '人工复核说明' }
  for (const [field, value] of Object.entries(fields)) {
    const label = form.findAll('label').find(node => node.text().startsWith(labels[field as keyof typeof labels]))!
    await label.get(field === 'instruction' ? 'textarea' : 'input').setValue(value)
  }
  await wrapper.get('[data-test="add-save"]').trigger('click')
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  await flushPromises()
  expect(saveChecklistDraft.mock.calls[0][2].criteria[0].rule).toEqual(expected)
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})

test('historical snapshots stay read-only even for an editor account', async () => {
  fetchChecklist.mockResolvedValue(emptyView({ state: 'historical', is_current_snapshot: false,
    draft: draftDefinition, checklist_revision: 3 }))
  const wrapper = setup()
  await flushPromises()
  expect(wrapper.find('[data-test="save-draft"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="edit-c-qty"]').exists()).toBe(false)
  expect(wrapper.find('[data-test="criterion-name-c-qty"] input').exists()).toBe(false)
})

test('a late rejected mutation cannot put an old error on a new candidate', async () => {
  savedDraft()
  let reject!: (reason: Error) => void
  saveChecklistDraft.mockReturnValue(new Promise((_resolve, no) => { reject = no }))
  const wrapper = setup()
  await flushPromises()
  await wrapper.get('[data-test="save-draft"]').trigger('click')
  await wrapper.setProps({ candidate: { candidate: draftDefinition, changes: [], runId: 'next-run' } })
  await flushPromises()
  reject(new Error('checklist_snapshot_changed'))
  await flushPromises()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.get('[data-test="save-draft"]').attributes('disabled')).toBeUndefined()
  expect(wrapper.text()).toContain('候选要求尚未保存确认')
})
