<script setup lang="ts">
import DepartmentMemberForm from './DepartmentMemberForm.vue'
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ departmentId: string; membership?: DepartmentMembership | null }>()
const emit = defineEmits<{ saved: [value: DepartmentMembership] }>()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof DepartmentMemberForm> | null>(null)
const editing = ref<DepartmentMembership | null>(null)
const isEdit = computed(() => Boolean(props.membership))

// 開くたびに対象を入れ直し、前回の入力やエラーを残さない
const onOpen = (open: boolean) => {
  if (open) editing.value = props.membership ?? null
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
    <template #header>{{ isEdit ? $t('役割を変更') : $t('所属を追加') }}</template>
    <template #content>
      <DepartmentMemberForm
        ref="formRef"
        v-model="editing"
        :department-id="departmentId"
        @created="saved"
        @updated="saved"
      />
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ isEdit ? $t('更新') : $t('保存') }}
      </DBtn>
    </template>
  </DDialog>
</template>
