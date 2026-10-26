<script setup lang="ts">
/**
 * Connectorの作成と、境界の設定の編集（design.md §4.9.1）。使うDB接続は作成した後は変えられない。
 * 境界（説明、既定のスキーマ、上限、機密）を変えて保存すると新しい版になる。
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
  connection: null as string | null,
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
        connection: value.connection,
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
// 選んだDB接続の種別（MySQLは既定のスキーマを持たない）
const selectedConnection = ref<MinimumDatabaseConnection | null>(null)
const kind = computed<ConnectorKind>(
  () => modelValue.value?.kind ?? selectedConnection.value?.kind ?? ConnectorKind.POSTGRESQL,
)
const isMySQL = computed(() => kind.value === ConnectorKind.MYSQL)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (f) => {
    // 空欄の数値は送らない（作成ではサーバーの既定値、更新では変更なし）
    const common = {
      name: f.name,
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
    if (!f.id) return api.manage.connectors().create({ ...common, connection: f.connection! } as ConnectorWrite)
    return api.manage.connectors(f.id).patch({ ...common, version: f.version })
  },
  onSuccess: (result) => {
    if (form.value.id) emit('updated', result)
    else emit('created', result)
    modelValue.value = result
    reset() // 送った変更理由を残さない
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

// 必須項目が埋まっているかだけを確認する。形式の検証はサーバーに任せる
const canPost = computed(() => Boolean(form.value.name && form.value.connection))

defineExpose({ onSubmit, loading, canPost, reset })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />

    <DAutocomplete
      v-if="!isEdit"
      v-model="form.connection"
      :label="$t('DB接続')"
      required
      :search-fn="api.manage.databaseConnections().list"
      :query-params="{ isActive: true }"
      item-description="kindDisplay"
      :clearable="false"
      :hint="$t('作成した後は変更できません。接続先と資格情報は、DB接続の画面で変えます')"
      :error-messages="errors.connection"
      @selected="(item) => (selectedConnection = item as MinimumDatabaseConnection | null)"
    />
    <!-- 使うDB接続は作成した後は変えられない（公開した表は、そのDBのものなので） -->
    <div v-else class="grid gap-1 text-sm">
      <span class="text-muted">{{ $t('DB接続') }}</span>
      <NuxtLink :to="`/manage/database_connections/${modelValue?.connection}`" class="text-primary">
        {{ modelValue?.connectionInfo.name }}
      </NuxtLink>
      <span class="font-mono text-xs text-muted">
        {{ modelValue?.kindDisplay }}・{{ modelValue?.connectionInfo.host }}:{{ modelValue?.connectionInfo.port }}/{{
          modelValue?.connectionInfo.database
        }}
      </span>
    </div>

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
        :placeholder="defaultSchema(kind)"
        :hint="$t('表の名前にスキーマを書かなかったときのスキーマ。空欄は {schema}', { schema: defaultSchema(kind) })"
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
