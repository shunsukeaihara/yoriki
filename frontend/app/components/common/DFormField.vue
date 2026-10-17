<script setup lang="ts">
/**
 * 入力コンポーネントの共通の枠。ラベル・必須マーク・ヒント・DRF のエラーメッセージを同じ形で表示する。
 * 各 D*Field は必ずこのコンポーネントで入力欄を包む。
 */
const props = withDefaults(
  defineProps<{
    label?: string
    /** 入力欄の下に出す補足。エラーがある間はエラーに置き換わる */
    hint?: string
    /** ラベルの下に出す説明 */
    description?: string
    /** DRF のフィールドエラー（`errors.<field>`） */
    errorMessages?: string[]
    required?: boolean
    showLabel?: boolean
    name?: string
    class?: ClassProp
  }>(),
  { showLabel: true, required: false },
)
const error = useFieldError(() => props.errorMessages)
</script>

<template>
  <UFormField
    :name="name"
    :label="showLabel ? label : undefined"
    :description="description"
    :help="hint"
    :error="error"
    :required="required"
    :class="['w-full max-w-96', props.class]"
    :ui="{ error: 'whitespace-pre-line' }"
  >
    <template v-if="$slots.label" #label><slot name="label" /></template>
    <template v-if="$slots.hint" #help><slot name="hint" /></template>
    <slot />
  </UFormField>
</template>
