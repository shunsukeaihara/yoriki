import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DepartmentMemberForm } from '#components'

const { create, patch } = vi.hoisted(() => ({ create: vi.fn(), patch: vi.fn() }))

mockNuxtImport('useAPI', () => () => ({
  manage: {
    departments: () => ({ members: () => ({ create, patch }) }),
    users: () => ({ list: () => Promise.resolve({ results: [], next: null, previous: null }) }),
  },
}))

const membership: DepartmentMembership = {
  id: 'm1',
  user: 'u1',
  userInfo: { id: 'u1', email: 'taro@example.co.jp', displayName: '太郎' },
  role: 1,
  roleDisplay: '利用',
  created: '2026-10-24T00:00:00Z',
  updated: '2026-10-24T00:00:00Z',
}

type Form = InstanceType<typeof DepartmentMemberForm> & { form: { user: string | null; role: number } }

describe('DepartmentMemberForm', () => {
  beforeEach(() => {
    create.mockReset().mockResolvedValue(membership)
    patch.mockReset().mockResolvedValue(membership)
  })

  it('追加では社員と役割を送る', async () => {
    const wrapper = await mountSuspended(DepartmentMemberForm, { props: { modelValue: null, departmentId: 'd1' } })
    const vm = wrapper.vm as unknown as Form
    expect(vm.canPost).toBe(false)
    vm.form.user = 'u2'
    vm.form.role = DepartmentRole.EDITOR
    await vm.onSubmit()
    expect(create).toHaveBeenCalledWith({ user: 'u2', role: DepartmentRole.EDITOR })
    expect(wrapper.emitted('created')).toHaveLength(1)
  })

  it('変更では役割だけを送る（社員は変えない）', async () => {
    const wrapper = await mountSuspended(DepartmentMemberForm, {
      props: { modelValue: membership, departmentId: 'd1' },
    })
    const vm = wrapper.vm as unknown as Form
    vm.form.role = DepartmentRole.PUBLISHER
    await vm.onSubmit()
    expect(patch).toHaveBeenCalledWith({ role: DepartmentRole.PUBLISHER })
    expect(wrapper.text()).toContain('太郎')
  })
})
