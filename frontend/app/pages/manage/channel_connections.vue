<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

// 連携の接続（Slackのアプリ等）を全件扱う（範囲を持たない。使ってよい範囲は、接続の上に登録する対話の出口が持つ。
// authorization.md §5.5）
definePageMeta({ permission: ManagePagePermissions.CHANNEL_CONNECTION })

const api = useAPI()
const { hasPermission } = usePermission()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())
// 種類の絞り込みの選択肢（使えるPluginのもの）
const { extensions } = useSelectableExtensions('channel')

const emptyParams = (): ChannelConnectionListParams => ({
  search: undefined,
  extension: undefined,
  suspended: undefined,
})
const params = ref<ChannelConnectionListParams>(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<ChannelConnection>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'extensionName', header: t('種類') },
  { accessorKey: 'workspaceName', header: t('Workspace・サーバー') },
  { accessorKey: 'suspended', header: t('状態') },
  { accessorKey: 'updated', header: t('更新日時') },
]
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle
        :title="$t('連携の接続')"
        :description="
          $t(
            'SlackのアプリのWorkspace、DiscordのBotのサーバーごとの資格情報です。エージェントには見せません。この接続の上に、質問と報告を投稿するチャネルを「出口」として登録します',
          )
        "
      >
        <template #end>
          <!-- 作成はサーバー側でも権限を確認し、なければ403になる -->
          <ChannelConnectionDialog v-if="hasPermission(PermissionCode.ADD_CHANNEL_CONNECTION)" @saved="reload">
            <template #trigger="{ on }">
              <DBtn icon="i-lucide-plus" v-on="on">{{ $t('連携の接続を追加') }}</DBtn>
            </template>
          </ChannelConnectionDialog>
        </template>
      </ListPageTitle>

      <TableSearchForm :reload="reload" :clear="clearParams">
        <DTextField v-model="params.search" :label="$t('名前')" />
        <DSelect v-model="params.extension" :label="$t('種類')" :items="extensionItems(extensions, t)" clearable />
        <DSelect v-model="params.suspended" :label="$t('状態')" :items="suspendedItems(t)" clearable />
      </TableSearchForm>

      <DDataTable
        v-model:reload-key="reloadKey"
        :columns="columns"
        :fetch="api.manage.channelConnections().list"
        :params="params"
      >
        <template #name-cell="{ row }">
          <NuxtLink :to="`/manage/channel_connections/${row.original.id}`" class="text-primary">
            {{ row.original.name }}
          </NuxtLink>
        </template>
        <template #workspaceName-cell="{ row }">
          <div>{{ row.original.workspaceName || '-' }}</div>
          <div class="font-mono text-xs text-muted">{{ row.original.workspaceId }}</div>
        </template>
        <template #suspended-cell="{ row }">
          <UBadge :color="row.original.suspended ? 'error' : 'success'" variant="subtle">
            {{ row.original.suspended ? $t('停止中') : $t('動いている') }}
          </UBadge>
        </template>
        <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
      </DDataTable>
    </UContainer>
    <NuxtPage @changed="reload" />
  </div>
</template>
