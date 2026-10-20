<script setup lang="ts">
/** 一覧画面の見出し。#end に追加ボタン等を置く */
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <div class="mb-4 flex flex-wrap items-end justify-between gap-3">
    <div>
      <h1 class="text-xl font-bold text-highlighted">{{ title }}</h1>
      <p v-if="description" class="mt-1 text-sm text-muted">{{ description }}</p>
    </div>
    <div v-if="$slots.end" class="flex items-center gap-2">
      <slot name="end" />
    </div>
  </div>
</template>
