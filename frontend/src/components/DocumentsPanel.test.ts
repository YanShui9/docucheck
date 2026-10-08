// @vitest-environment jsdom
import { expect, test } from 'vitest'
import { mount } from '@vue/test-utils'
import DocumentsPanel from './DocumentsPanel.vue'
test('generic supplier materials can upload without inventing an electronic device model', async () => {
  const wrapper = mount(DocumentsPanel, { props: { selectedCase: { id: 'case-a', project_id: 'project-a', revision: 0, created_at: '' }, documents: null,
    snapshots: [], nextSnapshotBefore: null, loadingOlder: false, selectedHistoryId: null, historyDocuments: null, canWrite: true, busy: false } })
  await wrapper.get('#document-role').setValue('quote')
  await wrapper.get('#supplier-handle').setValue('orchard-a')
  await wrapper.get('#supplier-name').setValue('果园甲')
  Object.defineProperty(wrapper.get('#document-file').element, 'files', { value: [new File(['test'], '水果报价.xlsx')] })
  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('upload')).toHaveLength(1)
  expect((wrapper.emitted('upload')![0]![0] as { modelName: string }).modelName).toBe('')
  wrapper.unmount()
})

test.each(['', '红富士-一级'])('supplier material uses generic product identification: %s', modelName => {
  const wrapper = mount(DocumentsPanel, { props: {
    selectedCase: { id: 'case-a', project_id: 'project-a', revision: 1, created_at: '' },
    documents: { case_id: 'case-a', snapshot_id: 'snap-a', revision: 1, documents: [{
      id: 'doc-a', original_name: '水果报价.xlsx', role: 'quote', supplier_handle: 'orchard-a',
      supplier_name: '果园甲', model_name: modelName, parse_status: 'complete', source_sha256: 'test-source',
    }] }, snapshots: [], nextSnapshotBefore: null, loadingOlder: false,
    selectedHistoryId: null, historyDocuments: null, canWrite: true, busy: false,
  } })
  expect(wrapper.text()).not.toContain('未绑定型号')
  expect(wrapper.text()).toContain(modelName ? `产品标识 ${modelName}` : '未设置产品标识')
  wrapper.unmount()
})
