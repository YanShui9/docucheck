// @vitest-environment jsdom
import { expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import DocumentsPanel from './DocumentsPanel.vue'

vi.mock('../auth', () => ({ accessToken: async () => 'token' }))
vi.mock('../itemBindings', async importOriginal => ({
  ...await importOriginal<object>(),
  fetchItemBindings: async (caseId: string, snapshotId: string) => ({ case_id: caseId, snapshot_id: snapshotId,
    state: 'empty', binding_revision: 0, current_version: null, historical_version: null }),
}))

test('current supplier material opens item attribution without changing the upload product identifier', async () => {
  const notifyBindingsConfirmed = vi.fn()
  const wrapper = mount(DocumentsPanel, { props: {
    selectedCase: { id: 'case-a', project_id: 'project-a', revision: 1, created_at: '' },
    documents: { case_id: 'case-a', snapshot_id: 'snap-a', revision: 1, documents: [{
      id: 'doc-a', original_name: '水果报价.xlsx', role: 'quote', supplier_handle: 'orchard-a',
      supplier_name: '果园甲', model_name: '苹果', parse_status: 'complete', source_sha256: 'a'.repeat(64),
    }] }, snapshots: [], nextSnapshotBefore: null, loadingOlder: false,
    selectedHistoryId: null, historyDocuments: null, historyLoading: false, historyError: '', canWrite: true, busy: false, notifyBindingsConfirmed,
  }, global: { stubs: { DocumentItemBindings: { name: 'DocumentItemBindings', props: ['document', 'canWrite', 'notifyConfirmed'], template: '<div class="binding-editor-stub" />' } } } })
  const entry = wrapper.findAll('button').find(button => button.text() === '关联采购品目')
  expect(entry, 'supplier document needs a Chinese attribution entry').toBeDefined()
  await entry!.trigger('click')
  expect(wrapper.findComponent({ name: 'DocumentItemBindings' }).props('document').model_name).toBe('苹果')
  expect(wrapper.findComponent({ name: 'DocumentItemBindings' }).props('canWrite')).toBe(true)
  expect(wrapper.findComponent({ name: 'DocumentItemBindings' }).props('notifyConfirmed')).toBe(notifyBindingsConfirmed)
  await wrapper.setProps({ busy: true })
  expect(wrapper.findComponent({ name: 'DocumentItemBindings' }).props('canWrite')).toBe(true)
  await wrapper.setProps({ busy: false })
  await flushPromises()
  const documents = wrapper.props('documents')!
  await wrapper.setProps({ documents: { ...documents, documents: [...documents.documents, {
    ...documents.documents[0]!, id: 'another-doc', parse_status: 'pending',
  }] } })
  await wrapper.setProps({ documents: { ...documents, documents: [...documents.documents, {
    ...documents.documents[0]!, id: 'another-doc', parse_status: 'complete',
  }] } })
  await flushPromises()
  expect(wrapper.findComponent({ name: 'DocumentItemBindings' }).exists(), 'other file parsing must preserve this editor').toBe(true)
  await wrapper.setProps({ canWrite: false })
  expect(wrapper.findComponent({ name: 'DocumentItemBindings' }).props('canWrite')).toBe(false)
  wrapper.unmount()
})

test('buyer requirements have no supplier attribution entry and historical supplier entries are read only', async () => {
  const document = { id: 'doc', original_name: '需求.xlsx', role: 'requirement' as const,
    supplier_handle: null, supplier_name: null, model_name: null, parse_status: 'complete', source_sha256: 'a'.repeat(64) }
  const documents = { case_id: 'case', revision: 1, snapshot_id: 'snap', documents: [document] }
  const wrapper = mount(DocumentsPanel, { props: { selectedCase: { id: 'case', project_id: 'project', revision: 2, created_at: '' }, documents,
    snapshots: [], nextSnapshotBefore: null, loadingOlder: false, selectedHistoryId: null, historyDocuments: null, historyLoading: false, historyError: '', canWrite: true, busy: false },
    global: { stubs: { DocumentItemBindings: { name: 'DocumentItemBindings', props: ['historical'], template: '<div />' } } } })
  expect(wrapper.findAll('button').some(button => button.text() === '关联采购品目')).toBe(false)
  await wrapper.setProps({ selectedHistoryId: 'snap', historyDocuments: { ...documents,
    documents: [{ ...document, role: 'quote', supplier_handle: 'vendor' }] } })
  await wrapper.findAll('button').find(button => button.text() === '查看品目对应')!.trigger('click')
  expect(wrapper.findComponent({ name: 'DocumentItemBindings' }).props('historical')).toBe(true)
  wrapper.unmount()
})
test('generic supplier materials can upload without inventing an electronic device model', async () => {
  const wrapper = mount(DocumentsPanel, { props: { selectedCase: { id: 'case-a', project_id: 'project-a', revision: 0, created_at: '' }, documents: null,
    snapshots: [], nextSnapshotBefore: null, loadingOlder: false, selectedHistoryId: null, historyDocuments: null, historyLoading: false, historyError: '', canWrite: true, busy: false } })
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
    selectedHistoryId: null, historyDocuments: null, historyLoading: false, historyError: '', canWrite: true, busy: false,
  } })
  expect(wrapper.text()).not.toContain('未绑定型号')
  expect(wrapper.text()).toContain(modelName ? `产品标识 ${modelName}` : '未设置产品标识')
  wrapper.unmount()
})

