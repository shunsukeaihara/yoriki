<script setup lang="ts">
/**
 * ダイアログ。
 * #trigger="{ on }" で開くボタンを受け取り、#footer="{ closeDialog }" に操作ボタンを置く。
 */
const open = defineModel<boolean>('open', { default: false })
const openDialog = () => (open.value = true)
const closeDialog = () => (open.value = false)
defineExpose({ openDialog, closeDialog })
defineOptions({ inheritAttrs: false })
</script>

<template>
  <slot name="trigger" :on="{ click: openDialog }" />
  <UModal v-model:open="open" v-bind="$attrs">
    <template #title><slot name="header" /></template>
    <template #body><slot name="content" /></template>
    <template v-if="$slots.footer" #footer>
      <div class="flex w-full justify-end gap-2">
        <slot name="footer" :close-dialog="closeDialog" />
      </div>
    </template>
  </UModal>
</template>
