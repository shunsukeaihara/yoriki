<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: ManagePagePermissions.DEPARTMENT })

const { hasPermission } = usePermission()
const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): ListParams => ({ search: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<Department>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'memberCount', header: t('所属する人数') },
  { accessorKey: 'updated', header: t('更新日時') },
]
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('部門')"
        :description="
          $t(
            '部門のSkillを誰が使い、誰が編集・公開できるかを、所属と部門の中の役割で決めます。役割（権限の組）とは別です',
          )
        "
      >
        <template #end>
          <DepartmentDialog v-if="hasPermission(PermissionCode.ADD_DEPARTMENT)" @saved="reload">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('部門を追加') }}</DBtn>
            </template>
          </DepartmentDialog>
        </template>
      </ListPageTitle>

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.search" :label="$t('名前')" />
      </TableSearchForm>

      <DDataTable
        v-model:reload-key="reloadKey"
        :columns="columns"
        :fetch="api.manage.departments().list"
        :params="params"
      >
        <template #name-cell="{ row }">
          <NuxtLink :to="`/manage/departments/${row.original.id}`" class="text-primary">
            {{ row.original.name }}
          </NuxtLink>
        </template>
        <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
      </DDataTable>
    </UContainer>
    <NuxtPage @changed="reload" />
  </div>
</template>
