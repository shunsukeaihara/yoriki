<script setup lang="ts">
/**
 * 使える（見える）Skillの一覧（authorization.md §7.3.1）。個人は本人、部門は使える部門の人、全社は全社員。
 * 未公開のものは編集できる人にだけ出る。システムのSkillは管理画面（/manage/system_skills）で扱う
 */
import type { TableColumn } from '@nuxt/ui'

const api = useAPI()
const { t } = useI18n()
const account = useAccountStore()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): SkillListParams => ({
  name: undefined,
  scope: undefined,
  department: undefined,
  published: undefined,
  editable: undefined,
})
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}
// 範囲のタブ（0はすべて）と、部門の範囲の部門（使える部門から選ぶ）。選んだらすぐに絞り込む
const ALL = 0
const scopeTabs = computed(() => [
  { value: ALL, title: t('すべて') },
  ...skillScopeItems(t).filter((item) => item.value !== SkillScope.SYSTEM),
])
const scopeTab = computed({
  get: () => params.value.scope ?? ALL,
  set: (value: number) => {
    params.value.scope = value === ALL ? undefined : (value as SkillScopeEnum)
    if (params.value.scope !== SkillScope.DEPARTMENT) params.value.department = undefined
    reload()
  },
})
const departmentItems = computed(() =>
  (account.user?.usableDepartments ?? []).map((d) => ({ value: d.id, title: d.name })),
)
const editableOnly = computed({
  get: () => Boolean(params.value.editable),
  set: (value: boolean) => {
    params.value.editable = value || undefined
    reload()
  },
})
// 行ごとの「できること」。サーバーが判定した可否を出す
const abilityLabel = (skill: Skill) => {
  if (skill.canPublish) return t('編集・公開できます')
  if (skill.canEdit) return t('編集できます')
  return t('使えます')
}
const publishedItems = computed(() => [
  { value: true, title: t('公開済み') },
  { value: false, title: t('未公開') },
])

const columns: TableColumn<Skill>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'scopeDisplay', header: t('範囲') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'description', header: t('説明') },
  { id: 'abilities', header: t('できること') },
  { accessorKey: 'updated', header: t('更新日時') },
]

const onCreated = (skill: Skill) => navigateTo(`/skills/${skill.id}`)
</script>

<template>
  <UDashboardPanel id="skills">
    <template #header>
      <UDashboardNavbar :title="$t('Skill')">
        <template #right>
          <SkillCreateDialog @created="onCreated">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('Skillを作る') }}</DBtn>
            </template>
          </SkillCreateDialog>
        </template>
      </UDashboardNavbar>
    </template>
    <template #body>
      <UContainer>
        <p class="mb-4 text-sm text-muted">
          {{
            $t(
              '業務の手順・判断の基準・資料をまとめたものです。会話の入力欄の / で指定するか、エージェントが依頼に合うものを選んで使います',
            )
          }}
        </p>
        <div class="mb-4 flex flex-wrap items-end gap-4">
          <DSelectButton v-model="scopeTab" :label="$t('範囲')" :show-label="false" :items="scopeTabs" />
          <DSelect
            v-if="params.scope === SkillScope.DEPARTMENT"
            v-model="params.department"
            :label="$t('部門')"
            :show-label="false"
            :placeholder="$t('すべての部門')"
            :items="departmentItems"
            clearable
            class="w-56"
            @update:model-value="reload"
          />
          <DCheckbox v-model="editableOnly" :label="$t('編集できるものだけ')" />
        </div>
        <TableSearchForm :reload="reload" :clear="clearParams">
          <DTextField v-model="params.name" :label="$t('名前')" />
          <DSelect v-model="params.published" :label="$t('公開')" :items="publishedItems" clearable />
        </TableSearchForm>

        <DDataTable v-model:reload-key="reloadKey" :columns="columns" :fetch="api.skills().list" :params="params">
          <template #name-cell="{ row }">
            <div class="flex flex-wrap items-center gap-1.5">
              <NuxtLink :to="`/skills/${row.original.id}`" class="text-primary">{{ row.original.name }}</NuxtLink>
              <UBadge v-if="row.original.suspended" color="error" variant="subtle" size="sm">{{ $t('停止中') }}</UBadge>
              <UBadge v-if="row.original.draft" color="warning" variant="subtle" size="sm">{{ $t('編集中') }}</UBadge>
            </div>
          </template>
          <template #scopeDisplay-cell="{ row }">
            <div>{{ row.original.scopeDisplay }}</div>
            <div v-if="row.original.scope !== SkillScope.COMPANY" class="text-xs text-muted">
              {{ skillOwnerLabel(row.original, t) }}
            </div>
          </template>
          <template #version-cell="{ row }">
            <span v-if="row.original.version">{{ $t('版 {number}', { number: row.original.version }) }}</span>
            <UBadge v-else color="neutral" variant="subtle" size="sm">{{ $t('未公開') }}</UBadge>
          </template>
          <template #description-cell="{ row }">
            <p class="line-clamp-2 max-w-md whitespace-normal text-sm text-muted">{{ row.original.description }}</p>
          </template>
          <template #abilities-cell="{ row }">
            <span class="text-sm text-muted">{{ abilityLabel(row.original) }}</span>
          </template>
          <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
        </DDataTable>
      </UContainer>
    </template>
  </UDashboardPanel>
</template>
