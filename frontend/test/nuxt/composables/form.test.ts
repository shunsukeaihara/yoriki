import { AxiosError, AxiosHeaders, type AxiosResponse } from 'axios'
import { describe, expect, it, vi } from 'vitest'

const axiosError = (status: number, data: unknown, contentType = 'application/json') => {
  const headers = new AxiosHeaders({ 'content-type': contentType })
  const response = { status, data, headers, statusText: '', config: { headers } } as AxiosResponse
  return new AxiosError('error', 'ERR_BAD_REQUEST', undefined, undefined, response)
}

describe('getValidationError', () => {
  it('400 はフィールドごとのエラーを camelCase にして返す', () => {
    const error = axiosError(400, { display_name: ['必須です。'], non_field_errors: ['重複しています。'] })
    expect(getValidationError(error)).toEqual({ displayName: ['必須です。'], nonFieldErrors: ['重複しています。'] })
  })

  it('400 で配列が返ったら nonFieldErrors にする', () => {
    expect(getValidationError(axiosError(400, ['期間が逆です。']))).toEqual({ nonFieldErrors: ['期間が逆です。'] })
  })

  it('400 以外は detail を nonFieldErrors にする', () => {
    expect(getValidationError(axiosError(403, { detail: '権限がありません。' }))).toEqual({
      nonFieldErrors: ['権限がありません。'],
    })
  })

  it('応答がないときは接続できなかった旨を返す', () => {
    const error = new AxiosError('Network Error', 'ERR_NETWORK')
    expect(getValidationError(error).nonFieldErrors?.[0]).toContain('サーバーに接続できませんでした')
  })

  it('JSON でない応答や AxiosError 以外は不明なエラーにする', () => {
    expect(getValidationError(axiosError(502, '<html>', 'text/html')).nonFieldErrors?.[0]).toContain('不明なエラー')
    expect(getValidationError(new Error('x')).nonFieldErrors?.[0]).toContain('不明なエラー')
  })
})

describe('makeErrorMsg', () => {
  it('400 は入力の修正を促し、それ以外は detail を返す', () => {
    expect(makeErrorMsg(axiosError(400, { name: ['必須です。'] }))).toBe('入力に問題があります。修正してください。')
    expect(makeErrorMsg(axiosError(409, { detail: '実行中です。' }))).toBe('実行中です。')
  })
})

describe('createSubmit', () => {
  it('成功したら onSuccess に結果を渡し、loading を戻す', async () => {
    const form = ref({ name: 'a' })
    const fn = vi.fn(async (data: { name: string }) => ({ id: '1', ...data }))
    const onSuccess = vi.fn()
    const { onSubmit, loading, errors } = createSubmit({ form, fn, onSuccess })

    const pending = onSubmit()
    expect(loading.value).toBe(true)
    await pending
    expect(loading.value).toBe(false)
    expect(fn).toHaveBeenCalledWith({ name: 'a' })
    expect(onSuccess).toHaveBeenCalledWith({ id: '1', name: 'a' })
    expect(errors.value).toEqual({})
  })

  it('失敗したら errors に DRF のエラーを入れ、onError を呼ぶ。次の送信でエラーを消す', async () => {
    const form = ref({ name: '' })
    const error = axiosError(400, { name: ['必須です。'] })
    const fn = vi.fn().mockRejectedValueOnce(error).mockResolvedValueOnce({})
    const onError = vi.fn()
    const { onSubmit, loading, errors } = createSubmit({ form, fn, onError })

    await onSubmit()
    expect(loading.value).toBe(false)
    expect(errors.value).toEqual({ name: ['必須です。'] })
    expect(onError).toHaveBeenCalledWith(error)

    await onSubmit()
    expect(errors.value).toEqual({})
  })
})

describe('flattenRowErrors', () => {
  it('行ごとのエラーに行番号を付けて平坦にする', () => {
    expect(flattenRowErrors({ '0': { name: ['必須です。'] }, '2': { price: ['数値にしてください。'] } })).toEqual([
      '#1: 必須です。',
      '#3: 数値にしてください。',
    ])
  })

  it('旧リスト形式と文字列の配列にも対応する', () => {
    expect(flattenRowErrors([{}, { name: ['必須です。'] }])).toEqual(['#2: 必須です。'])
    expect(flattenRowErrors(['1件以上必要です。'])).toEqual(['1件以上必要です。'])
    expect(flattenRowErrors(undefined)).toEqual([])
  })
})
