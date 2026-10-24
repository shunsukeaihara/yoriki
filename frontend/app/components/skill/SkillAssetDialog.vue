<script setup lang="ts">
import SkillAssetForm from './SkillAssetForm.vue'
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ skillId: string; draftId: string; revision: number; asset?: SkillDraftAsset | null }>()
const emit = defineEmits<{ saved: [value: SkillDraftAsset]; failed: [error: unknown] }>()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof SkillAssetForm> | null>(null)
const editing = ref<SkillDraftAsset | null>(null)
const isEdit = computed(() => Boolean(props.asset))

// 開くたびに対象を入れ直し、前回の入力やエラーを残さない
const onOpen = (open: boolean) => {
  if (open) editing.value = props.asset ?? null
}

const saved = (value: SkillDraftAsset) => {
  emit('saved', value)
  dialog.value?.closeDialog()
}
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ isEdit ? $t('資料を変更') : $t('資料を追加') }}</template>
    <template #content>
      <SkillAssetForm
        ref="formRef"
        v-model="editing"
        :skill-id="skillId"
        :draft-id="draftId"
        :revision="revision"
        @created="saved"
        @updated="saved"
        @failed="(err) => emit('failed', err)"
      />
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ isEdit ? $t('更新') : $t('追加') }}
      </DBtn>
    </template>
  </DDialog>
</template>
