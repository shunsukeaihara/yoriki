<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

/**
 * Plugin（基盤管理者。design.md §3.11.1）。行は配備されているコード（本体のPluginと、自社のPlugin）から作られ、
 * ここで変えられるのは有効・停止だけ。停止すると、そのPluginを使う入口・出口・Connectorの接続を停止したのと同じに
 * なる（設定は残り、有効に戻せば元どおり）。保存先のPluginは環境変数で選ぶので、ここでは止めない
 */
definePageMeta({ permission: ManagePagePermissions.PLUGIN })

const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { hasPermission } = usePermission()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = (): PluginListParams => ({ search: undefined, builtin: undefined, enabled: undefined })
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<PluginInfo>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'extensions', header: t('種類') },
  { accessorKey: 'version', header: t('版') },
  { accessorKey: 'usable', header: t('状態') },
  { id: 'failures', header: t('直近7日の失敗') },
  { id: 'actions', header: '' },
]

const typeLabels = computed<Record<string, string>>(() => ({
  trigger: t('入口'),
  notification: t('出口'),
  storage: t('保存先'),
  connector: t('Connector'),
}))
const isStorage = (plugin: PluginInfo) => plugin.extensions.some((e) => e.type === 'storage')
const failureCount = (plugin: PluginInfo) =>
  plugin.extensions.flatMap((e) => e.failures).reduce((sum, f) => sum + f.unexpected + f.timeouts, 0)
const lastFailure = (plugin: PluginInfo) =>
  plugin.extensions
    .flatMap((e) => e.failures)
    .filter((f) => f.lastAt)
    .sort((a, b) => (b.lastAt ?? '').localeCompare(a.lastAt ?? ''))[0]

const state = (plugin: PluginInfo): { label: string; color: 'success' | 'neutral' | 'error' | 'warning' } => {
  if (!plugin.deployed) return { label: t('配備されていない'), color: 'neutral' }
  if (plugin.problem) return { label: t('登録できない'), color: 'error' }
  if (!plugin.enabled) return { label: t('停止中'), color: 'neutral' }
  if (plugin.deprecated) return { label: t('有効（廃止予定）'), color: 'warning' }
  return { label: t('有効'), color: 'success' }
}

const busy = ref<string | null>(null)
const setEnabled = async (plugin: PluginInfo, enabled: boolean) => {
  if (!enabled) {
    const ok = await confirm({
      title: t('Pluginを停止しますか？'),
      description: t(
        '「{name}」を使う入口・出口・Connectorの接続は、停止したのと同じになります（Webhookを受け付けず、まだ送っていない配信も送りません）。設定は残り、有効に戻せば元どおり使えます',
        { name: plugin.name },
      ),
      confirmLabel: t('停止'),
      color: 'error',
    })
    if (!ok) return
  }
  busy.value = plugin.id
  try {
    await api.manage.plugins(plugin.id).patch({ enabled })
    toast.add({ title: enabled ? t('有効にしました') : t('停止しました'), color: 'success' })
    reload()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('Plugin')"
        :description="
          $t(
            'サーバーに配備した拡張（入口・出口・保存先・Connector）です。本体のものと、自社で足したものが並びます。自社のPluginは、見つかったときは停止の状態なので、確かめてから有効にします',
          )
        "
      />

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.search" :label="$t('名前')" />
        <DSelect
          v-model="params.builtin"
          :label="$t('提供元')"
          :items="[
            { value: true, title: $t('本体') },
            { value: false, title: $t('自社') },
          ]"
          clearable
        />
        <DSelect v-model="params.enabled" :label="$t('有効')" :items="activeItems(t)" clearable />
      </TableSearchForm>

      <DDataTable v-model:reload-key="reloadKey" :columns="columns" :fetch="api.manage.plugins().list" :params="params">
        <template #name-cell="{ row }">
          <div class="grid gap-0.5">
            <span class="font-medium">{{ row.original.name }}</span>
            <span class="font-mono text-xs text-muted">{{ row.original.id }}</span>
            <span v-if="row.original.problem" class="text-xs text-error whitespace-pre-wrap">
              {{ row.original.problem }}
            </span>
            <span v-if="row.original.deprecated" class="text-xs text-warning">
              {{ $t('廃止予定：{reason}', { reason: row.original.deprecated }) }}
              <template v-if="row.original.replacedBy">
                {{ $t('（代わり：{id}）', { id: row.original.replacedBy }) }}
              </template>
            </span>
          </div>
        </template>
        <template #extensions-cell="{ row }">
          <div class="flex flex-wrap gap-1">
            <UBadge v-for="e in row.original.extensions" :key="e.id" color="neutral" variant="subtle" size="sm">
              {{ typeLabels[e.type] ?? e.type }}
            </UBadge>
            <UBadge v-if="!row.original.builtin" color="primary" variant="subtle" size="sm">{{ $t('自社') }}</UBadge>
          </div>
        </template>
        <template #version-cell="{ row }">
          <span class="font-mono text-xs">{{ row.original.version }}</span>
        </template>
        <template #usable-cell="{ row }">
          <UBadge :color="state(row.original).color" variant="subtle">{{ state(row.original).label }}</UBadge>
        </template>
        <template #failures-cell="{ row }">
          <span v-if="failureCount(row.original) === 0" class="text-dimmed">0</span>
          <UTooltip v-else :text="lastFailure(row.original)?.lastError ?? ''">
            <span class="text-error">
              {{ failureCount(row.original) }}
              <span class="text-xs text-muted">
                {{ formatDatetime(lastFailure(row.original)?.lastAt ?? '') }}
              </span>
            </span>
          </UTooltip>
        </template>
        <template #actions-cell="{ row }">
          <template
            v-if="hasPermission(PermissionCode.CHANGE_PLUGIN) && row.original.deployed && !isStorage(row.original)"
          >
            <DBtn
              v-if="row.original.enabled"
              size="sm"
              color="error"
              variant="outline"
              :loading="busy === row.original.id"
              @click="setEnabled(row.original, false)"
            >
              {{ $t('停止') }}
            </DBtn>
            <DBtn
              v-else-if="!row.original.problem && !row.original.deprecated"
              size="sm"
              :loading="busy === row.original.id"
              @click="setEnabled(row.original, true)"
            >
              {{ $t('有効にする') }}
            </DBtn>
          </template>
        </template>
      </DDataTable>
    </UContainer>
  </div>
</template>
