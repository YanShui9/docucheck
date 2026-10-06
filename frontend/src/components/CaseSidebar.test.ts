// @vitest-environment jsdom
import { expect, test } from 'vitest'
import { mount } from '@vue/test-utils'
import CaseSidebar from './CaseSidebar.vue'
test('read-only members can navigate every task without creating one', async () => {
  const wrapper = mount(CaseSidebar, { props: { projects: [], cases: ['a', 'b'].map(id => ({ id, project_id: 'p', revision: 0, created_at: '' })), selectedProjectId: 'p', selectedCaseId: 'a', canWrite: false, busy: false } })
  expect(wrapper.findAll('.case-item')).toHaveLength(2)
  await wrapper.findAll('.case-item')[1]!.trigger('click')
  expect(wrapper.emitted('selectCase')![0]).toEqual(['b'])
  expect(wrapper.get('.new-task-button').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})
