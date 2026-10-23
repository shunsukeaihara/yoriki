<script setup lang="ts">
/**
 * 選んだ過去の版と同じ中身・資料の版を新しく作り、公開中の版にする（公開の権限が要る）。履歴は消さない（design.md §3.7）
 */
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ skill: Skill; number: number }>()
const emit = defineEmits<{ rolledBack: [value: SkillVersion]; failed: [error: unknown] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const dialog = ref<InstanceType<typeof DDialog> | null>(null)

const form = ref({ changeReason: '', operationKey: '', expectedVersion: 0 })
const onOpen = (open: boolean) => {
  if (!open) return
  // 開いた時点の公開中の版。その間に他の人が公開していたら、サーバーが409で断る
  form.value = { changeReason: '', operationKey: newOperationKey(), expectedVersion: props.skill.version }
  errors.value = {}
}

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) => api.skills(props.skill.id).rollback({ number: props.number, ...data }),
  onSuccess: (result) => {
    emit('rolledBack', result)
    dialog.value?.closeDialog()
    toast.add({
      title: t('版 {from} と同じ中身で、版 {number} を公開しました', { from: props.number, number: result.number }),
      color: 'success',
    })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ $t('版 {number} に戻す', { number }) }}</template>
    <template #content>
      <DFormStack>
        <DFormAlert :errors="errors" />
        <p class="text-sm text-muted">
          {{
            $t(
              '版 {number} と同じ中身・資料で新しい版を作り、公開中の版にします。始まっている実行は元の版を使い続けます',
              {
                number,
              },
            )
          }}
        </p>
        <UAlert
          v-if="skill.draft"
          color="warning"
          variant="subtle"
          icon="i-lucide-triangle-alert"
          :title="$t('編集中のDraftは、公開中の版が変わるので公開できなくなります（破棄して開き直します）')"
        />
        <DTextarea
          v-model="form.changeReason"
          :label="$t('変更理由')"
          :hint="$t('版の履歴に残ります')"
          :error-messages="errors.changeReason"
        />
      </DFormStack>
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn icon="i-lucide-undo-2" :loading="loading" @click="onSubmit">{{ $t('戻す') }}</DBtn>
    </template>
  </DDialog>
</template>
