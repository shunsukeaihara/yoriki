<script setup lang="ts">
import TaskRunForm from './TaskRunForm.vue'
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ task: TaskDetail }>()
const emit = defineEmits<{ started: [value: Run]; conflicted: []; unknown: [] }>()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof TaskRunForm> | null>(null)
// 開くたびにフォームを作り直し、前回の入力やエラーを残さない（タスクの版が変われば入力の項目も変わる）
const formKey = ref(0)
const onOpen = (open: boolean) => {
  if (open) formKey.value += 1
}

const started = (value: Run) => {
  emit('started', value)
  dialog.value?.closeDialog()
}
// 版が変わった・受け付けたかが分からないときは、閉じて親に読み直させる
const conflicted = () => {
  emit('conflicted')
  dialog.value?.closeDialog()
}
const unknown = () => {
  emit('unknown')
  dialog.value?.closeDialog()
}
</script>

<template>
  <!-- 送信中は閉じさせない（閉じて開き直すと、フォームが作り直されて、同じ実行をもう一度送れてしまうため） -->
  <DDialog ref="dialog" :dismissible="!formRef?.loading" :close="!formRef?.loading" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ $t('「{name}」を実行', { name: props.task.name }) }}</template>
    <template #content>
      <p class="mb-4 text-sm text-muted">
        {{
          props.task.scope === TaskScope.PERSONAL
            ? $t('タスクの今の版（版 {number}）で、自分の権限で実行します', { number: props.task.version })
            : $t('タスクの今の版（版 {number}）で、タスクの範囲の権限で実行します。結果は通知先にも送ります', {
                number: props.task.version,
              })
        }}
      </p>
      <TaskRunForm
        :key="formKey"
        ref="formRef"
        :task="props.task"
        @started="started"
        @conflicted="conflicted"
        @unknown="unknown"
      />
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" :disabled="formRef?.loading" @click="closeDialog()">
        {{ $t('キャンセル') }}
      </DBtn>
      <DBtn icon="i-lucide-play" :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ $t('実行') }}
      </DBtn>
    </template>
  </DDialog>
</template>
