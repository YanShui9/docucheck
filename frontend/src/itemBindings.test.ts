import { afterEach, expect, test, vi } from 'vitest'
import { confirmItemBindings, fetchDocumentEvidence, fetchItemBindings } from './itemBindings'

afterEach(() => vi.unstubAllGlobals())
test('item attribution uses authenticated scoped endpoints and the caller supplied retry key', async () => {
  const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ state: 'empty' }), { status: 200 }))
  vi.stubGlobal('fetch', fetcher)
  const token = async () => 'signed-token'
  await fetchItemBindings('case-a', 'snap-a', token)
  const body = { entries: [], checklist_version_id: 'check-a', expected_revision: 2,
    expected_checklist_revision: 3, expected_binding_revision: 1, confirmed: true as const, reason: '明确移除对应' }
  await confirmItemBindings('case-a', 'snap-a', body, 'stable-retry-key', token)
  await confirmItemBindings('case-a', 'snap-a', body, 'stable-retry-key', token)
  expect(fetcher.mock.calls[0]![0]).toBe('/api/v1/cases/case-a/snapshots/snap-a/item-bindings')
  for (const call of fetcher.mock.calls.slice(1)) {
    const init = call[1] as RequestInit
    expect((init.headers as Headers).get('Authorization')).toBe('Bearer signed-token')
    expect((init.headers as Headers).get('Idempotency-Key')).toBe('stable-retry-key')
    expect(JSON.parse(init.body as string)).toEqual(body)
    expect(init.cache).toBe('no-store')
  }
})
test('evidence reads page only the selected document and encode server identifiers', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
  vi.stubGlobal('fetch', fetcher)
  await fetchDocumentEvidence('case/a', 'snap a', 'doc/a', 99, async () => 'token')
  expect(fetcher.mock.calls[0]![0]).toBe('/api/v1/cases/case%2Fa/snapshots/snap%20a/documents/doc%2Fa/evidence?after_ordinal=99&limit=100')
})
