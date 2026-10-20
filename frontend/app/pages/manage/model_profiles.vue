<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: PermissionCode.CHANGE_MODEL_PROFILE })

const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): ModelProfileListParams => ({ name: undefined, connection: undefined, isActive: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<ModelProfile>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'connectionInfo', header: t('LLMの接続') },
  { accessorKey: 'modelName', header: t('モデル名') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'isActive', header: t('有効') },
  { accessorKey: 'updated', header: t('更新日時') },
]
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('モデル設定')"
        :description="$t('Agentが選ぶモデルです。中身を変えて保存すると新しい版になり、履歴が残ります')"
      >
        <template #end>
          <ModelProfileDialog @saved="reload">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('モデル設定を追加') }}</DBtn>
            </template>
          </ModelProfileDialog>
        </template>
      </ListPageTitle>

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.name" :label="$t('名前')" />
        <DAutocomplete
          v-model="params.connection"
          :label="$t('LLMの接続')"
          :search-fn="api.manage.llmConnections().list"
          item-value="id"
        />
        <DSelect v-model="params.isActive" :label="$t('有効')" :items="activeItems(t)" clearable />
      </TableSearchForm>

      <DDataTable
        v-model:reload-key="reloadKey"
        :columns="columns"
        :fetch="api.manage.modelProfiles().list"
        :params="params"
      >
        <template #name-cell="{ row }">
          <NuxtLink :to="`/manage/model_profiles/${row.original.id}`" class="text-primary">
            {{ row.original.name }}
          </NuxtLink>
        </template>
        <template #connectionInfo-cell="{ row }">
          {{ row.original.connectionInfo.name }}
          <span class="text-xs text-muted">（{{ row.original.connectionInfo.kindDisplay }}）</span>
        </template>
        <template #modelName-cell="{ row }">
          <span class="font-mono">{{ row.original.modelName }}</span>
        </template>
        <template #isActive-cell="{ row }">
          <UBadge :color="row.original.isActive ? 'success' : 'neutral'" variant="subtle">
            {{ row.original.isActive ? $t('有効') : $t('無効') }}
          </UBadge>
        </template>
        <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
      </DDataTable>
    </UContainer>
    <NuxtPage @changed="reload" />
  </div>
</template>
