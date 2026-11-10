<script setup lang="ts">
/**
 * タスクの修正チャット（design.md §11.11）でタスクを動かすダイアログ。タスクの画面からは新しい修正チャットを始め、
 * 修正チャットの「もう一度動かす」からは同じ会話で動かし直す（`conversationId`。前の入力を初期値にする）。
 * 実行記録の画面からは、その実行の入力で新しい修正チャットを始める（`fromRunId`）。元の実行（もう一度動かすときは前の
 * 実行）のファイルと入口の本文は、同じものを渡す。タスクの範囲で動かし、出口へは送らない
 */
import TaskRunForm from './TaskRunForm.vue'
import DDialog from '../common/DDialog.vue'

const props = defineProps<{
  task: TaskDetail | null
  /** 動かし直す修正チャット */
  conversationId?: string
  /** 入力の初期値（前の実行の入力） */
  initialValues?: TaskRunInputs
  /** 元の実行（ファイルと入口の本文を引き継ぐ） */
  fromRunId?: string
}>()
const open = defineModel<boolean>('open', { default: false })
const emit = defineEmits<{ started: [value: Run]; conflicted: []; unknown: [] }>()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof TaskRunForm> | null>(null)
// 開くたびにフォームを作り直し、前回の入力やエラーを残さない（タスクの版が変われば入力の項目も変わる）
const formKey = ref(0)
// 要求のID。受け付けたかが分からなかった要求は、開き直したときも同じIDと入力で送る（先の要求を受け付けていれば、
// サーバーがそのRunを返すので二重に動かさない）。動かせた・断られたら次は新しいIDにする
const requestId = ref(crypto.randomUUID())
const unknownValues = ref<TaskRunInputs | undefined>()
watch(open, (value) => {
  if (!value) return
  formKey.value += 1
  if (unknownValues.value) return
  requestId.value = crypto.randomUUID()
})
const initialValues = computed(() => unknownValues.value ?? props.initialValues)
// 元の実行の入力のうち、タスクの今の版にない項目（値は渡さない）
const droppedInputs = computed(() => {
  const names = new Set((props.task?.inputs ?? []).map((i) => i.name))
  return Object.keys(props.initialValues ?? {}).filter((name) => !names.has(name))
})

const started = (value: Run | TaskRun) => {
  unknownValues.value = undefined
  emit('started', value as Run)
  dialog.value?.closeDialog()
}
// 版が変わった・受け付けたかが分からないときは、閉じて親に読み直させる
const conflicted = () => {
  unknownValues.value = undefined
  emit('conflicted')
  dialog.value?.closeDialog()
}
const unknown = (values: TaskRunInputs) => {
  unknownValues.value = values
  emit('unknown')
  dialog.value?.closeDialog()
}
</script>

<template>
  <!-- 送信中は閉じさせない（閉じて開き直すと、フォームが作り直されて、同じ実行をもう一度送れてしまうため） -->
  <DDialog ref="dialog" v-model:open="open" :dismissible="!formRef?.loading" :close="!formRef?.loading">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>
      {{
        props.conversationId
          ? $t('「{name}」をもう一度動かす', { name: props.task?.name ?? '' })
          : $t('「{name}」の修正チャット', { name: props.task?.name ?? '' })
      }}
    </template>
    <template #content>
      <template v-if="props.task">
        <ul class="mb-4 grid list-disc gap-1 ps-5 text-sm text-muted">
          <li>
            {{
              $t('タスクの今の版（版 {number}）の依頼文と指示で、タスクの範囲で動かします', {
                number: props.task.version,
              })
            }}
          </li>
          <li>{{ $t('作業の記録が会話に出ます。作業中に追加の指示を出したり、質問に答えたりできます') }}</li>
          <li>{{ $t('結果は通知先に送りません。送るはずだった通知を会話に出します') }}</li>
          <li v-if="props.conversationId">{{ $t('前の実行の作業ファイルは引き継がず、新しく始めます') }}</li>
          <li v-if="props.fromRunId">
            {{ $t('前の実行の入力のファイルと入口の本文は、同じものを渡します。外したファイルは付け直してください') }}
          </li>
          <li v-if="droppedInputs.length">
            {{ $t('今の版にない項目（{names}）の値は渡しません', { names: droppedInputs.join('、') }) }}
          </li>
          <li v-if="props.task.suspended">{{ $t('タスクは停止中ですが、修正チャットでは動かせます') }}</li>
        </ul>
        <p v-if="unknownValues" class="mb-2 text-sm text-warning">
          {{
            $t('前の要求を受け付けたかを確かめられませんでした。同じ入力で送ると、受け付けていれば二重には動かしません')
          }}
        </p>
        <TaskRunForm
          :key="formKey"
          ref="formRef"
          :task="props.task"
          fix
          :fix-conversation-id="props.conversationId"
          :from-run-id="props.fromRunId"
          :client-request-id="requestId"
          :initial-values="initialValues"
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
        {{ $t('動かす') }}
      </DBtn>
    </template>
  </DDialog>
</template>
