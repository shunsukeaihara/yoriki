<script setup lang="ts">
/** Skillの名前を変える（公開の権限が要る。サーバーでも確かめる） */
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ skill: Skill }>()
const emit = defineEmits<{ updated: [value: Skill] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const dialog = ref<InstanceType<typeof DDialog> | null>(null)

const form = ref({ name: '' })
const onOpen = (open: boolean) => {
  if (!open) return
  form.value = { name: props.skill.name }
  errors.value = {}
}

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) => api.skills(props.skill.id).patch(data),
  onSuccess: (result) => {
    emit('updated', result)
    dialog.value?.closeDialog()
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' }),
})
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ $t('名前を変更') }}</template>
    <template #content>
      <DFormStack>
        <DFormAlert :errors="errors" />
        <DTextField
          v-model="form.name"
          :label="$t('名前')"
          required
          :hint="$t('会話で / で指定するときの名前も変わります')"
          :error-messages="errors.name"
        />
      </DFormStack>
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn :disabled="!form.name.trim()" :loading="loading" @click="onSubmit">{{ $t('更新') }}</DBtn>
    </template>
  </DDialog>
</template>
