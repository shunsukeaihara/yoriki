<script setup lang="ts">
/**
 * 添付・成果物の1件。クリックするとダウンロードする（サーバーはいつも添付として返し、同じ origin で開かない）
 */
const props = defineProps<{ conversationId: string; artifact: Artifact; showPath?: boolean }>()
const api = useAPI()
const url = computed(() => api.conversations(props.conversationId).artifacts(props.artifact.id).downloadUrl())
</script>

<template>
  <a
    :href="url"
    download
    class="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-elevated"
    :title="showPath && artifact.path ? artifact.path : artifact.name"
  >
    <UIcon :name="fileIcon(artifact.contentType)" class="size-4 shrink-0 text-muted" />
    <span class="min-w-0 flex-1 truncate">{{ showPath && artifact.path ? artifact.path : artifact.name }}</span>
    <span class="shrink-0 text-xs text-dimmed">{{ formatFileSize(artifact.size) }}</span>
    <UIcon name="i-lucide-download" class="size-3.5 shrink-0 text-dimmed" />
  </a>
</template>
