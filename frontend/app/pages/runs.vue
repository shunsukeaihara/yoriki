<script setup lang="ts">
/**
 * タスクをまたいだ実行の履歴（design.md §11.9）。本番の実行（入口・手動実行・チャットからの起動）のうち、Runの結果を
 * 見られるもの（実行したときの範囲を管理できる人と、チャットから起動した人。authorization.md §7.1）を並べる。
 * 修正チャットのRunは出さない。失敗や確認待ちの実行を、タスクを開かずに探すためのもの
 */
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): TaskRunHistoryParams => ({ taskName: undefined, status: undefined, launch: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}
const statusItems = computed(() => runStatusItems(t))
const launchItems = computed(() => taskRunLaunchItems(t))
</script>

<template>
  <UDashboardPanel id="runs">
    <template #header>
      <UDashboardNavbar :title="$t('実行履歴')" />
    </template>
    <template #body>
      <UContainer>
        <p class="mb-4 text-sm text-muted">
          {{
            $t(
              'タスクの実行を新しい順に並べます。管理できるタスクの実行と、チャットから起動した実行（結果だけ）が出ます。修正チャットの実行は修正チャットで見ます',
            )
          }}
        </p>
        <TableSearchForm :reload="reload" :clear="clearParams">
          <DTextField v-model="params.taskName" :label="$t('タスクの名前')" />
          <DSelect v-model="params.status" :label="$t('状態')" :items="statusItems" clearable />
          <DSelect v-model="params.launch" :label="$t('起動')" :items="launchItems" clearable />
        </TableSearchForm>
        <TaskRunList v-model:reload-key="reloadKey" :params="params" />
      </UContainer>
    </template>
  </UDashboardPanel>
</template>
