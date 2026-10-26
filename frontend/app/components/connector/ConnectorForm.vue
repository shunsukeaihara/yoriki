<script setup lang="ts">
/**
 * Connectorの作成と、接続・境界の設定の編集（design.md §4.9）。種別と接続先（ホスト・ポート・DB名）は作成した後は
 * 変えられない。境界（説明、既定のスキーマ、上限、機密）を変えて保存すると新しい版になる。
 * 公開する表は ConnectorTablesForm、利用の許可は ConnectorPermissionsForm で編集する
 */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<Connector | null>()
const emit = defineEmits<{
  created: [value: Connector]
  updated: [value: Connector]
  failed: [error: unknown]
}>()

// 上限の既定値はサーバーの既定と同じ
const emptyForm = () => ({
  id: null as string | null,
  // 編集を始めた時点の版。その間に他の人が版を作っていたら、サーバーが409で断る
  version: 0,
  name: '',
  kind: ConnectorKind.POSTGRESQL as ConnectorKind,
  host: '',
  port: null as number | null,
  database: '',
  username: '',
  // パスワードは書き込み専用。編集では、入力したときだけ送る
  password: '',
  tls: TlsMode.VERIFY as TlsMode,
  connectTimeoutSeconds: 10 as number | null,
  maxConcurrency: 4 as number | null,
  isActive: true,
  description: '',
  defaultSchema: '',
  maxRows: 1000 as number | null,
  maxResultMb: 10 as number | null,
  timeoutSeconds: 30 as number | null,
  isSensitive: false,
  changeReason: '',
})
const toForm = (value: Connector | null | undefined) =>
  value
    ? {
        ...emptyForm(),
        id: value.id,
        version: value.version ?? 0,
        name: value.name,
        kind: value.kind,
        host: value.host,
        port: value.port ?? null,
        database: value.database,
        username: value.username,
        tls: value.tls ?? TlsMode.VERIFY,
        connectTimeoutSeconds: value.connectTimeoutSeconds ?? null,
        maxConcurrency: value.maxConcurrency ?? null,
        isActive: value.isActive ?? true,
        description: value.description,
        defaultSchema: value.defaultSchema ?? '',
        maxRows: value.maxRows,
        maxResultMb: bytesToMb(value.maxResultBytes),
        timeoutSeconds: value.timeoutSeconds,
        isSensitive: value.isSensitive,
      }
    : emptyForm()
const form = ref(emptyForm())
const { reset } = syncConnectorForm(modelValue, form, toForm)

