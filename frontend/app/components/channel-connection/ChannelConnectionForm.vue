<script setup lang="ts">
/**
 * 連携の接続（Slackのアプリ等）の作成と編集（連携の管理者。design.md §4.6、authorization.md §5.5）。種類は作成した後は
 * 変えられない。Bot tokenとSigning secretは書くだけで、サーバーは返さないので、編集では入力したものだけを送る。
 * 作るときと資格情報を変えるときに、サーバーがSlackで接続を確かめ、Workspaceとbotを記録する
 */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<ChannelConnection | null>()
const emit = defineEmits<{
  created: [value: ChannelConnection]
  updated: [value: ChannelConnection]
  failed: [error: unknown]
}>()

const emptyForm = () => ({
  id: null as string | null,
  name: '',
  // 種類（PluginのAdapterのID）。作成のときだけ送る
  extension: BuiltinExtension.SLACK as string,
  // 資格情報は書き込み専用。編集では、入力したときだけ送る
  botToken: '',
  signingSecret: '',
})
const toForm = (value: ChannelConnection | null | undefined) =>
  value ? { ...emptyForm(), id: value.id, name: value.name, extension: value.extension } : emptyForm()
const form = ref(emptyForm())
watch(modelValue, (value) => (form.value = toForm(value)), { immediate: true })

const isEdit = computed(() => Boolean(form.value.id))
// 選べる種類（停止中・廃止予定のPluginのものは出ない）
const { extensions } = useSelectableExtensions('channel')

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (f) => {
    const secrets = {
      ...(f.botToken.trim() ? { botToken: f.botToken.trim() } : {}),
      ...(f.signingSecret.trim() ? { signingSecret: f.signingSecret.trim() } : {}),
    }
    if (!f.id) return api.manage.channelConnections().create({ name: f.name, extension: f.extension, ...secrets })
    return api.manage.channelConnections(f.id).patch({ name: f.name, ...secrets })
  },
  onSuccess: (result) => {
    if (form.value.id) emit('updated', result)
    else emit('created', result)
    modelValue.value = result
    form.value = toForm(result) // 送った資格情報を残さない
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

// 必須項目が埋まっているかだけを確認する。資格情報が正しいかはサーバーがSlackで確かめる
const canPost = computed(() => {
  const f = form.value
  if (!f.name.trim()) return false
  return isEdit.value || (Boolean(f.botToken.trim()) && Boolean(f.signingSecret.trim()))
})

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField
      v-model="form.name"
      :label="$t('名前')"
      required
      :hint="$t('Slackのアプリ（Workspace）が分かる名前')"
      :error-messages="errors.name"
    />

    <DRadioGroup
      v-if="!isEdit"
      v-model="form.extension"
      :label="$t('種類')"
      :hint="$t('作成した後は変更できません')"
      :items="extensionItems(extensions, t)"
      variant="card"
      required
      :error-messages="errors.extension"
    />
    <div v-else class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      <span class="text-muted">{{ $t('種類') }}</span>
      <span>{{ modelValue?.extensionName }}</span>
      <span class="text-muted">{{ $t('Workspace') }}</span>
      <span>
        {{ modelValue?.workspaceName || '-' }}
        <span v-if="modelValue?.workspaceId" class="font-mono text-xs text-muted"
          >（{{ modelValue.workspaceId }}）</span
        >
      </span>
      <span class="text-muted">{{ $t('botのユーザーID') }}</span>
      <span class="font-mono text-xs">{{ modelValue?.botUserId || '-' }}</span>
    </div>

    <DFormSection
      :title="$t('資格情報')"
      :description="
        isEdit
          ? $t('変えるときだけ入力します（登録済みの値は表示しません）。同じWorkspaceのものだけに変えられます')
          : $t('保存のときに、Slackへつないで確かめます')
      "
    >
      <DTextField
        v-model="form.botToken"
        type="password"
        autocomplete="off"
        :label="$t('Bot User OAuth Token')"
        :required="!isEdit"
        placeholder="xoxb-"
        :hint="$t('Slackのアプリの「OAuth & Permissions」にある、xoxb- から始まるtoken')"
        :error-messages="errors.botToken"
      />
      <DTextField
        v-model="form.signingSecret"
        type="password"
        autocomplete="off"
        :label="$t('Signing Secret')"
        :required="!isEdit"
        :hint="$t('Slackのアプリの「Basic Information」にある値。届いた要求の署名を確かめるのに使います')"
        :error-messages="errors.signingSecret"
      />
    </DFormSection>
  </DFormStack>
</template>
