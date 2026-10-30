<script setup lang="ts">
/** 出口の版の履歴（読み取り専用）。新しい版から順に並べる。送り先はホストと末尾の4文字だけ */
const props = defineProps<{ destinationId: string }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const api = useAPI()

const {
  items: revisions,
  loading,
  fetchAll,
} = useFetchAll(() => api.manage.destinations(props.destinationId).revisions().list())
watch(reloadKey, fetchAll, { immediate: true })
</script>

<template>
  <div class="grid gap-3">
    <p v-if="loading && !revisions.length" class="text-sm text-muted">{{ $t('読み込み中') }}</p>
    <UCard v-for="r in revisions" :key="r.id" :ui="{ body: 'p-3 sm:p-3' }">
      <div class="flex flex-wrap items-center gap-2">
        <span class="font-semibold">{{ $t('版 {number}', { number: r.number }) }}</span>
        <UBadge v-if="r.allowsSensitiveResults" color="warning" variant="subtle" size="sm">
          {{ $t('機密の結果を送ってよい') }}
        </UBadge>
        <div class="flex-1" />
        <span class="text-xs text-muted"
          >{{ r.createdBy.displayName || r.createdBy.email }}・{{ formatDatetime(r.created) }}</span
        >
      </div>
      <p v-if="r.changeReason" class="mt-1 whitespace-pre-wrap text-sm">{{ r.changeReason }}</p>
      <dl v-if="r.urlDisplay" class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt class="text-muted">{{ $t('送り先') }}</dt>
        <dd class="font-mono break-all">{{ r.urlDisplay }}</dd>
      </dl>
    </UCard>
  </div>
</template>
