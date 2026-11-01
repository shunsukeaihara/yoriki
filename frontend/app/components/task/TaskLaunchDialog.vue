<script setup lang="ts">
/**
 * チャットからのタスクの起動（authorization.md §5.3・§6）。会話の入力欄の `/` で選んだタスクの入力を受け取り、起動した
 * 人の権限で動かす。タスクの Run は会話の文脈を引き継がない（会話の内容は渡さない）。起動する前に、入力と処理の記録を
 * タスクの管理者が見られることを示す（authorization.md §7.1）
 */
import TaskRunForm from './TaskRunForm.vue'
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ task: LaunchableTask | null; conversationId: string }>()
const open = defineModel<boolean>('open', { default: false })
const emit = defineEmits<{ started: [value: TaskRun]; conflicted: []; unknown: [] }>()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof TaskRunForm> | null>(null)
// 開くたびにフォームを作り直し、前回の入力やエラーを残さない（選んだタスクと版で入力の項目が変わる）
const formKey = ref(0)
// 起動のID。受け付けたかが分からなかった起動は、同じタスクを開き直したときも同じIDで送る（先の起動を受け付けて
// いれば、サーバーがそのRunを返すので二重に起動しない）。起動できた・断られたら次は新しいIDにする
const requestId = ref(crypto.randomUUID())
let unknownTaskId: string | null = null
watch(open, (value) => {
  if (!value) return
  formKey.value += 1
  if (unknownTaskId !== props.task?.id) {
    requestId.value = crypto.randomUUID()
    unknownTaskId = null
  }
})

const started = (value: Run | TaskRun) => {
  unknownTaskId = null
  emit('started', value as TaskRun)
  dialog.value?.closeDialog()
}
// 版が変わった・受け付けたかが分からないときは、閉じて親に読み直させる
const conflicted = () => {
  unknownTaskId = null
  emit('conflicted')
  dialog.value?.closeDialog()
}
const unknown = () => {
  unknownTaskId = props.task?.id ?? null
  emit('unknown')
  dialog.value?.closeDialog()
}
</script>

<template>
  <!-- 送信中は閉じさせない（閉じて開き直すと、フォームが作り直されて、同じ起動をもう一度送れてしまうため） -->
  <DDialog ref="dialog" v-model:open="open" :dismissible="!formRef?.loading" :close="!formRef?.loading">
    <template #header>{{ $t('「{name}」を起動', { name: props.task?.name ?? '' }) }}</template>
    <template #content>
      <template v-if="props.task">
        <p v-if="props.task.description" class="mb-2 text-sm whitespace-pre-wrap">{{ props.task.description }}</p>
        <ul class="mb-4 grid list-disc gap-1 ps-5 text-sm text-muted">
          <li>{{ $t('タスクの今の版（版 {number}）で、あなたの権限で動かします', { number: props.task.version }) }}</li>
          <li>{{ $t('この会話の内容は渡しません。タスクに渡すのは下の入力だけです') }}</li>
          <li>{{ $t('結果はこの会話にカードで出します（タスクに通知先があれば、そこにも送ります）') }}</li>
          <li v-if="props.task.scope !== TaskScope.PERSONAL">
            {{ $t('入力と処理の記録は、タスクを管理する人も見られます') }}
          </li>
        </ul>
        <TaskRunForm
          :key="formKey"
          ref="formRef"
          :task="props.task"
          :conversation-id="props.conversationId"
          :client-request-id="requestId"
          @started="started"
          @conflicted="conflicted"
          @unknown="unknown"
        />
      </template>
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" :disabled="formRef?.loading" @click="closeDialog()">
        {{ $t('キャンセル') }}
      </DBtn>
      <DBtn
        icon="i-lucide-play"
        :disabled="!props.task || !formRef?.canPost"
        :loading="formRef?.loading"
        @click="formRef?.onSubmit()"
      >
        {{ $t('起動') }}
      </DBtn>
    </template>
  </DDialog>
</template>
