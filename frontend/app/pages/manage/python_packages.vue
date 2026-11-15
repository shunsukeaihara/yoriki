<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: ManagePagePermissions.PYTHON_PACKAGE })

const { hasPermission } = usePermission()
const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): PythonPackageListParams => ({ name: undefined, isActive: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<PythonPackage>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'specifier', header: t('版の範囲') },
  { accessorKey: 'note', header: t('メモ') },
  { accessorKey: 'isActive', header: t('有効') },
  { accessorKey: 'updated', header: t('更新日時') },
]
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('Pythonのpackage')"
        :description="
          $t(
            'エージェントが作業環境に追加してよいPythonのpackageです。ここにあるpackageだけを追加でき、依存するpackageは一緒に入ります。イメージに入っているライブラリで足りるものは登録しなくてかまいません',
          )
        "
      >
        <template #end>
          <PythonPackageDialog v-if="hasPermission(PermissionCode.ADD_PYTHON_PACKAGE)" @saved="reload">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('Pythonのpackageを追加') }}</DBtn>
            </template>
          </PythonPackageDialog>
        </template>
      </ListPageTitle>

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.name" :label="$t('名前')" />
        <DSelect v-model="params.isActive" :label="$t('有効')" :items="activeItems(t)" clearable />
      </TableSearchForm>

      <DDataTable
        v-model:reload-key="reloadKey"
        :columns="columns"
        :fetch="api.manage.pythonPackages().list"
        :params="params"
      >
        <template #name-cell="{ row }">
          <NuxtLink :to="`/manage/python_packages/${row.original.id}`" class="text-primary font-mono">
            {{ row.original.name }}
          </NuxtLink>
        </template>
        <template #specifier-cell="{ row }">
          <span v-if="row.original.specifier" class="font-mono text-xs">{{ row.original.specifier }}</span>
          <span v-else class="text-muted">{{ $t('どの版も') }}</span>
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
