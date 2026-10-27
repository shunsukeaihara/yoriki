<script setup lang="ts">
import DepartmentMemberForm from './DepartmentMemberForm.vue'
import DDialog from '../common/DDialog.vue'

defineProps<{ departmentId: string }>()
const emit = defineEmits<{ saved: [value: DepartmentMembership] }>()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof DepartmentMemberForm> | null>(null)
// 開くたびにフォームを作り直し、前回の入力やエラーを残さない
const formKey = ref(0)
const onOpen = (open: boolean) => {
  if (open) formKey.value += 1
}

const saved = (value: DepartmentMembership) => {
  emit('saved', value)
  dialog.value?.closeDialog()
}
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ $t('所属を追加') }}</template>
    <template #content>
      <DepartmentMemberForm :key="formKey" ref="formRef" :department-id="departmentId" @created="saved" />
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ $t('保存') }}
      </DBtn>
    </template>
  </DDialog>
</template>
