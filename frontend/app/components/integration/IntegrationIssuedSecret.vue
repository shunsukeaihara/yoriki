<script setup lang="ts">
/**
 * 作成・作り直しのときに一度だけ返る鍵（入口の鍵、出口の署名の鍵）。サーバーは鍵を保存しない（入口）か暗号化して
 * 保存し、二度と返さないので、この画面でコピーして送り元・受け手に設定してもらう
 */
const props = defineProps<{ label: string; value: string; description: string }>()
const toast = useToast()
const { t } = useI18n()
const { copy } = useClipboard()

const onCopy = async () => {
  await copy(props.value)
  toast.add({ title: t('コピーしました'), color: 'success' })
}
</script>

<template>
  <div class="grid min-w-0 gap-2 rounded-md border border-warning/30 bg-warning/10 p-3 text-sm">
    <div class="flex items-center gap-2 font-medium text-warning">
      <UIcon name="i-lucide-key-round" class="size-4 shrink-0" />
      {{ label }}
    </div>
    <p class="text-muted">{{ description }}</p>
    <div class="flex min-w-0 items-center gap-2">
      <code class="min-w-0 flex-1 truncate rounded bg-default px-2 py-1.5 text-xs" data-testid="issued-secret">
        {{ value }}
      </code>
      <DBtn size="xs" color="neutral" variant="outline" icon="i-lucide-copy" class="shrink-0" @click="onCopy">
        {{ $t('コピー') }}
      </DBtn>
    </div>
  </div>
</template>
