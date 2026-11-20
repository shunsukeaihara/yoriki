<script setup lang="ts">
/**
 * 常時接続で受ける接続（Discord）の受信の状態（channel-listener。design.md §10、deployment.md §7）。
 * 担当のlistenerがいなければ、スレッドへの返信を受け付けられない（ボタンとWebでの回答はできる）
 */
defineProps<{ listener: ChannelListenerStatus }>()
</script>

<template>
  <div class="grid gap-2 text-sm" data-testid="channel-listener">
    <span class="text-muted">{{ $t('返信の受信（channel-listener）') }}</span>
    <UAlert
      v-if="listener.error"
      color="error"
      variant="subtle"
      icon="i-lucide-plug-zap"
      :title="$t('つなげません')"
      :description="listener.error"
    />
    <UAlert
      v-else-if="!listener.active"
      color="warning"
      variant="subtle"
      icon="i-lucide-circle-pause"
      :title="$t('受信していません')"
      :description="
        $t(
          'channel-listenerが動いていません。スレッドへの返信は受け付けられません（ボタンとWebでの回答はできます）。動き出すと、確認待ちのスレッドの返信を読み直します',
        )
      "
    />
    <div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
      <span class="text-muted">{{ $t('担当') }}</span>
      <span class="font-mono text-xs break-all">{{ listener.active ? listener.owner : '-' }}</span>
      <span class="text-muted">{{ $t('最後につないだ日時') }}</span>
      <span>{{ formatDatetime(listener.connected) || '-' }}</span>
    </div>
  </div>
</template>
