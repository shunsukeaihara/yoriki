<script setup lang="ts">
/**
 * 出口の作成と編集（連携の管理者。authorization.md §5.5、design.md §4.4）。種類・範囲・部門は作成した後は変えられない。
 * 送り先のURLと「機密の結果を送ってよい」を変えて保存すると新しい版になる。URLは書くだけで、サーバーはホストと
 * 末尾の4文字しか返さないので、編集では空欄なら変えない
 */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<Destination | null>()
const emit = defineEmits<{
  created: [value: DestinationSecret]
  updated: [value: Destination]
  failed: [error: unknown]
}>()

const emptyForm = () => ({
  id: null as string | null,
  // 編集を始めた時点の版。その間に他の人が版を作っていたら、サーバーが409で断る
  version: 0,
  name: '',
  // 作成のときだけ送る
  kind: DestinationKind.WEBHOOK as DestinationKindEnum,
  scope: DestinationScope.DEPARTMENT as ConnectorScopeEnum,
  department: null as string | null,
  url: '',
  allowsSensitiveResults: false,
  changeReason: '',
})
const toForm = (value: Destination | null | undefined) =>
  value
    ? {
        ...emptyForm(),
        id: value.id,
        version: value.version ?? 0,
        name: value.name,
        kind: value.kind,
        allowsSensitiveResults: value.allowsSensitiveResults ?? false,
      }
    : emptyForm()
const form = ref(emptyForm())
const reset = () => {
  form.value = toForm(modelValue.value)
}
// 停止・再開・鍵の作り直しで出口が変わっても、未保存の入力は残す（版も編集を始めた時点のまま。その間に別の人が
// 版を作っていれば、保存がサーバーの409で止まる）
watch(
  modelValue,
  (value, old) => {
    const dirty = Boolean(old) && JSON.stringify(form.value) !== JSON.stringify(toForm(old))
    if (!(value && old && value.id === old.id && dirty)) form.value = toForm(value)
  },
  { immediate: true },
)

const isEdit = computed(() => Boolean(form.value.id))
const hasUrl = computed(() => isUrlDestinationKind(form.value.kind))

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (f) => {
    const common = {
      name: f.name,
      allowsSensitiveResults: f.allowsSensitiveResults,
      changeReason: f.changeReason || undefined,
      // 編集で空欄のURLは送らない（今の送り先のまま）。画面の通知は送り先を持たない
      url: hasUrl.value && f.url.trim() ? f.url.trim() : undefined,
    }
    if (!f.id) {
      const department = f.scope === DestinationScope.DEPARTMENT ? f.department : null
      return api.manage.destinations().create({ ...common, kind: f.kind, scope: f.scope, department })
    }
    // 名前は版の外なので、変えたときだけ送る（古い画面からの保存で、別の人が変えた名前を戻さない）
    const name = f.name !== modelValue.value?.name ? f.name : undefined
    return api.manage.destinations(f.id).patch({ ...common, name, version: f.version })
  },
  onSuccess: (result) => {
    if (form.value.id) emit('updated', result)
    else emit('created', result as DestinationSecret)
    modelValue.value = result
    reset() // 送ったURL・変更理由を残さない
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

// 必須項目が埋まっているかだけを確認する。URLの形と送ってよい宛先かはサーバーが確かめる
const canPost = computed(
  () =>
    Boolean(form.value.name.trim()) &&
    (isEdit.value ||
      ((!hasUrl.value || Boolean(form.value.url.trim())) &&
        (form.value.scope !== DestinationScope.DEPARTMENT || Boolean(form.value.department)))),
)

defineExpose({ onSubmit, loading, canPost, reset })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />

    <template v-if="!isEdit">
      <DRadioGroup
        v-model="form.kind"
        :label="$t('種類')"
        :hint="$t('作成した後は変更できません')"
        :items="destinationKindItems(t)"
        variant="card"
        :error-messages="errors.kind"
      />
      <DRadioGroup
        v-model="form.scope"
        :label="$t('使ってよい範囲')"
        :hint="$t('作成した後は変更できません。複数の範囲で使う送り先は、範囲ごとに作ります')"
        :items="destinationScopeItems(t)"
        variant="card"
        :error-messages="errors.scope"
      />
      <DAutocomplete
        v-if="form.scope === DestinationScope.DEPARTMENT"
        v-model="form.department"
        :label="$t('部門')"
        required
        :search-fn="api.manage.destinations().departments"
        :hint="$t('この部門のタスクだけが使えます')"
        :error-messages="errors.department"
      />
    </template>
    <div v-else class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      <span class="text-muted">{{ $t('種類') }}</span>
      <span>{{ modelValue?.kindDisplay }}</span>
      <span class="text-muted">{{ $t('使ってよい範囲') }}</span>
      <span>
        {{ modelValue?.scopeDisplay }}
        <template v-if="modelValue?.departmentInfo">・{{ modelValue.departmentInfo.name }}</template>
      </span>
    </div>

    <DFormSection
      :title="$t('送り方')"
      :description="
        isEdit
          ? $t('この欄から下を変えて保存すると、新しい版になります。受け付けたRunは、受け付けたときの版で送ります')
          : undefined
      "
    >
      <DTextField
        v-if="hasUrl"
        v-model="form.url"
        :label="$t('送り先のURL')"
        :required="!isEdit"
        type="url"
        :placeholder="isEdit ? modelValue?.urlDisplay : 'https://'"
        :hint="
          isEdit
            ? $t('今の送り先：{url}。変えるときだけ入力します（今のURLは表示しません）', {
                url: modelValue?.urlDisplay || '-',
              })
            : $t('httpsだけ。社内のアドレスへは送れません（基盤管理者が許したネットワークを除く）')
        "
        :error-messages="errors.url"
      />
      <DSwitch
        v-model="form.allowsSensitiveResults"
        :label="$t('機密の結果を送ってよい')"
        :hint="
          hasUrl
            ? $t('個人情報・機密情報を扱うタスクの結果を、この送り先へ送ってよいときだけ付けます')
            : $t('画面の通知は認証の要る画面への参照だけなので、この指定によらず機密のタスクにも使えます')
        "
        :error-messages="errors.allowsSensitiveResults"
      />
    </DFormSection>

    <DTextarea
      v-model="form.changeReason"
      :label="$t('変更理由')"
      :hint="$t('版の履歴に残ります')"
      :error-messages="errors.changeReason"
    />
  </DFormStack>
</template>