const isEdit = computed(() => Boolean(form.value.id))
const isMySQL = computed(() => form.value.kind === ConnectorKind.MYSQL)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (f) => {
    // 空欄の数値は送らない（作成ではサーバーの既定値、更新では変更なし）
    const common = {
      name: f.name,
      username: f.username,
      tls: f.tls,
      connectTimeoutSeconds: f.connectTimeoutSeconds ?? undefined,
      maxConcurrency: f.maxConcurrency ?? undefined,
      isActive: f.isActive,
      description: f.description,
      // MySQLはDB名がスキーマなので送らない。空欄は種別の既定（public、dbo）
      defaultSchema: isMySQL.value ? undefined : f.defaultSchema.trim(),
      maxRows: f.maxRows ?? undefined,
      maxResultBytes: f.maxResultMb === null ? undefined : mbToBytes(f.maxResultMb, modelValue.value?.maxResultBytes),
      timeoutSeconds: f.timeoutSeconds ?? undefined,
      isSensitive: f.isSensitive,
      changeReason: f.changeReason || undefined,
    }
    if (!f.id) {
      return api.manage.connectors().create({
        ...common,
        kind: f.kind,
        host: f.host.trim(),
        port: f.port ?? undefined,
        database: f.database.trim(),
        password: f.password,
      } as ConnectorWrite)
    }
    return api.manage.connectors(f.id).patch({
      ...common,
      version: f.version,
      ...(f.password ? { password: f.password } : {}),
    })
  },
  onSuccess: (result) => {
    if (form.value.id) emit('updated', result)
    else emit('created', result)
    modelValue.value = result
    reset() // 送ったパスワードと変更理由を残さない
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

// 必須項目が埋まっているかだけを確認する。形式の検証はサーバーに任せる
const canPost = computed(() => {
  const f = form.value
  if (!f.name || !f.username) return false
  if (isEdit.value) return true
  return Boolean(f.host.trim() && f.database.trim() && f.password)
})

const passwordHint = computed(() => {
  const current = modelValue.value
  if (current?.hasPassword)
    return t('登録済み（末尾 {hint}）。差し替えるときだけ入力します', { hint: current.passwordHint })
  return t('SELECT権限だけを持つ専用のアカウントを使います')
})

defineExpose({ onSubmit, loading, canPost, reset })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />

    <DFormSection :title="$t('接続先')">
      <template v-if="!isEdit">
        <DRadioGroup
          v-model="form.kind"
          :label="$t('種別')"
          :items="connectorKindItems(t)"
          orientation="horizontal"
          variant="card"
          required
          :error-messages="errors.kind"
        />
        <div class="grid grid-cols-[1fr_8rem] gap-4">
          <DTextField
            v-model="form.host"
            :label="$t('ホスト')"
            required
            placeholder="db.internal"
            :hint="$t('作成した後は変更できません')"
            :error-messages="errors.host"
          />
          <DNumberField
            v-model="form.port"
            :label="$t('ポート')"
            :min="1"
            :max="65535"
            :format-options="{ useGrouping: false }"
            :placeholder="String(defaultPort(form.kind))"
            :error-messages="errors.port"
          />
        </div>
        <DTextField
          v-model="form.database"
          :label="$t('DB名')"
          required
          :hint="$t('作成した後は変更できません')"
          :error-messages="errors.database"
        />
      </template>
      <template v-else>
        <!-- 種別と接続先は作成した後は変えられない。変えるときは新しいConnectorを作る -->
        <DTextField
          :model-value="`${modelValue?.kindDisplay}・${form.host}:${form.port}/${form.database}`"
          :label="$t('接続先')"
          disabled
          :hint="$t('作成した後は変更できません')"
        />
      </template>
      <div class="grid grid-cols-2 gap-4">
        <DTextField
          v-model="form.username"
          :label="$t('ユーザー名')"
          required
          autocomplete="off"
          :error-messages="errors.username"
        />
        <DTextField
          v-model="form.password"
          type="password"
          autocomplete="new-password"
          :label="$t('パスワード')"
          :required="!isEdit"
          :hint="passwordHint"
          :error-messages="errors.password"
        />
      </div>
      <DSelect
        v-model="form.tls"
        :label="$t('通信の暗号化')"
        :items="tlsItems(t)"
        :clearable="false"
        :error-messages="errors.tls"
      />
      <div class="grid grid-cols-2 gap-4">
        <DNumberField
          v-model="form.connectTimeoutSeconds"
          :label="$t('接続のタイムアウト（秒）')"
          :min="1"
          :max="300"
          :error-messages="errors.connectTimeoutSeconds"
        />
        <DNumberField
          v-model="form.maxConcurrency"
          :label="$t('同時実行数の上限')"
          :min="1"
          :max="1000"
          :hint="$t('すべてのWorkerを通した数')"
          :error-messages="errors.maxConcurrency"
        />
      </div>
    </DFormSection>

    <DFormSection
      :title="$t('エージェントが扱える範囲')"
      :description="
        isEdit
          ? $t('この欄から下を変えて保存すると、新しい版になります。始まっている実行は元の版を使い続けます')
          : $t('公開する表・ビューは、作成した後に選びます')
      "
    >
      <DTextarea
        v-model="form.description"
        :label="$t('説明')"
        :hint="$t('エージェントに見せます。どんなデータがあり、何に使うかを書きます')"
        :error-messages="errors.description"
      />
      <DTextField
        v-if="!isMySQL"
        v-model="form.defaultSchema"
        :label="$t('既定のスキーマ')"
        :placeholder="defaultSchema(form.kind)"
        :hint="
          $t('表の名前にスキーマを書かなかったときのスキーマ。空欄は {schema}', { schema: defaultSchema(form.kind) })
        "
        :error-messages="errors.defaultSchema"
      />
      <div class="grid grid-cols-3 gap-4">
        <DNumberField
          v-model="form.maxRows"
          :label="$t('行数の上限')"
          :min="1"
          :max="1000000"
          :error-messages="errors.maxRows"
        />
        <DNumberField
          v-model="form.maxResultMb"
          :label="$t('結果のサイズの上限（MB）')"
          :min="1"
          :max="1024"
          :error-messages="errors.maxResultBytes"
        />
        <DNumberField
          v-model="form.timeoutSeconds"
          :label="$t('実行時間の上限（秒）')"
          :min="1"
          :max="3600"
          :error-messages="errors.timeoutSeconds"
        />
      </div>
      <DSwitch
        v-model="form.isSensitive"
        :label="$t('機密')"
        :hint="
          $t(
            '個人情報を主な中身とする接続先に付けます。ローカルLLMのモデルを使う会話・タスクだけが使えます。一部の列だけが個人情報なら、その列を除いたビューを公開します',
          )
        "
        :error-messages="errors.isSensitive"
      />
    </DFormSection>

    <DSwitch v-model="form.isActive" :label="$t('有効')" :error-messages="errors.isActive" />
    <DTextarea
      v-model="form.changeReason"
      :label="$t('変更理由')"
      :hint="$t('版の履歴に残ります')"
      :error-messages="errors.changeReason"
    />
  </DFormStack>
</template>
