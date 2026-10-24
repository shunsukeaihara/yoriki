<script setup lang="ts">
/** Skillの公開・切戻し・停止・再開の記録。変わった項目と資料の key を出す */
import type { TableColumn } from '@nuxt/ui'

defineProps<{ skillId: string }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const api = useAPI()
const { t } = useI18n()
const params = ref<ListParams>({})

const columns: TableColumn<SkillChange>[] = [
  { accessorKey: 'created', header: t('日時') },
  { accessorKey: 'kindDisplay', header: t('操作') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'summary', header: t('変更点') },
  { accessorKey: 'actorInfo', header: t('操作者') },
]

const summaryLines = (change: SkillChange): string[] => skillChangeLines(change.summary, t)
</script>

<template>
  <DDataTable
    v-model:reload-key="reloadKey"
    :columns="columns"
    :fetch="api.skills(skillId).changes().list"
    :params="params"
  >
    <template #created-cell="{ row }">{{ formatDatetime(row.original.created) }}</template>
    <template #version-cell="{ row }">
      <span v-if="row.original.version">
        {{
          row.original.previousVersion
            ? $t('{from} → {to}', { from: row.original.previousVersion, to: row.original.version })
            : row.original.version
        }}
      </span>
    </template>
    <template #summary-cell="{ row }">
      <div class="grid gap-0.5 text-xs">
        <span v-for="line in summaryLines(row.original)" :key="line">{{ line }}</span>
        <span v-if="row.original.reason" class="whitespace-pre-wrap text-muted">{{ row.original.reason }}</span>
      </div>
    </template>
    <template #actorInfo-cell="{ row }">
      {{ row.original.actorInfo.displayName || row.original.actorInfo.email }}
    </template>
  </DDataTable>
</template>
