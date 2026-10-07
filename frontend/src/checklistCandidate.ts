import type { ChecklistDefinitionPayload, ChecklistView } from './api'

export interface ChecklistCandidate {
  candidate: ChecklistDefinitionPayload
  changes: import('./api').CriterionChange[]
  runId: string
  baseChecklistRevision?: number
}
export interface ChecklistTarget {
  caseId: string; snapshotId: string; runId: string
  candidate: ChecklistCandidate | null
  readOnly?: boolean
}
export type CandidateState = 'unknown' | 'candidate' | 'saved' | 'confirmed' | 'partial' | 'historical'

// Match full definitions, including sources, conditions and disabled criteria. Key order is immaterial.
// Only the documented optional defaults are normalized; changed thresholds are never guessed equivalent.
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') return `{${Object.entries(value)
    .filter(([, v]) => v !== undefined).sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(',')}}`
  return JSON.stringify(value)
}
function normalized(definition: ChecklistDefinitionPayload) {
  return {
    items: definition.items.map(item => ({ ...item, specification: item.specification ?? '' }))
      .sort((a, b) => a.item_id.localeCompare(b.item_id)),
    criteria: definition.criteria.map(criterion => {
      const rule = { ...criterion.rule }
      if (['numeric', 'money', 'duration'].includes(rule.type)) {
        rule.value ??= null; rule.lower ??= null; rule.upper ??= null
      }
      if (rule.type === 'presence') rule.must_be_present ??= true
      return { ...criterion, rule, source: { ...criterion.source, context: criterion.source.context ?? null },
        required: criterion.required ?? true, enabled: criterion.enabled ?? true, conditions: criterion.conditions ?? [] }
    }).sort((a, b) => a.criterion_id.localeCompare(b.criterion_id)),
  }
}
export function sameDefinition(a: ChecklistDefinitionPayload, b: ChecklistDefinitionPayload | null | undefined): boolean {
  return !!b && canonical(normalized(a)) === canonical(normalized(b))
}
export function candidateState(candidate: ChecklistDefinitionPayload, view: ChecklistView | undefined,
  revision: number, baseRevision: number | undefined, isCurrent: boolean): CandidateState {
  if (!view) return 'unknown'
  if (!view.is_current_snapshot || view.state === 'historical' || view.revision !== revision) return 'historical'
  const confirmed = view.confirmed_version?.definition
  if (view.state === 'partially_confirmed' && view.draft && confirmed) {
    const combined = { items: [...new Map([...confirmed.items, ...view.draft.items].map(item => [item.item_id, item])).values()],
      criteria: [...confirmed.criteria, ...view.draft.criteria] }
    if (sameDefinition(candidate, combined)) return 'partial'
  }
  if (sameDefinition(candidate, view.draft)) return 'saved'
  if (sameDefinition(candidate, confirmed)) return 'confirmed'
  return isCurrent && baseRevision === view.checklist_revision ? 'candidate' : 'historical'
}
