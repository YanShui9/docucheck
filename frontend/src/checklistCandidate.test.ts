import { expect, test } from 'vitest'
import { candidateState, sameDefinition } from './checklistCandidate'
import type { ChecklistDefinitionPayload, ChecklistView } from './api'

const candidate: ChecklistDefinitionPayload = {
  items: [{ item_id: 'apple', name: '苹果' }],
  criteria: [{ criterion_id: 'qty', item_id: 'apple', name: '数量',
    rule: { type: 'numeric', operator: 'gte', value: '20', unit: '箱' },
    source: { kind: 'user_defined', reason: '用户要求' }, enabled: true, required: true, conditions: [] },
  { criterion_id: 'quality', item_id: 'apple', name: '品质', rule: { type: 'manual', instruction: '核实新鲜度' },
    source: { kind: 'user_defined', reason: '用户要求' }, enabled: true, required: true, conditions: ['无霉变'] }],
}
function view(overrides: Partial<ChecklistView> = {}): ChecklistView {
  return { case_id: 'case-a', snapshot_id: 'snap-a', revision: 1, is_current_snapshot: true,
    checklist_revision: 2, state: 'draft', draft: candidate, confirmed_version: null,
    previous_version_id: null, issues: [], execution_available: false, ...overrides }
}

test('definition matching ignores object key order, row order and schema defaults', () => {
  const persisted = structuredClone(candidate)
  persisted.items[0]!.specification = ''
  persisted.criteria.reverse()
  persisted.criteria[1]!.source.context = null
  persisted.criteria[1]!.rule.lower = null
  persisted.criteria[1]!.rule.upper = null
  expect(sameDefinition(candidate, persisted)).toBe(true)
})
test.each(['threshold', 'source', 'condition', 'enabled', 'item'])('a changed %s cannot falsely mark a candidate saved', field => {
  const changed = structuredClone(candidate)
  if (field === 'threshold') changed.criteria[0]!.rule.value = '200'
  if (field === 'source') changed.criteria[0]!.source = { kind: 'document_extracted', context: { evidence_refs: ['ev'], quote: '20箱' } }
  if (field === 'condition') changed.criteria[1]!.conditions = []
  if (field === 'enabled') changed.criteria[0]!.enabled = false
  if (field === 'item') changed.items[0]!.name = '梨'
  expect(candidateState(candidate, view({ draft: changed }), 1, 0, false)).toBe('historical')
})
test('partial confirmation matches the union of confirmed and draft criteria', () => {
  const partial = view({ state: 'partially_confirmed',
    draft: { ...candidate, criteria: [candidate.criteria[1]!] },
    confirmed_version: { id: 'v1', version: 1, snapshot_id: 'snap-a', revision: 1,
      definition: { ...candidate, criteria: [candidate.criteria[0]!] }, confirmed_by: 'user', reason: '确认', manual_review_ids: [] } })
  expect(candidateState(candidate, partial, 1, 0, false)).toBe('partial')
})
test('matching old-snapshot data stays historical and a missing view stays unknown', () => {
  expect(candidateState(candidate, view({ is_current_snapshot: false }), 1, 0, false)).toBe('historical')
  expect(candidateState(candidate, undefined, 1, 0, true)).toBe('unknown')
})
