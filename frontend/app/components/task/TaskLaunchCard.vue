<script setup lang="ts">
/**
 * 会話に出す、チャットから起動したタスクの Run のカード（authorization.md §7.1）。起動した人が見られる結果（状態と
 * 最後の応答）を出し、業務結果と成果物は Run の画面で見る。この内容は会話のモデルへは渡らない
 */
const props = defineProps<{ run: LaunchedTaskRun }>()

const failed = computed(() => props.run.status === RunStatus.FAILED)
const runPath = computed(() => `/tasks/${props.run.task}/runs/${props.run.id}`)
</script>

<template>
  <div class="grid gap-2 rounded-md border border-default p-3">
    <div class="flex flex-wrap items-center gap-2 text-sm">
      <UIcon name="i-lucide-workflow" class="size-4 shrink-0 text-muted" />
      <span class="min-w-0 font-medium break-words">{{ $t('タスク「{name}」', { name: run.taskName }) }}</span>
      <RunStatusBadge :run="run" />
      <span
        v-if="!failed && run.endReasonDisplay && run.endReasonDisplay !== run.statusDisplay"
        class="text-xs text-muted"
        >{{ run.endReasonDisplay }}</span
      >
    </div>
    <!-- 失敗の理由の本文はログなので、起動した人には終わり方だけを出す（authorization.md §7.1） -->
    <UAlert
      v-if="failed"
      color="error"
      variant="subtle"
      icon="i-lucide-circle-x"
      :title="run.endReasonDisplay || run.statusDisplay"
    />
    <MarkdownContent v-if="run.response" :text="run.response" />
    <div>
      <DBtn :to="runPath" size="sm" variant="outline" color="neutral" trailing-icon="i-lucide-arrow-right">
        {{ $t('結果を開く') }}
      </DBtn>
    </div>
  </div>
</template>
