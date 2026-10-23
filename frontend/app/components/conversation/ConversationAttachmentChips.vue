<script setup lang="ts">
/** 入力欄で添付したファイル。アップロード中・失敗を示し、送る前なら外せる */
defineProps<{ items: PendingAttachment[] }>()
const emit = defineEmits<{ remove: [key: string] }>()
</script>

<template>
  <ul v-if="items.length" class="flex flex-wrap gap-2">
    <li
      v-for="item in items"
      :key="item.key"
      class="flex max-w-64 items-center gap-1.5 rounded-md border px-2 py-1 text-xs"
      :class="item.error ? 'border-error text-error' : 'border-default'"
    >
      <UIcon
        :name="item.uploading ? 'i-lucide-loader-circle' : item.error ? 'i-lucide-circle-alert' : 'i-lucide-paperclip'"
        class="size-3.5 shrink-0"
        :class="{ 'animate-spin': item.uploading }"
      />
      <UTooltip :text="item.error || item.file.name">
        <span class="truncate">{{ item.file.name }}</span>
      </UTooltip>
      <span class="shrink-0 text-dimmed">{{ formatFileSize(item.file.size) }}</span>
      <DBtn
        icon="i-lucide-x"
        size="xs"
        variant="link"
        color="neutral"
        class="-me-1 p-0.5"
        :aria-label="$t('添付を外す')"
        @click="emit('remove', item.key)"
      />
    </li>
  </ul>
</template>
