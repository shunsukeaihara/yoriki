<script setup lang="ts">
/**
 * 基盤の設定（design.md §8）。会話のワークスペースの保存期限を扱う。期限を過ぎた会話のワークスペースは、
 * 1日1回の清掃で消す（履歴・入力・成果物は残る）
 */
definePageMeta({ permission: ManagePagePermissions.PLATFORM_SETTINGS })

const { hasPermission } = usePermission()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const canChange = computed(() => hasPermission(PermissionCode.CHANGE_PLATFORM_SETTINGS))

const { data: settings, error } = await useAsyncData('platform-settings', () => api.manage.platformSettings.get())
if (error.value) throw createError(error.value)

const form = ref({ workspaceRetentionDays: settings.value?.workspaceRetentionDays ?? null })
const saved = ref(JSON.stringify(form.value))
const dirty = computed(() => JSON.stringify(form.value) !== saved.value)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) => api.manage.platformSettings.patch(data),
  onSuccess: (result) => {
    settings.value = result
    form.value = { workspaceRetentionDays: result.workspaceRetentionDays }
    saved.value = JSON.stringify(form.value)
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' }),
})
</script>

<template>
  <div>
    <UContainer>
      <ListPageTitle :title="$t('基盤の設定')" :description="$t('基盤の全体に効く設定です')" />

      <UCard class="max-w-2xl">
        <template #header>
          <span class="font-semibold">{{ $t('ワークスペースの保存期限') }}</span>
        </template>
        <DFormStack>
          <DFormAlert :errors="errors" />
          <DNumberField
            v-model="form.workspaceRetentionDays"
            :label="$t('会話のワークスペースの保存期限（日）')"
            :hint="
              $t(
                '会話の最後の実行が終わってからこの日数を過ぎると、ワークスペース（作業中のファイル）を1日1回の清掃で消します。会話の履歴・添付・成果物は残り、続きの依頼は空のワークスペースで始まります。空なら自動では消しません',
              )
            "
            :min="1"
            :disabled="!canChange"
            :error-messages="errors.workspaceRetentionDays"
          />
          <div v-if="canChange">
            <DBtn :disabled="!dirty" :loading="loading" @click="onSubmit">{{ $t('保存') }}</DBtn>
          </div>
        </DFormStack>
      </UCard>
    </UContainer>
  </div>
</template>
