// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { getDocument } from 'pdfjs-dist'
import EvidencePanel from './EvidencePanel.vue'

const { apiRequest, readOriginal } = vi.hoisted(() => ({
  apiRequest: vi.fn(), readOriginal: vi.fn(),
}))
vi.mock('../auth', () => ({ accessToken: async () => 'token' }))
vi.mock('../api', () => ({ apiRequest, readOriginal }))
vi.mock('pdfjs-dist', () => ({ GlobalWorkerOptions: { workerSrc: '' }, getDocument: vi.fn() }))

afterEach(() => { apiRequest.mockReset(); readOriginal.mockReset(); vi.mocked(getDocument).mockReset() })

test('XLSX evidence resolves its authorized snapshot and shows exact cell text', async () => {
  apiRequest.mockImplementation((path: string) => {
    if (path.endsWith('/evidence/evidence-1')) return Promise.resolve({
      id: 'evidence-1', document_id: 'doc-1', ordinal: 1,
      source_text: '已装内存 16 GB', page_idx: null, bbox: null,
      page_width_pt: null, page_height_pt: null,
      sheet: '报价', cell: 'B6', parser_version: 'xlsx-v1',
    })
    if (path.endsWith('/documents')) return Promise.resolve({
      documents: [{ id: 'doc-1', original_name: 'vendor-a-quote.xlsx',
        role: 'quote', supplier_handle: 'vendor-a', model_name: 'Model-X1',
        parse_status: 'complete', source_sha256: 'abc123456789abcdef' }],
    })
    if (path.includes('/original/sheet-window?')) return Promise.resolve({
      sheet: '报价', cell: 'B6', rows: [{ number: 6, cells: [
        { address: 'A6', text: '已装内存', highlight: false },
        { address: 'B6', text: '已装内存 16 GB', highlight: true },
      ] }],
    })
    throw new Error(`Unexpected path ${path}`)
  })
  readOriginal.mockResolvedValue(new Blob(['xlsx bytes']))
  const wrapper = mount(EvidencePanel, { props: {
    target: { caseId: 'case-1', snapshotId: 'snapshot-1', evidenceId: 'evidence-1' },
    width: 390,
  } })
  await flushPromises()
  expect(wrapper.text()).toContain('报价!B6')
  expect(wrapper.text()).toContain('已装内存 16 GB')
  expect(wrapper.find('.sheet-cell-target').text()).toContain('B6')
  expect(readOriginal).toHaveBeenCalledWith(
    'case-1', 'snapshot-1', 'doc-1', expect.any(Function))
  wrapper.unmount()
})

test('PDF evidence renders only its selected page and changes pages once', async () => {
  const context = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
    .mockReturnValue({} as CanvasRenderingContext2D)
  const getPage = vi.fn().mockResolvedValue({
    getViewport: ({ scale }: { scale: number }) => ({ width: 400 * scale,
      height: 600 * scale }),
    render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
  })
  vi.mocked(getDocument).mockReturnValue({ promise: Promise.resolve({
    numPages: 3, getPage,
  }), destroy: vi.fn() } as never)
  apiRequest.mockImplementation((path: string) => {
    if (path.endsWith('/evidence/evidence-2')) return Promise.resolve({
      id: 'evidence-2', document_id: 'doc-2', ordinal: 1,
      source_text: '主机重量 1.8 kg', page_idx: 1, bbox: [0.1, 0.1, 0.6, 0.2],
      page_width_pt: 400, page_height_pt: 600, sheet: null, cell: null,
      parser_version: 'pdf-v1',
    })
    if (path.endsWith('/documents')) return Promise.resolve({ documents: [{
      id: 'doc-2', original_name: 'spec.pdf', role: 'spec',
      supplier_handle: 'vendor-a', model_name: 'Model-X1',
      parse_status: 'complete', source_sha256: 'abc123456789abcdef',
    }] })
    throw new Error(`Unexpected path ${path}`)
  })
  readOriginal.mockResolvedValue({
    arrayBuffer: async () => new Uint8Array([37, 80, 68, 70]).buffer,
  })
  const wrapper = mount(EvidencePanel, { props: {
    target: { caseId: 'case-1', snapshotId: 'snapshot-1', evidenceId: 'evidence-2' },
    width: 390,
  } })
  await flushPromises()
  expect(getPage).toHaveBeenCalledTimes(1)
  expect(getPage).toHaveBeenCalledWith(2)
  await wrapper.find('.page-controls button').trigger('click')
  await flushPromises()
  expect(getPage).toHaveBeenCalledTimes(2)
  expect(getPage).toHaveBeenLastCalledWith(1)
  wrapper.unmount()
  context.mockRestore()
})
