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
