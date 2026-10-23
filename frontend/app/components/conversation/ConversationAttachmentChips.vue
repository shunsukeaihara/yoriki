<script setup lang="ts">
/** 入力欄で添付したファイル。アップロード中・失敗を示し、送る前なら外せる。失敗したものは送り直せる */
defineProps<{ items: PendingAttachment[] }>()
const emit = defineEmits<{ remove: [key: string]; retry: [] }>()
</script>

<template>
  <div v-if="items.length" class="grid gap-1">
    <ul class="flex flex-wrap gap-2">
      <li
        v-for="item in items"
        :key="item.key"
        class="flex max-w-64 items-center gap-1.5 rounded-md border px-2 py-1 text-xs"
        :class="item.error ? 'border-error text-error' : 'border-default'"
      >
        <UIcon
          :name="
            item.uploading ? 'i-lucide-loader-circle' : item.error ? 'i-lucide-circle-alert' : 'i-lucide-paperclip'
          "
          class="size-3.5 shrink-0"
          :class="{ 'animate-spin': item.uploading }"
        />
        <span class="truncate" :title="item.error || item.file.name">{{ item.file.name }}</span>
        <span class="shrink-0 text-dimmed">{{ formatFileSize(item.file.size) }}</span>
        <DBtn
          v-if="item.error"
          icon="i-lucide-rotate-cw"
          size="xs"
          variant="link"
          color="error"
          class="p-0.5"
          :aria-label="$t('アップロードし直す')"
          @click="emit('retry')"
        />
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
    <p v-for="item in items.filter((i) => i.error)" :key="item.key" class="text-xs text-error">
      {{ item.file.name }}：{{ item.error }}
    </p>
  </div>
</template>
