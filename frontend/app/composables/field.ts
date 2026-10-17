/** 選択肢から値・表示名を取り出す指定。プロパティ名（`a.b` のようなパスも可）か関数 */
// 型引数の既定を never にして、どんな型の項目を受け取る関数でも渡せるようにする
export type ItemAccessor<T = never> = string | ((item: T) => unknown)

/** ラッパーの外枠に付ける class（Vue の class バインディングと同じ形） */
export type ClassProp = string | Record<string, boolean> | Array<ClassProp | undefined>

/** item から accessor で指定した値を取り出す */
export const getProp = (item: unknown, accessor?: ItemAccessor | null): unknown => {
  if (item === null || item === undefined || !accessor) return item
  if (typeof accessor === 'function') return (accessor as (item: unknown) => unknown)(item)
  if (typeof item !== 'object') return item
  return accessor.split('.').reduce<unknown>((obj, key) => {
    if (obj === null || obj === undefined || typeof obj !== 'object') return undefined
    return (obj as Record<string, unknown>)[key]
  }, item)
}

/** DRF のエラーメッセージ配列を UFormField の error に渡せる形にする */
export const useFieldError = (errorMessages: () => string[] | undefined) =>
  computed(() => {
    const messages = errorMessages()
    return messages && messages.length > 0 ? messages.join('\n') : undefined
  })

/** すべての D*Field が共通で持つ props。DFormField にそのまま渡す */
export type FieldProps = {
  label?: string
  hint?: string
  description?: string
  errorMessages?: string[]
  required?: boolean
  showLabel?: boolean
  class?: ClassProp
}

export const pickFieldProps = (props: FieldProps): FieldProps => ({
  label: props.label,
  hint: props.hint,
  description: props.description,
  errorMessages: props.errorMessages,
  required: props.required,
  showLabel: props.showLabel,
  class: props.class,
})
