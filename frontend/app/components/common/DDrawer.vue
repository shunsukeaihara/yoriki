<script setup lang="ts">
/** 右から出る詳細パネル。一覧ページの子ルートで詳細を表示するのに使う */
const open = defineModel<boolean>('open', { default: false })
defineProps<{ title?: string; description?: string }>()
defineOptions({ inheritAttrs: false })
</script>

<template>
  <USlideover v-model:open="open" :title="title" :description="description" v-bind="$attrs">
    <template v-if="$slots.header" #title><slot name="header" /></template>
    <template #body><slot name="content" /></template>
    <template v-if="$slots.footer" #footer>
      <div class="flex w-full items-center gap-2">
        <slot name="footer" />
      </div>
    </template>
  </USlideover>
</template>
