<script setup lang="ts">
/** 入口の受け取り方の版の履歴（読み取り専用）。新しい版から順に並べる */
const props = defineProps<{ bindingId: string }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const api = useAPI()

const {
  items: revisions,
  loading,
  fetchAll,
} = useFetchAll(() => api.manage.triggerBindings(props.bindingId).revisions().list())
watch(reloadKey, fetchAll, { immediate: true })
</script>

<template>
  <div class="grid gap-3">
    <p v-if="loading && !revisions.length" class="text-sm text-muted">{{ $t('読み込み中') }}</p>
    <UCard v-for="r in revisions" :key="r.id" :ui="{ body: 'p-3 sm:p-3' }">
      <div class="flex flex-wrap items-center gap-2">
        <span class="font-semibold">{{ $t('版 {number}', { number: r.number }) }}</span>
        <div class="flex-1" />
        <span class="text-xs text-muted">
          {{ r.createdBy.displayName || r.createdBy.email }}・{{ formatDatetime(r.created) }}
        </span>
      </div>
      <p v-if="r.changeReason" class="mt-1 whitespace-pre-wrap text-sm">{{ r.changeReason }}</p>
      <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt class="text-muted">{{ $t('イベントIDの場所') }}</dt>
        <dd class="font-mono break-all">{{ r.eventIdPath || '-' }}</dd>
        <dt class="text-muted">{{ $t('項目の取り出し') }}</dt>
        <dd class="font-mono break-all">
          <template v-if="r.inputs.length">
            <div v-for="i in r.inputs" :key="i.name">{{ i.name }} ← {{ i.path }}</div>
          </template>
          <template v-else>{{ $t('推奨の形式の inputs') }}</template>
        </dd>
        <dt class="text-muted">{{ $t('payloadの上限') }}</dt>
        <dd>{{ formatFileSize(r.maxPayloadBytes) }}</dd>
      </dl>
    </UCard>
  </div>
</template>
