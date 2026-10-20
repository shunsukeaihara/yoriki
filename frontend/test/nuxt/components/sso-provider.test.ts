import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SsoProviderForm } from '#components'

const { create, patch } = vi.hoisted(() => ({ create: vi.fn(), patch: vi.fn() }))

mockNuxtImport('useAPI', () => () => ({
  manage: { ssoProviders: () => ({ create, patch }) },
}))

const microsoft: SSOProvider = {
  id: 's1',
  kind: SSOKind.MICROSOFT,
  kindDisplay: 'Microsoft 365',
  clientId: 'client-1',
  hasClientSecret: true,
  clientSecretHint: 'abcd',
  tenantId: 'tenant-1',
  hostedDomain: '',
  allowedEmailDomains: ['example.co.jp', 'sub.example.co.jp'],
  callbackUrl: 'https://yoriki.example.co.jp/api/accounts/microsoft/login/callback/',
  isActive: true,
  createdBy: 'u1',
  created: '2026-10-20T00:00:00Z',
  updated: '2026-10-20T00:00:00Z',
}

type Form = InstanceType<typeof SsoProviderForm>

describe('SsoProviderForm', () => {
  beforeEach(() => {
    create.mockReset().mockResolvedValue(microsoft)
    patch.mockReset().mockResolvedValue(microsoft)
  })

  it('更新では client secret を入力しなければ送らず、種別も送らない', async () => {
    const wrapper = await mountSuspended(SsoProviderForm, { props: { modelValue: microsoft } })
    expect(wrapper.text()).toContain(microsoft.callbackUrl)
    expect(wrapper.find('textarea').element.value).toBe('example.co.jp\nsub.example.co.jp')
    await (wrapper.vm as unknown as Form).onSubmit()
    const data = patch.mock.calls[0]![0]
    expect(data).not.toHaveProperty('clientSecret')
    expect(data).not.toHaveProperty('kind')
    expect(data).toMatchObject({
      tenantId: 'tenant-1',
      hostedDomain: '',
      allowedEmailDomains: microsoft.allowedEmailDomains,
    })

    await wrapper.find('input[type="password"]').setValue('new-secret')
    await (wrapper.vm as unknown as Form).onSubmit()
    expect(patch.mock.calls[1]![0]).toMatchObject({ clientSecret: 'new-secret' })
  })

  it('作成ではドメインを配列にして送る', async () => {
    const wrapper = await mountSuspended(SsoProviderForm, { props: { modelValue: null } })
    const texts = wrapper.findAll('input[type="text"]')
    await texts[0]!.setValue('client-2')
    await wrapper.find('input[type="password"]').setValue('secret')
    await texts[1]!.setValue('tenant-2')
    await wrapper.find('textarea').setValue('example.co.jp\nother.co.jp')
    expect((wrapper.vm as unknown as Form).canPost).toBe(true)
    await (wrapper.vm as unknown as Form).onSubmit()
    expect(create).toHaveBeenCalledWith({
      kind: SSOKind.MICROSOFT,
      clientId: 'client-2',
      clientSecret: 'secret',
      tenantId: 'tenant-2',
      hostedDomain: '',
      allowedEmailDomains: ['example.co.jp', 'other.co.jp'],
      isActive: true,
    })
  })
})
