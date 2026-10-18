<script setup lang="ts">
// 管理画面（/manage 以下）の layout。サイドメニューは権限のある項目だけを出す（composables/manage.ts）
const account = useAccountStore()
const route = useRoute()
const { items } = useManageMenu()

// 表示中のページに対応するメニュー項目の名前を、上部の見出しにする
const title = computed(() => items.value.find((item) => route.path.startsWith(item.to))?.label ?? '')
</script>

<template>
  <UDashboardGroup>
    <UDashboardSidebar collapsible :ui="{ footer: 'border-t border-default' }">
      <template #header="{ collapsed }">
        <NuxtLink to="/manage" class="flex items-center gap-2 font-bold">
          <span>Yoriki</span>
          <span v-if="!collapsed" class="text-sm font-normal text-muted">{{ $t('管理') }}</span>
        </NuxtLink>
      </template>

      <template #default="{ collapsed }">
        <UNavigationMenu :items="items" :collapsed="collapsed" orientation="vertical" tooltip />
      </template>

      <template #footer="{ collapsed }">
        <div class="flex w-full flex-col gap-1">
          <DBtn
            to="/"
            variant="ghost"
            color="neutral"
            icon="i-lucide-arrow-left"
            :label="collapsed ? undefined : $t('社員の画面へ')"
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

    <UDashboardPanel>
      <template #header>
        <UDashboardNavbar :title="title">
          <template #right>
            <span v-if="account.user" class="text-sm text-muted">
              {{ account.user.displayName || account.user.email }}
            </span>
          </template>
        </UDashboardNavbar>
      </template>
      <template #body>
        <slot />
      </template>
    </UDashboardPanel>
  </UDashboardGroup>
</template>
