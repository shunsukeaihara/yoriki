<script setup lang="ts">
import DepartmentForm from './DepartmentForm.vue'
import DDialog from '../common/DDialog.vue'

const emit = defineEmits<{ saved: [value: Department] }>()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof DepartmentForm> | null>(null)
const editing = ref<Department | null>(null)

// 開くたびに空のフォームにし、前回の入力やエラーを残さない
const onOpen = (open: boolean) => {
  if (open) editing.value = null
}

const saved = (value: Department) => {
  emit('saved', value)
  dialog.value?.closeDialog()
}
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ $t('部門を追加') }}</template>
    <template #content>
      <DepartmentForm ref="formRef" v-model="editing" @created="saved" />
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ $t('保存') }}
      </DBtn>
    </template>
  </DDialog>
</template>
