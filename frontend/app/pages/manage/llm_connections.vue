<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: PermissionCode.CHANGE_LLM_CONNECTION })

const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): LLMConnectionListParams => ({ name: undefined, kind: undefined, isActive: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<LLMConnection>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'kindDisplay', header: t('種別') },
  { accessorKey: 'baseUrl', header: t('base URL') },
  { accessorKey: 'apiKeyHint', header: t('API key') },
  { accessorKey: 'isActive', header: t('有効') },
  { accessorKey: 'updated', header: t('更新日時') },
]
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('LLMの接続')"
        :description="$t('Agentが使うLLMのプロバイダと、そのAPI keyを登録します')"
      >
        <template #end>
          <LlmConnectionDialog @saved="reload">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('LLMの接続を追加') }}</DBtn>
            </template>
          </LlmConnectionDialog>
        </template>
      </ListPageTitle>

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.name" :label="$t('名前')" />
        <DSelect v-model="params.kind" :label="$t('種別')" :items="providerKindItems(t)" clearable />
        <DSelect v-model="params.isActive" :label="$t('有効')" :items="activeItems(t)" clearable />
      </TableSearchForm>

      <DDataTable
        v-model:reload-key="reloadKey"
        :columns="columns"
        :fetch="api.manage.llmConnections().list"
        :params="params"
      >
        <template #name-cell="{ row }">
          <NuxtLink :to="`/manage/llm_connections/${row.original.id}`" class="text-primary">
            {{ row.original.name }}
          </NuxtLink>
        </template>
        <template #apiKeyHint-cell="{ row }">
          <span v-if="row.original.hasApiKey" class="font-mono">…{{ row.original.apiKeyHint }}</span>
          <span v-else class="text-muted">{{ $t('なし') }}</span>
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
