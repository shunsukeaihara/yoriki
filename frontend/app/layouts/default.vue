<script setup lang="ts">
const account = useAccountStore()
// 管理画面のメニューに表示できる項目があるときだけ、管理画面への入口を出す
const { items: manageItems } = useManageMenu()
</script>

<template>
  <div class="min-h-svh bg-default">
    <header class="border-b border-default">
      <UContainer class="flex h-14 items-center justify-between gap-4">
        <NuxtLink to="/" class="font-bold">Yoriki</NuxtLink>
        <div v-if="account.user" class="flex items-center gap-3">
          <DBtn v-if="manageItems.length > 0" to="/manage" variant="ghost" color="neutral" icon="i-lucide-settings">
            {{ $t('管理') }}
          </DBtn>
          <span class="text-sm text-muted">{{ account.user.displayName || account.user.email }}</span>
          <DBtn variant="ghost" color="neutral" icon="i-lucide-log-out" @click="account.logout()">
            {{ $t('ログアウト') }}
          </DBtn>
        </div>
      </UContainer>
    </header>
    <slot />
  </div>
</template>
