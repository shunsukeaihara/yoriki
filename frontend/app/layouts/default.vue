<script setup lang="ts">
const account = useAccountStore()
</script>

<template>
  <div class="min-h-svh bg-default">
    <header class="border-b border-default">
      <UContainer class="flex h-14 items-center justify-between gap-4">
        <NuxtLink to="/" class="font-bold">Yoriki</NuxtLink>
        <div v-if="account.user" class="flex items-center gap-3">
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
