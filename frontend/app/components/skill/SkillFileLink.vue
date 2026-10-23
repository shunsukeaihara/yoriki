<script setup lang="ts">
/** Skillの資料の原本。クリックするとダウンロードする（サーバーはいつも添付として返す） */
const props = defineProps<{ skillId: string; file: ResourceVersion }>()
const api = useAPI()
const url = computed(() => api.skills(props.skillId).fileUrl(props.file.id))
</script>

<template>
  <a :href="url" download class="inline-flex min-w-0 items-center gap-1.5 text-sm text-primary hover:underline">
    <UIcon :name="fileIcon(file.contentType)" class="size-4 shrink-0" />
    <span class="truncate">{{ file.name }}</span>
    <span class="shrink-0 text-xs text-dimmed">
      {{ $t('第{number}版', { number: file.number }) }}・{{ formatFileSize(file.size) }}
    </span>
  </a>
</template>
