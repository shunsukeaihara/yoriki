import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RuntimeProfileForm } from '#components'

const { create, patch } = vi.hoisted(() => ({ create: vi.fn(), patch: vi.fn() }))

mockNuxtImport('useAPI', () => () => ({
  manage: { runtimeProfiles: () => ({ create, patch }) },
}))

const IMAGE = `docker.io/yoriki/yoriki-sandbox-docs@sha256:${'a'.repeat(64)}`
const profile: RuntimeProfile = {
  id: 'r1',
  name: '文書処理',
  isActive: true,
  isDefault: true,
  version: 3,
  image: IMAGE,
  contract: 1,
  platforms: ['linux/amd64', 'linux/arm64'],
  pythonVersion: '3.13.16',
  memoryMb: 2048,
  cpus: '2.00',
  pidsLimit: 256,
  tmpMb: 256,
  workMb: 512,
  outputMb: 256,
  envMb: 512,
  commandTimeoutSeconds: 600,
  jobTimeoutSeconds: 1800,
  createdBy: 'u1',
  created: '2026-10-22T00:00:00Z',
  updated: '2026-10-22T00:00:00Z',
}

type Form = InstanceType<typeof RuntimeProfileForm> & { form: { memoryMb: number | null; image: string } }

describe('RuntimeProfileForm', () => {
  beforeEach(() => {
    create.mockReset().mockResolvedValue(profile)
    patch.mockReset().mockResolvedValue(profile)
  })

  it('作成では既定の上限を送り、空にした上限は送らない', async () => {
    const wrapper = await mountSuspended(RuntimeProfileForm, { props: { modelValue: null } })
    const vm = wrapper.vm as unknown as Form
    vm.form.image = ` ${IMAGE} `
    vm.form.memoryMb = null
    await wrapper.findAll('input[type="text"]')[0]!.setValue('文書処理')
    await vm.onSubmit()
    const data = create.mock.calls[0]![0]
    expect(data).toMatchObject({ name: '文書処理', image: IMAGE, cpus: '2.00', envMb: 512 })
    expect(data).not.toHaveProperty('memoryMb')
  })

  it('更新では、編集を始めた時点の版を送る', async () => {
    const wrapper = await mountSuspended(RuntimeProfileForm, { props: { modelValue: profile } })
    await (wrapper.vm as unknown as Form).onSubmit()
    expect(patch.mock.calls[0]![0]).toMatchObject({ version: 3, image: IMAGE, memoryMb: 2048 })
    expect(wrapper.text()).toContain('linux/amd64, linux/arm64')
  })
})
