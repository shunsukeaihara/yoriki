<script setup lang="ts">
/**
 * Connectorを使える役割（Group）・部門と、使える操作（design.md §4.9.1）。部門は、所属していれば部門の中の役割に
 * よらず使える。基盤管理者はすべてのConnectorを使える。版には入れず、保存するとすぐに（実行中の実行にも次の操作から）効く
 */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<Connector | null>()
const emit = defineEmits<{
  updated: [value: Connector]
  failed: [error: unknown]
}>()

type Grantee = 'group' | 'department'
type Row = {
  grantee: Grantee
  group: string | null
  department: string | null
  departmentInfo: MinimumDepartment | null
  operations: string[]
}

const allOperations = () => Object.values(ConnectorOperation) as string[]
const toRows = (permissions: ConnectorPermission[] | undefined): Row[] =>
  (permissions ?? []).map((p) => ({
    grantee: p.group ? 'group' : 'department',
    group: p.group ?? null,
    department: p.department ?? null,
    departmentInfo: p.departmentInfo,
    operations: [...(p.operations ?? [])],
  }))

const form = ref({ version: 0, permissions: [] as Row[] })
watch(
  modelValue,
  (value) => {
    form.value = { version: value?.version ?? 0, permissions: toRows(value?.permissions) }
  },
  { immediate: true },
)

// 役割（Group）は少ないので、全件を選択肢にする（defineExpose より前で await しないよう、待たずに読み込む）
const { data: groups } = useAsyncData('manage-groups', () => api.manage.groups.list())
const groupItems = computed(() => (groups.value ?? []).map((g) => ({ value: g.name, title: g.name })))
const granteeItems = computed(() => [
  { value: 'group', title: t('役割') },
  { value: 'department', title: t('部門') },
])

const addRow = () =>
  form.value.permissions.push({
    grantee: 'group',
    group: null,
    department: null,
    departmentInfo: null,
    operations: allOperations(),
  })
const removeRow = (index: number) => form.value.permissions.splice(index, 1)

const toWrite = (rows: Row[]): ConnectorPermissionWrite[] =>
  rows.map((row) => ({
    group: row.grantee === 'group' ? row.group : null,
    department: row.grantee === 'department' ? row.department : null,
    operations: row.operations as ConnectorOperationEnum[],
  }))
const dirty = computed(
  () =>
    JSON.stringify(toWrite(form.value.permissions)) !== JSON.stringify(toWrite(toRows(modelValue.value?.permissions))),
)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ version, permissions }) =>
    api.manage.connectors(modelValue.value!.id).patch({ version, permissions: toWrite(permissions) }),
  onSuccess: (result) => {
    emit('updated', result)
    modelValue.value = result
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})
const rowErrors = computed(() =>
  flattenRowErrors(errors.value.permissions, (i, message) => t('{n}行目：{message}', { n: i + 1, message })),
)

// 役割・部門を選んでいない行があれば送らない
const canPost = computed(
  () =>
    Boolean(modelValue.value) &&
    dirty.value &&
    form.value.permissions.every((row) => (row.grantee === 'group' ? row.group : row.department)),
)

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="{ nonFieldErrors: [...(errors.nonFieldErrors ?? []), ...rowErrors] }" />
    <p class="text-sm text-muted">
      {{
        $t(
          '使える役割・部門を指定します。部門は、所属していれば部門の中の役割によらず使えます。基盤管理者はすべてのConnectorを使えます。保存するとすぐに効きます',
        )
      }}
    </p>

    <p v-if="!form.permissions.length" class="text-sm text-muted">
      {{ $t('まだ誰も使えません（基盤管理者を除く）') }}
    </p>
    <UCard v-for="(row, index) in form.permissions" :key="index" :ui="{ body: 'p-3 sm:p-3' }">
      <div class="grid gap-3">
        <div class="flex items-end gap-2">
          <DSelectButton v-model="row.grantee" :label="$t('対象')" :items="granteeItems" />
          <DSelect
            v-if="row.grantee === 'group'"
            v-model="row.group"
            :label="$t('役割')"
            :items="groupItems"
            searchable
            class="flex-1"
          />
          <DAutocomplete
            v-else
            v-model="row.department"
            :label="$t('部門')"
            :search-fn="api.manage.departments().list"
            :initial-selected="row.departmentInfo"
            class="flex-1"
          />
          <DBtn
            color="neutral"
            variant="ghost"
            icon="i-lucide-trash-2"
            :aria-label="$t('削除')"
            @click="removeRow(index)"
          />
        </div>
        <DCheckboxGroup
          v-model="row.operations"
          :label="$t('使える操作')"
          :items="connectorOperationItems(t)"
          orientation="horizontal"
        />
      </div>
    </UCard>
    <div>
      <DBtn color="neutral" variant="outline" icon="i-lucide-plus" @click="addRow">{{ $t('役割・部門を追加') }}</DBtn>
    </div>
  </DFormStack>
</template>
