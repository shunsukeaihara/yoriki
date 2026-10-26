import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DepartmentMemberForm } from '#components'

const { create } = vi.hoisted(() => ({ create: vi.fn() }))

mockNuxtImport('useAPI', () => () => ({
  manage: {
    departments: () => ({ members: () => ({ create }) }),
    users: () => ({ list: () => Promise.resolve({ results: [], next: null, previous: null }) }),
  },
}))

const membership: DepartmentMembership = {
  id: 'm1',
  user: 'u1',
  userInfo: { id: 'u1', email: 'taro@example.co.jp', displayName: '太郎' },
  created: '2026-10-24T00:00:00Z',
  updated: '2026-10-24T00:00:00Z',
}

type Form = InstanceType<typeof DepartmentMemberForm> & { form: { user: string | null } }

describe('DepartmentMemberForm', () => {
  beforeEach(() => {
    create.mockReset().mockResolvedValue(membership)
  })

  it('追加では社員だけを送る（部門の中の役割は持たない）', async () => {
    const wrapper = await mountSuspended(DepartmentMemberForm, { props: { departmentId: 'd1' } })
    const vm = wrapper.vm as unknown as Form
    expect(vm.canPost).toBe(false)
    vm.form.user = 'u2'
    await vm.onSubmit()
    expect(create).toHaveBeenCalledWith({ user: 'u2' })
    expect(wrapper.emitted('created')).toHaveLength(1)
  })
})
