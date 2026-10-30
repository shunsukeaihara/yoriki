<script setup lang="ts">
import TaskCreateForm from './TaskCreateForm.vue'
import DDialog from '../common/DDialog.vue'

// 作れる範囲（TaskCreateForm の scopes）。システムのタスクは管理画面から作る
defineProps<{ scopes?: SkillScopeEnum[] }>()
const emit = defineEmits<{ created: [value: TaskDetail] }>()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof TaskCreateForm> | null>(null)
// 開くたびにフォームを作り直し、前回の入力やエラーを残さない
const formKey = ref(0)
const onOpen = (open: boolean) => {
  if (open) formKey.value += 1
}

const created = (value: TaskDetail) => {
  emit('created', value)
  dialog.value?.closeDialog()
}
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ $t('タスクを作る') }}</template>
    <template #content>
      <TaskCreateForm :key="formKey" ref="formRef" :scopes="scopes" @created="created" />
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ $t('作る') }}
      </DBtn>
    </template>
  </DDialog>
</template>
