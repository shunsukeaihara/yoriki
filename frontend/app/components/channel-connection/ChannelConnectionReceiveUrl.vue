<script setup lang="ts">
/** Slackのアプリに設定する受け口のURL（Event Subscriptions と Interactivity の両方に同じURLを設定する） */
const props = defineProps<{ url: string }>()
const toast = useToast()
const { t } = useI18n()
const { copy } = useClipboard()
const absolute = computed(() => hookUrl(props.url))

const onCopy = async () => {
  await copy(absolute.value)
  toast.add({ title: t('コピーしました'), color: 'success' })
}
</script>

<template>
  <div class="grid min-w-0 gap-2 text-sm">
    <span class="text-muted">{{ $t('Slackのアプリに設定するURL') }}</span>
    <div class="flex min-w-0 items-center gap-2">
      <code
        class="min-w-0 flex-1 rounded bg-elevated px-2 py-1.5 text-xs break-all"
        data-testid="channel-receive-url"
        >{{ absolute }}</code
      >
      <DBtn size="xs" color="neutral" variant="outline" icon="i-lucide-copy" class="shrink-0" @click="onCopy">
        {{ $t('コピー') }}
      </DBtn>
    </div>
    <p class="text-xs text-muted">
      {{
        $t(
          'Slackのアプリの「Event Subscriptions」のRequest URL（message.channels のイベントを受け取る）と、「Interactivity & Shortcuts」のRequest URLの両方に、このURLを設定します',
        )
      }}
    </p>
  </div>
</template>
