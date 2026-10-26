<script setup lang="ts">
/** Connectorの版の履歴（読み取り専用）。新しい版から順に並べる */
const props = defineProps<{ connectorId: string }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const api = useAPI()

const {
  items: versions,
  loading,
  fetchAll,
} = useFetchAll(() => api.manage.connectors(props.connectorId).versions().list())
watch(reloadKey, fetchAll, { immediate: true })
</script>

<template>
  <div class="grid gap-3">
    <p v-if="loading && !versions.length" class="text-sm text-muted">{{ $t('読み込み中') }}</p>
    <UCard v-for="v in versions" :key="v.id" :ui="{ body: 'p-3 sm:p-3' }">
      <div class="flex items-center justify-between gap-2">
        <span class="font-semibold">{{ $t('版 {number}', { number: v.number }) }}</span>
        <UBadge v-if="v.isSensitive" color="warning" variant="subtle" size="sm">{{ $t('機密') }}</UBadge>
        <div class="flex-1" />
        <span class="text-xs text-muted">{{ formatDatetime(v.created) }}</span>
      </div>
      <p v-if="v.changeReason" class="mt-1 whitespace-pre-wrap text-sm">{{ v.changeReason }}</p>
      <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt class="text-muted">{{ $t('説明') }}</dt>
        <dd class="whitespace-pre-wrap">{{ v.description || '-' }}</dd>
        <template v-if="v.defaultSchema">
          <dt class="text-muted">{{ $t('既定のスキーマ') }}</dt>
          <dd class="font-mono">{{ v.defaultSchema }}</dd>
        </template>
        <dt class="text-muted">{{ $t('上限') }}</dt>
        <dd>
          {{
            $t('{rows}行・{size}MB・{seconds}秒', {
              rows: v.maxRows,
              size: bytesToMb(v.maxResultBytes),
              seconds: v.timeoutSeconds,
            })
          }}
        </dd>
        <dt class="text-muted">{{ $t('公開する表・ビュー') }}</dt>
        <dd class="font-mono break-all">
          {{ v.tables.length ? v.tables.map(tableLabel).join(', ') : '-' }}
        </dd>
      </dl>
    </UCard>
  </div>
</template>
