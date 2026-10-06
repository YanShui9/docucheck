import { afterEach, expect, test, vi } from 'vitest'
import { apiRequest, downloadRunExport, readOriginal, uploadDocument } from './api'

afterEach(() => vi.unstubAllGlobals())

test('API requests include the current access token and surface safe error codes', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ projects: [] }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      error: { code: 'not_found', message: 'not_found' },
    }), { status: 404, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetcher)

  await apiRequest('/projects', async () => 'signed-token')
  expect(fetcher.mock.calls[0][0]).toBe('/api/v1/projects')
  expect((fetcher.mock.calls[0][1].headers as Headers).get('Authorization'))
    .toBe('Bearer signed-token')
  await expect(apiRequest('/cases/missing', async () => 'signed-token'))
    .rejects.toThrow('not_found')
})

test('upload binds the document role, supplier and current revision', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    document_id: 'doc', revision: 2,
  }), { status: 201, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetcher)
  const file = new File(['sample'], 'quote.xlsx')

  await uploadDocument('case-1', file, 'quote', 'vendor-a', '供应商 A', 1,
                       async () => 'signed-token', 'Model-X1')
  const init = fetcher.mock.calls[0][1] as RequestInit
  const form = init.body as FormData
  expect(form.get('role')).toBe('quote')
  expect(form.get('supplier_handle')).toBe('vendor-a')
  expect(form.get('supplier_name')).toBe('供应商 A')
  expect(form.get('expected_revision')).toBe('1')
  expect(form.get('model_name')).toBe('Model-X1')
  expect((init.headers as Headers).get('Idempotency-Key')).toBeTruthy()
})

test('original download sends authorization and keeps binary contents', async () => {
  const source = new Uint8Array([37, 80, 68, 70, 45])
  const fetcher = vi.fn().mockResolvedValue(new Response(source, {
    headers: { 'Content-Type': 'application/pdf' },
  }))
  vi.stubGlobal('fetch', fetcher)

  const result = await readOriginal('case-1', 'snapshot-2', 'document-3',
                                    async () => 'signed-token')
  expect(Array.from(new Uint8Array(await result.arrayBuffer()))).toEqual(Array.from(source))
  expect(fetcher.mock.calls[0][0]).toBe(
    '/api/v1/cases/case-1/snapshots/snapshot-2/documents/document-3/original')
  expect(fetcher.mock.calls[0][1].headers.Authorization).toBe('Bearer signed-token')
})

test('Run export downloads the selected format with authorization', async () => {
  const source = new Uint8Array([80, 75, 3, 4])
  const fetcher = vi.fn().mockResolvedValue(new Response(source, {
    headers: { 'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
  }))
  vi.stubGlobal('fetch', fetcher)
  const result = await downloadRunExport('run-1', 'xlsx', async () => 'signed-token')
  expect(Array.from(new Uint8Array(await result.arrayBuffer()))).toEqual(Array.from(source))
  expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agent-runs/run-1/export?format=xlsx')
  expect(fetcher.mock.calls[0][1].headers.Authorization).toBe('Bearer signed-token')
})

test('Run export surfaces API error codes', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    error: { code: 'checks_unavailable' },
  }), { status: 409, headers: { 'Content-Type': 'application/json' } })))
  await expect(downloadRunExport('run-1', 'json', async () => 'signed-token'))
    .rejects.toThrow('checks_unavailable')
})