test('historical loading shows the selected revision and an unknown document count', () => {
  const wrapper = mount(DocumentsPanel, { props: {
    selectedCase: { id: 'case', project_id: 'project', revision: 9, created_at: '' },
    documents: { case_id: 'case', revision: 9, snapshot_id: 'current', documents: [] },
    snapshots: [{ id: 'older', revision: 7, document_count: 2, created_at: '' }],
    nextSnapshotBefore: null, loadingOlder: false, selectedHistoryId: 'older', historyDocuments: null,
    historyLoading: true, historyError: '', canWrite: true, busy: false,
  } })
  expect(wrapper.find('.document-list-head > div > span').text()).toBe('— 份')
  expect(wrapper.find('.history-notice').text()).toContain('历史版本 7')
  expect(wrapper.find('.revision-tag').text()).toBe('资料版本 7')
  expect(wrapper.find('[role="status"]').text()).toBe('正在读取历史资料…')
  expect(wrapper.find('.empty-state').exists()).toBe(false)
  wrapper.unmount()
})

test('historical failure offers a local retry and return without suggesting the snapshot is empty', async () => {
  const wrapper = mount(DocumentsPanel, { props: {
    selectedCase: { id: 'case', project_id: 'project', revision: 9, created_at: '' },
    documents: { case_id: 'case', revision: 9, snapshot_id: 'current', documents: [] },
    snapshots: [{ id: 'older', revision: 7, document_count: 2, created_at: '' }],
    nextSnapshotBefore: null, loadingOlder: false, selectedHistoryId: 'older', historyDocuments: null,
    historyLoading: false, historyError: '历史资料加载失败，请重试或返回当前版本。', canWrite: true, busy: false,
  } })
  expect(wrapper.find('[role="status"]').exists()).toBe(false)
  expect(wrapper.find('.empty-state').exists()).toBe(false)
  const alert = wrapper.get('[role="alert"]')
  expect(alert.text()).toContain('历史资料加载失败')
  await alert.findAll('button').find(button => button.text() === '重试')!.trigger('click')
  expect(wrapper.emitted('refreshDocuments')).toHaveLength(1)
  await alert.findAll('button').find(button => button.text() === '返回当前版本')!.trigger('click')
  expect(wrapper.emitted('selectHistory')).toEqual([[null]])
  expect(wrapper.find('form').exists()).toBe(true)
  await wrapper.setProps({ historyError: '', historyDocuments: { case_id: 'case', revision: 7, snapshot_id: 'older', documents: [] } })
  expect(wrapper.find('.document-list-head > div > span').text()).toBe('0 份')
  expect(wrapper.get('.empty-state').text()).toContain('该历史版本没有资料')
  await wrapper.setProps({ canWrite: false })
  expect(wrapper.find('form').exists()).toBe(false)
  expect(wrapper.text()).toContain('只读权限')
  wrapper.unmount()
})
