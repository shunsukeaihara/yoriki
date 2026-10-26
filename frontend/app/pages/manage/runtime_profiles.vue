<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: ManagePagePermissions.RUNTIME_PROFILE })

const { hasPermission } = usePermission()
const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): RuntimeProfileListParams => ({ name: undefined, isActive: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<RuntimeProfile>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'image', header: t('イメージ') },
  { accessorKey: 'memoryMb', header: t('資源') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'isActive', header: t('有効') },
  { accessorKey: 'updated', header: t('更新日時') },
]

// 表では repository と digest の先頭だけを出す
const shortImage = (image: string) => {
  const [repository, digest = ''] = image.split('@')
  return `${repository}@${digest.slice(0, 19)}…`
}
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('Runtime Profile')"
        :description="
          $t(
            'コマンドを動かすSandboxのイメージと資源の上限です。既定のものを、コマンドを使う実行が使います。中身を変えて保存すると新しい版になります',
          )
        "
      >
        <template #end>
          <RuntimeProfileDialog v-if="hasPermission(PermissionCode.ADD_RUNTIME_PROFILE)" @saved="reload">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('Runtime Profileを追加') }}</DBtn>
            </template>
          </RuntimeProfileDialog>
        </template>
      </ListPageTitle>

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.name" :label="$t('名前')" />
        <DSelect v-model="params.isActive" :label="$t('有効')" :items="activeItems(t)" clearable />
      </TableSearchForm>

      <DDataTable
        v-model:reload-key="reloadKey"
        :columns="columns"
        :fetch="api.manage.runtimeProfiles().list"
        :params="params"
      >
        <template #name-cell="{ row }">
          <NuxtLink :to="`/manage/runtime_profiles/${row.original.id}`" class="text-primary">
            {{ row.original.name }}
          </NuxtLink>
          <UBadge v-if="row.original.isDefault" color="primary" variant="subtle" size="sm" class="ml-2">
            {{ $t('既定') }}
          </UBadge>
        </template>
        <template #image-cell="{ row }">
          <span class="font-mono text-xs">{{ shortImage(row.original.image) }}</span>
        </template>
        <template #memoryMb-cell="{ row }">
          {{ $t('メモリ {memory}MB・CPU {cpus}', { memory: row.original.memoryMb, cpus: row.original.cpus }) }}
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
