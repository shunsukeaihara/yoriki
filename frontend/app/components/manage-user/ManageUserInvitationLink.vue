<script setup lang="ts">
/** 発行した招待・再設定のリンク。この画面でしか分からないので、コピーして本人に渡してもらう */
const props = defineProps<{ link: InvitationLink }>()
const toast = useToast()
const { t } = useI18n()
const { copy } = useClipboard()

const onCopy = async () => {
  await copy(props.link.url)
  toast.add({ title: t('コピーしました'), color: 'success' })
}
</script>

<template>
  <div class="grid min-w-0 gap-2 rounded-md border border-info/30 bg-info/10 p-3 text-sm">
    <div class="flex items-center gap-2 font-medium text-info">
      <UIcon name="i-lucide-link" class="size-4 shrink-0" />
      {{ $t('{purpose}のリンク', { purpose: link.purposeDisplay }) }}
    </div>
    <p class="text-muted">
      {{
        $t('このリンクを本人に渡してください。リンクはこの画面でしか表示できません。有効期限：{date}', {
          date: formatDatetime(link.expiresAt),
        })
      }}
    </p>
    <div class="flex min-w-0 items-center gap-2">
      <code class="min-w-0 flex-1 truncate rounded bg-default px-2 py-1.5 text-xs">{{ link.url }}</code>
      <DBtn size="xs" color="neutral" variant="outline" icon="i-lucide-copy" class="shrink-0" @click="onCopy">
        {{ $t('コピー') }}
      </DBtn>
    </div>
  </div>
</template>
