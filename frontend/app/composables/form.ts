import { AxiosError } from 'axios'
import type { Ref } from 'vue'

/**
 * DRF のエラー応答（camelCase 変換済み）。フィールド名ごとのメッセージ配列と nonFieldErrors。
 * many=True のネストしたフィールドは配列ではなくオブジェクトになるので、flattenRowErrors() で平坦にして表示する。
 */
export type ValidationErrors = { nonFieldErrors?: string[] } & Record<string, string[] | undefined>

const isJson = (error: AxiosError) => String(error.response?.headers['content-type'] ?? '').includes('application/json')

/**
 * API のエラーを、フォームに表示するエラー辞書に変換する。
 * - 400: DRF のフィールドごとのエラーをそのまま返す
 * - それ以外: detail を nonFieldErrors に入れる
 */
export const getValidationError = (error: unknown): ValidationErrors => {
  const { $i18n } = useNuxtApp()
  if (!(error instanceof AxiosError))
    return { nonFieldErrors: [$i18n.t('不明なエラーです。管理者に問い合わせてください。')] }
  if (!error.response)
    return { nonFieldErrors: [$i18n.t('サーバーに接続できませんでした。時間をおいて再度お試しください。')] }
  if (!isJson(error)) return { nonFieldErrors: [$i18n.t('不明なエラーです。管理者に問い合わせてください。')] }
  const data = camelProps<unknown>(error.response.data)
  if (error.response.status === 400) {
    // シリアライザの validate() で非フィールドのエラーが文字列配列で返る場合
    return Array.isArray(data) ? { nonFieldErrors: data.map(String) } : (data as ValidationErrors)
  }
  const detail = (data as { detail?: unknown } | null)?.detail
  return { nonFieldErrors: [detail ? String(detail) : $i18n.t('不明なエラーです。管理者に問い合わせてください。')] }
}

/** トーストに出す短いエラー文 */
export const makeErrorMsg = (error: unknown): string => {
  const { $i18n } = useNuxtApp()
  if (error instanceof AxiosError && error.response?.status === 400)
    return $i18n.t('入力に問題があります。修正してください。')
  return getValidationError(error).nonFieldErrors?.[0] ?? $i18n.t('不明なエラーです。管理者に問い合わせてください。')
}

export type CreateSubmitOption<T, F> = {
  form: Ref<F>
  fn: (data: F) => Promise<T>
  loading?: Ref<boolean>
  errors?: Ref<ValidationErrors>
  onSuccess?: (result: T) => void
  onError?: (error: unknown) => void
}

/** 送信・loading・DRF のエラー辞書をまとめて扱う */
export const createSubmit = <T, F>({ form, fn, loading, errors, onSuccess, onError }: CreateSubmitOption<T, F>) => {
  const isLoading = loading ?? ref(false)
  const errDict = errors ?? ref<ValidationErrors>({})
  const onSubmit = async () => {
    isLoading.value = true
    errDict.value = {}
    try {
      const result = await fn(form.value)
      onSuccess?.(result)
    } catch (error) {
      errDict.value = getValidationError(error)
      onError?.(error)
    } finally {
      isLoading.value = false
    }
  }
  return { onSubmit, loading: isLoading, errors: errDict }
}

const collectMessages = (value: unknown): string[] => {
  if (!value) return []
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(collectMessages)
  if (typeof value === 'object') return Object.values(value).flatMap(collectMessages)
  return [String(value)]
}

/**
 * many=True のネストしたシリアライザが返す行単位のエラーを string[] に平坦化する。
 * dict 形式 { "1": { field: [...] } } と旧リスト形式 [{}, { field: [...] }] の両方に対応する。
 */
export const flattenRowErrors = (
  errors: unknown,
  formatRow: (index: number, message: string) => string = (i, m) => `#${i + 1}: ${m}`,
): string[] => {
  if (!errors) return []
  if (Array.isArray(errors) && errors.every((e) => typeof e === 'string')) return errors
  if (typeof errors !== 'object') return [String(errors)]
  return Object.entries(errors).flatMap(([key, row]) => {
    const index = Number(key)
    const messages = collectMessages(row)
    return Number.isNaN(index) ? messages : messages.map((m) => formatRow(index, m))
  })
}
