import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ManageUserForm } from '#components'

const { create, patch } = vi.hoisted(() => ({ create: vi.fn(), patch: vi.fn() }))

mockNuxtImport('useAPI', () => () => ({
  manage: {
    users: () => ({ create, patch }),
    groups: { list: () => Promise.resolve([{ name: '業務管理者' }]) },
  },
}))

const user: ManageUser = {
  id: 'u1',
  email: 'taro@example.co.jp',
  displayName: '太郎',
  groups: ['業務管理者'],
  isActive: true,
  isPlatformAdmin: false,
  hasPassword: true,
  ssoProviders: [],
  lastLogin: null,
  created: '2026-10-20T00:00:00Z',
  updated: '2026-10-20T00:00:00Z',
}

type Form = InstanceType<typeof ManageUserForm>

describe('ManageUserForm', () => {
  beforeEach(() => {
    create.mockReset().mockResolvedValue({ ...user, invitation: null })
    patch.mockReset().mockResolvedValue(user)
  })

  it('更新ではメールアドレスを送らない（SSO の紐付けに使うので変えない）', async () => {
    const wrapper = await mountSuspended(ManageUserForm, { props: { modelValue: user } })
    expect(wrapper.find('input[type="email"]').attributes('disabled')).toBeDefined()
    await (wrapper.vm as unknown as Form).onSubmit()
    expect(patch).toHaveBeenCalledWith({ displayName: '太郎', groups: ['業務管理者'], isActive: true })
  })

  it('作成ではメールアドレスを送る', async () => {
    const wrapper = await mountSuspended(ManageUserForm, { props: { modelValue: null } })
    await wrapper.find('input[type="email"]').setValue('hanako@example.co.jp')
    await (wrapper.vm as unknown as Form).onSubmit()
    expect(create).toHaveBeenCalledWith({ email: 'hanako@example.co.jp', displayName: '', groups: [], isActive: true })
    expect(wrapper.emitted('created')).toHaveLength(1)
  })
})
