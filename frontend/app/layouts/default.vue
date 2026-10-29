<script setup lang="ts">
// 社員の画面の layout。サイドバーに会話の一覧を出す
const account = useAccountStore()
// 管理画面のメニューに表示できる項目があるときだけ、管理画面への入口を出す
const { links: manageLinks } = useManageMenu()
</script>

<template>
  <UDashboardGroup>
    <UDashboardSidebar collapsible resizable :ui="{ footer: 'border-t border-default' }">
      <template #header="{ collapsed }">
        <NuxtLink to="/conversations" class="font-bold">{{ collapsed ? 'Y' : 'Yoriki' }}</NuxtLink>
      </template>

      <template #default="{ collapsed }">
        <DBtn
          to="/conversations"
          icon="i-lucide-square-pen"
          :label="collapsed ? undefined : $t('新しい会話')"
          variant="soft"
          block
          :class="collapsed ? undefined : 'justify-start'"
        />
        <DBtn
          to="/skills"
          icon="i-lucide-book-open-check"
          :label="collapsed ? undefined : $t('Skill')"
          variant="ghost"
          color="neutral"
          block
          :class="collapsed ? undefined : 'justify-start'"
        />
        <NotificationBell :collapsed="collapsed" />
        <ConversationList :collapsed="collapsed" />
      </template>

      <template #footer="{ collapsed }">
        <div class="flex w-full flex-col gap-1">
          <span v-if="account.user && !collapsed" class="truncate px-2.5 text-sm text-muted">
            {{ account.user.displayName || account.user.email }}
          </span>
          <DBtn
            v-if="manageLinks.length > 0"
            to="/manage"
            variant="ghost"
            color="neutral"
            icon="i-lucide-settings"
            :label="collapsed ? undefined : $t('管理')"
            block
            class="justify-start"
          />
          <DBtn
            variant="ghost"
            color="neutral"
            icon="i-lucide-log-out"
            :label="collapsed ? undefined : $t('ログアウト')"
            block
            class="justify-start"
            @click="account.logout()"
          />
        </div>
      </template>
    </UDashboardSidebar>

    <slot />
  </UDashboardGroup>
</template>
