import { mountSuspended } from '@nuxt/test-utils/runtime'
import { describe, expect, it } from 'vitest'
import { DCheckbox, DDecimalField, DFormAlert, DTextField } from '#components'

describe('DTextField', () => {
  it('null を空文字で表示し、入力した値を v-model に返す', async () => {
    const wrapper = await mountSuspended(DTextField, { props: { modelValue: null, label: '名前' } })
    const input = wrapper.find('input')
    expect(input.element.value).toBe('')

    await input.setValue('Yoriki')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['Yoriki'])
  })

  it('ラベルと必須マーク、DRF のエラーメッセージを表示する', async () => {
    const wrapper = await mountSuspended(DTextField, {
      props: { modelValue: 'a', label: '名前', required: true, hint: '50文字まで', errorMessages: ['必須です。'] },
    })
    expect(wrapper.text()).toContain('名前')
    expect(wrapper.text()).toContain('必須です。')
    // エラーがある間は、ヒントの代わりにエラーを出す
    expect(wrapper.text()).not.toContain('50文字まで')
    expect(wrapper.find('input').attributes('aria-invalid')).toBe('true')
  })

  it('placeholder 等の属性は入力欄に渡す', async () => {
    const wrapper = await mountSuspended(DTextField, { props: { modelValue: '' }, attrs: { placeholder: '例: 山田' } })
    expect(wrapper.find('input').attributes('placeholder')).toBe('例: 山田')
  })
})

describe('DDecimalField', () => {
  it('DecimalField の文字列を数値で表示し、decimalPlaces 桁の文字列で返す', async () => {
    const wrapper = await mountSuspended(DDecimalField, { props: { modelValue: '1.5', decimalPlaces: 2 } })
    const input = wrapper.find('input')
    expect(input.element.value).toBe('1.5')

    await input.setValue('2.345')
    await input.trigger('blur')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['2.35'])
  })

  it('空にしたら null を返す', async () => {
    const wrapper = await mountSuspended(DDecimalField, { props: { modelValue: '3' } })
    const input = wrapper.find('input')
    await input.setValue('')
    await input.trigger('blur')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([null])
  })
})

describe('DCheckbox', () => {
  it('クリックで boolean を返す', async () => {
    const wrapper = await mountSuspended(DCheckbox, { props: { modelValue: false, label: '有効' } })
    expect(wrapper.text()).toContain('有効')
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([true])
  })
})

describe('DFormAlert', () => {
  it('nonFieldErrors だけを表示する', async () => {
    const wrapper = await mountSuspended(DFormAlert, {
      props: { errors: { nonFieldErrors: ['重複しています。'], name: ['必須です。'] } },
    })
    expect(wrapper.text()).toContain('重複しています。')
    expect(wrapper.text()).not.toContain('必須です。')
  })

  it('nonFieldErrors がなければ何も表示しない', async () => {
    const wrapper = await mountSuspended(DFormAlert, { props: { errors: { name: ['必須です。'] } } })
    expect(wrapper.text()).toBe('')
  })
})
