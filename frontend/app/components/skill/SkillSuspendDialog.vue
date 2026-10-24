<script setup lang="ts">
/**
 * 緊急停止と再開。停止すると、新しい実行でも、進行中の実行の次の呼出しでも使わせない（design.md §11.7）。
 * 公開の権限が要る（サーバーでも確かめる）
 */
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ skill: Skill }>()
const emit = defineEmits<{ updated: [value: Skill] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const dialog = ref<InstanceType<typeof DDialog> | null>(null)
// 開いたときの状態で、停止か再開かを決める（送った後に表示が切り替わらないように）
const resuming = ref(false)

const form = ref({ reason: '' })
const onOpen = (open: boolean) => {
  if (!open) return
  resuming.value = props.skill.suspended
  form.value = { reason: '' }
  errors.value = {}
}

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) => (resuming.value ? api.skills(props.skill.id).resume(data) : api.skills(props.skill.id).suspend(data)),
  onSuccess: (result) => {
    emit('updated', result)
    dialog.value?.closeDialog()
    toast.add({ title: result.suspended ? t('停止しました') : t('再開しました'), color: 'success' })
  },
  onError: (err) => toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' }),
})
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ resuming ? $t('Skillを再開') : $t('Skillを停止') }}</template>
    <template #content>
      <DFormStack>
        <DFormAlert :errors="errors" />
        <p class="text-sm text-muted">
          {{
            resuming
              ? $t('再開すると、公開中の版をまた使えるようになります')
              : $t('停止すると、これから始まる実行でも、進行中の実行の次の呼出しでも、このSkillを使えなくなります')
          }}
        </p>
        <DTextarea
          v-model="form.reason"
          :label="$t('理由')"
          :hint="$t('変更の記録に残ります')"
          :error-messages="errors.reason"
        />
      </DFormStack>
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn :color="resuming ? 'primary' : 'error'" :loading="loading" @click="onSubmit">
        {{ resuming ? $t('再開する') : $t('停止する') }}
      </DBtn>
    </template>
  </DDialog>
</template>
