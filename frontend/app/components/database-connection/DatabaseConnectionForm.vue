<script setup lang="ts">
/**
 * DB接続（社内DBの接続先と資格情報）の作成と編集（design.md §4.9.1）。種別は作成した後は変えられない。
 * 接続先（ホスト・ポート・DB名）を変えるときはパスワードを入れ直す（登録済みのパスワードを別のサーバーへ送らない）。
 * 変えると、この接続を使うConnectorの次の操作から効く
 */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<DatabaseConnection | null>()
const emit = defineEmits<{
  created: [value: DatabaseConnection]
  updated: [value: DatabaseConnection]
  failed: [error: unknown]
}>()

const emptyForm = () => ({
  id: null as string | null,
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
})
const toForm = (value: DatabaseConnection | null | undefined) =>
  value
    ? {
        ...emptyForm(),
        id: value.id,
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
      }
    : emptyForm()
const form = ref(emptyForm())
watch(modelValue, (value) => (form.value = toForm(value)), { immediate: true })

const isEdit = computed(() => Boolean(form.value.id))
// 編集で接続先を変えたか（変えたらパスワードを入れ直す）
const moved = computed(() => {
  const current = modelValue.value
  if (!current) return false
  const f = form.value
  return f.host.trim() !== current.host || f.port !== (current.port ?? null) || f.database.trim() !== current.database
})

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (f) => {
    // 空欄の数値は送らない（作成ではサーバーの既定値、更新では変更なし）
    const data = {
      name: f.name,
      host: f.host.trim(),
      port: f.port ?? undefined,
      database: f.database.trim(),
      username: f.username,
      tls: f.tls,
      connectTimeoutSeconds: f.connectTimeoutSeconds ?? undefined,
      maxConcurrency: f.maxConcurrency ?? undefined,
      isActive: f.isActive,
    }
    if (!f.id) return api.manage.databaseConnections().create({ ...data, kind: f.kind, password: f.password })
    return api.manage.databaseConnections(f.id).patch({ ...data, ...(f.password ? { password: f.password } : {}) })
  },
  onSuccess: (result) => {
    if (form.value.id) emit('updated', result)
    else emit('created', result)
    modelValue.value = result
    form.value = toForm(result) // 送ったパスワードを残さない
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
  if (!f.name || !f.username || !f.host.trim() || !f.database.trim()) return false
  return isEdit.value && !moved.value ? true : Boolean(f.password)
})

const passwordHint = computed(() => {
  const current = modelValue.value
  if (moved.value) return t('接続先を変えるときは、パスワードを入力し直します')
  if (current?.hasPassword)
    return t('登録済み（末尾 {hint}）。差し替えるときだけ入力します', { hint: current.passwordHint })
  return t('SELECT権限だけを持つ専用のアカウントを使います')
})

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />

    <DRadioGroup
      v-if="!isEdit"
      v-model="form.kind"
      :label="$t('種別')"
      :items="connectorKindItems(t)"
      orientation="horizontal"
      variant="card"
      required
      :error-messages="errors.kind"
    />
    <!-- 種別は作成した後は変えられない（SQLの書き方と既定のスキーマの意味が変わるため） -->
    <DTextField
      v-else
      :model-value="modelValue?.kindDisplay"
      :label="$t('種別')"
      disabled
      :hint="$t('作成した後は変更できません')"
    />

    <div class="grid grid-cols-[1fr_8rem] gap-4">
      <DTextField
        v-model="form.host"
        :label="$t('ホスト')"
        required
        placeholder="db.internal"
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
    <DTextField v-model="form.database" :label="$t('DB名')" required :error-messages="errors.database" />
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
        :required="!isEdit || moved"
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
        :hint="$t('この接続を使うConnectorとすべてのWorkerを合わせた数')"
        :error-messages="errors.maxConcurrency"
      />
    </div>
    <DSwitch
      v-model="form.isActive"
      :label="$t('有効')"
      :hint="$t('無効にすると、この接続を使うConnectorも使えなくなります')"
      :error-messages="errors.isActive"
    />
  </DFormStack>
</template>
