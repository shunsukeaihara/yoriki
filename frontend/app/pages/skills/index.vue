<script setup lang="ts">
/** 使える（見える）Skillの一覧。個人は本人、部門は所属する人、全社は全社員。未公開のものは編集できる人にだけ出る */
import type { TableColumn } from '@nuxt/ui'

const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): SkillListParams => ({ name: undefined, scope: undefined, published: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}
const publishedItems = computed(() => [
  { value: true, title: t('公開済み') },
  { value: false, title: t('未公開') },
])

const columns: TableColumn<Skill>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'scopeDisplay', header: t('公開範囲') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'description', header: t('説明') },
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
        <TableSearchForm :reload="reload" :clear="clearParams">
          <DTextField v-model="params.name" :label="$t('名前')" />
          <DSelect v-model="params.scope" :label="$t('公開範囲')" :items="skillScopeItems(t)" clearable />
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
          <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
        </DDataTable>
      </UContainer>
    </template>
  </UDashboardPanel>
</template>
