<script setup lang="ts">
/** 一覧の検索条件。入力欄を並べ、「絞り込む」で reload、「クリア」で clear を呼ぶ */
defineProps<{ reload: () => void; clear: () => void }>()
</script>

<template>
  <form class="mb-4 flex flex-wrap items-end gap-3" @submit.prevent="reload()">
    <div class="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <slot />
    </div>
    <div class="flex gap-2">
      <DBtn type="submit" icon="i-lucide-search">{{ $t('絞り込む') }}</DBtn>
      <DBtn color="neutral" variant="ghost" @click="clear()">{{ $t('クリア') }}</DBtn>
    </div>
  </form>
</template>
