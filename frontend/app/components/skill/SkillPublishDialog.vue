<script setup lang="ts">
/**
 * Draftを公開し、新しい版を公開中の版にする（公開の権限が要る）。Draftは閉じる。
 * operation key は開くたびに作り、同じ公開の再送で版を重ねて作らない（design.md §3.7）
 */
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ skill: Skill; draft: SkillDraft }>()
const emit = defineEmits<{ published: [value: SkillVersion]; failed: [error: unknown] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const dialog = ref<InstanceType<typeof DDialog> | null>(null)

const form = ref({ changeReason: '', operationKey: '' })
const onOpen = (open: boolean) => {
  if (!open) return
  form.value = { changeReason: '', operationKey: newOperationKey() }
  errors.value = {}
}

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) => api.skills(props.skill.id).publish({ draft: props.draft.id, revision: props.draft.revision, ...data }),
  onSuccess: (result) => {
    emit('published', result)
    dialog.value?.closeDialog()
    toast.add({
      title: t('版 {number} を公開しました。次の実行から使います', { number: result.number }),
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
    <template #header>{{ $t('公開') }}</template>
    <template #content>
      <DFormStack>
        <DFormAlert :errors="errors" />
        <p class="text-sm text-muted">
          {{
            $t(
              'Draftの中身と資料から版 {number} を作り、{scope}の公開中の版にします。始まっている実行は元の版を使い続けます',
              { number: skill.version + 1, scope: skill.scopeDisplay },
            )
          }}
        </p>
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
      <DBtn icon="i-lucide-upload" :loading="loading" @click="onSubmit">{{ $t('公開する') }}</DBtn>
    </template>
  </DDialog>
</template>
