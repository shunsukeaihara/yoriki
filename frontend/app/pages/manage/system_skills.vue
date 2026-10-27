<script setup lang="ts">
/**
 * システムのSkill（authorization.md §5.1）。システムのタスクだけが使い、会話と業務画面の一覧には出さない。
 * 中身の編集・公開は、業務画面と同じSkillの詳細（/skills/[skillId]）で行う
 */
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: ManagePagePermissions.SYSTEM_SKILL })

const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): SkillListParams => ({ scope: SkillScope.SYSTEM, name: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<Skill>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'description', header: t('説明') },
  { accessorKey: 'updated', header: t('更新日時') },
]

const onCreated = (skill: Skill) => navigateTo(`/skills/${skill.id}`)
</script>

<template>
  <UContainer>
    <ListPageTitle
      :title="$t('システムのSkill')"
      :description="
        $t(
          'システムのタスクだけが使う手順です。会話と業務画面の一覧には出しません。中身はSkillの画面で編集・公開します',
        )
      "
    >
      <template #end>
        <SkillCreateDialog :scopes="[SkillScope.SYSTEM]" @created="onCreated">
          <template #trigger="{ on }">
            <DBtn icon="i-lucide-plus" v-on="on">{{ $t('Skillを作る') }}</DBtn>
          </template>
        </SkillCreateDialog>
      </template>
    </ListPageTitle>

    <TableSearchForm :reload="reload" :clear="clearParams">
      <DTextField v-model="params.name" :label="$t('名前')" />
    </TableSearchForm>

    <DDataTable v-model:reload-key="reloadKey" :columns="columns" :fetch="api.skills().list" :params="params">
      <template #name-cell="{ row }">
        <div class="flex flex-wrap items-center gap-1.5">
          <NuxtLink :to="`/skills/${row.original.id}`" class="text-primary">{{ row.original.name }}</NuxtLink>
          <UBadge v-if="row.original.suspended" color="error" variant="subtle" size="sm">{{ $t('停止中') }}</UBadge>
          <UBadge v-if="row.original.draft" color="warning" variant="subtle" size="sm">{{ $t('編集中') }}</UBadge>
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
