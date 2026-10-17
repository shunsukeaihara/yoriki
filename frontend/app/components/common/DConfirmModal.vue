<script setup lang="ts">
/** useConfirmDialog() から開く確認ダイアログ。OK で true、キャンセルで false を返す */
withDefaults(
  defineProps<{
    title: string
    description?: string
    confirmLabel?: string
    cancelLabel?: string
    color?: 'primary' | 'error'
  }>(),
  { color: 'primary' },
)
const emit = defineEmits<{ close: [value: boolean] }>()
</script>

<template>
  <UModal :title="title" :description="description" :close="false" :dismissible="false">
    <template #footer>
      <div class="flex w-full justify-end gap-2">
        <DBtn color="neutral" variant="ghost" @click="emit('close', false)">
          {{ cancelLabel ?? $t('キャンセル') }}
        </DBtn>
        <DBtn :color="color" @click="emit('close', true)">{{ confirmLabel ?? $t('OK') }}</DBtn>
      </div>
    </template>
  </UModal>
</template>
