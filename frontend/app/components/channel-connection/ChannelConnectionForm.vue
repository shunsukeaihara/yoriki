<script setup lang="ts">
/**
 * 連携の接続（Slackのアプリ、DiscordのBotとサーバー等）の作成と編集（連携の管理者。design.md §4.6、
 * authorization.md §5.5）。種類は作成した後は変えられない。資格情報（Slackは Bot token・Signing secret、Discordは
 * Bot token・Public Key・サーバーのID）は書くだけで、サーバーは返さないので、編集では入力したものだけを送る。
 * 作るときと資格情報を変えるときに、サーバーがチャットツールで接続を確かめ、Workspace（サーバー）とbotを記録する
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
  publicKey: '',
  // Discordのサーバー（guild）のID。作成のときだけ入力する（別のサーバーには別の接続を作る）
  guildId: '',
})
const toForm = (value: ChannelConnection | null | undefined) =>
  value ? { ...emptyForm(), id: value.id, name: value.name, extension: value.extension } : emptyForm()
const form = ref(emptyForm())
watch(modelValue, (value) => (form.value = toForm(value)), { immediate: true })

const isEdit = computed(() => Boolean(form.value.id))
const isDiscord = computed(() => form.value.extension === BuiltinExtension.DISCORD)
// 選べる種類（停止中・廃止予定のPluginのものは出ない）
const { extensions } = useSelectableExtensions('channel')

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (f) => {
    const discord = f.extension === BuiltinExtension.DISCORD
    const secrets = {
      ...(f.botToken.trim() ? { botToken: f.botToken.trim() } : {}),
      ...(!discord && f.signingSecret.trim() ? { signingSecret: f.signingSecret.trim() } : {}),
      ...(discord && f.publicKey.trim() ? { publicKey: f.publicKey.trim() } : {}),
      ...(discord && !f.id && f.guildId.trim() ? { guildId: f.guildId.trim() } : {}),
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

// 必須項目が埋まっているかだけを確認する。資格情報が正しいかはサーバーがチャットツールで確かめる
const canPost = computed(() => {
  const f = form.value
  if (!f.name.trim()) return false
  if (isEdit.value) return true
  if (!f.botToken.trim()) return false
  return isDiscord.value ? Boolean(f.publicKey.trim() && f.guildId.trim()) : Boolean(f.signingSecret.trim())
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
      :hint="$t('チャットツールのアプリと、Workspace・サーバーが分かる名前')"
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
      <span class="text-muted">{{ isDiscord ? $t('サーバー') : $t('Workspace') }}</span>
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
          ? $t(
              '変えるときだけ入力します（登録済みの値は表示しません）。同じWorkspace・サーバーのものだけに変えられます',
            )
          : $t('保存のときに、チャットツールへつないで確かめます')
      "
    >
      <template v-if="isDiscord">
        <DTextField
          v-model="form.botToken"
          type="password"
          autocomplete="off"
          :label="$t('Bot token')"
          :required="!isEdit"
          :hint="$t('Discordのアプリの「Bot」で作るtoken（Reset Token）')"
          :error-messages="errors.botToken"
        />
        <DTextField
          v-model="form.publicKey"
          autocomplete="off"
          :label="$t('Public Key')"
          :required="!isEdit"
          :hint="$t('Discordのアプリの「General Information」にある値。届いたボタンの要求の署名を確かめるのに使います')"
          :error-messages="errors.publicKey"
        />
        <DTextField
          v-if="!isEdit"
          v-model="form.guildId"
          autocomplete="off"
          :label="$t('サーバーのID')"
          required
          :hint="
            $t(
              'Discordの開発者モードで、サーバーの右クリックの「IDをコピー」で取れる数字。Botをサーバーに追加してから登録します',
            )
          "
          :error-messages="errors.guildId"
        />
      </template>
      <template v-else>
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
      </template>
    </DFormSection>
  </DFormStack>
</template>
