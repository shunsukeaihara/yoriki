<script setup lang="ts">
/** 部門の所属の一覧。追加・役割の変更・外す */
import type { TableColumn } from '@nuxt/ui'

const props = defineProps<{ departmentId: string }>()
const emit = defineEmits<{ changed: [] }>()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const params = ref<DepartmentMembershipListParams>({})

const columns: TableColumn<DepartmentMembership>[] = [
  { accessorKey: 'userInfo', header: t('社員') },
  { accessorKey: 'roleDisplay', header: t('部門の中の役割') },
  { id: 'actions', header: '' },
]

const onSaved = () => {
  reload()
  emit('changed')
}

const onRemove = async (membership: DepartmentMembership) => {
  const ok = await confirm({
    title: t('{name}を部門から外しますか？', { name: membership.userInfo.displayName || membership.userInfo.email }),
    description: t('この部門のSkillを使えなくなります'),
    confirmLabel: t('外す'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.manage.departments(props.departmentId).members(membership.id).delete()
    toast.add({ title: t('外しました'), color: 'success' })
    onSaved()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <div class="grid gap-3">
    <div class="flex items-center justify-between">
      <h3 class="font-semibold">{{ $t('所属') }}</h3>
      <DepartmentMemberDialog :department-id="departmentId" @saved="onSaved">
        <template #trigger="{ on }">
          <DBtn icon="i-lucide-plus" variant="outline" v-on="on">{{ $t('所属を追加') }}</DBtn>
        </template>
      </DepartmentMemberDialog>
    </div>
    <DDataTable
      v-model:reload-key="reloadKey"
      :columns="columns"
      :fetch="api.manage.departments(departmentId).members().list"
      :params="params"
    >
      <template #userInfo-cell="{ row }">
        <div>{{ row.original.userInfo.displayName || row.original.userInfo.email }}</div>
        <div v-if="row.original.userInfo.displayName" class="text-xs text-muted">{{ row.original.userInfo.email }}</div>
      </template>
      <template #actions-cell="{ row }">
        <div class="flex justify-end gap-1">
          <DepartmentMemberDialog :department-id="departmentId" :membership="row.original" @saved="onSaved">
            <template #trigger="{ on }">
              <DBtn
                size="sm"
                color="neutral"
                variant="ghost"
                icon="i-lucide-pencil"
                :aria-label="$t('役割を変更')"
                v-on="on"
              />
            </template>
          </DepartmentMemberDialog>
          <DBtn
            size="sm"
            color="error"
            variant="ghost"
            icon="i-lucide-user-minus"
            :aria-label="$t('外す')"
            @click="onRemove(row.original)"
          />
        </div>
      </template>
    </DDataTable>
  </div>
</template>
