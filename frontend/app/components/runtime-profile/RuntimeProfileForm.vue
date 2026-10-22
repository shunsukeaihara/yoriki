<script setup lang="ts">
/**
 * Runtime Profileの作成と編集。イメージは「名前@sha256:digest」で入れ、サーバーがレジストリで契約番号と対応platformを
 * 確かめる（design.md §7.3）。中身を変えて保存すると新しい版になり、始まっている実行は元の版を使い続ける
 */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<RuntimeProfile | null>()
const emit = defineEmits<{
  created: [value: RuntimeProfile]
  updated: [value: RuntimeProfile]
  failed: [error: unknown]
}>()

// 資源の上限の既定値（sandbox/CONTRACT.md §2。サーバーの既定と同じ）
const emptyForm = () => ({
  id: null as string | null,
  // 編集を始めた時点の版。その間に他の人が版を作っていたら、サーバーが409で断る
  version: 0,
  name: '',
  isActive: true,
  image: '',
  memoryMb: 2048 as number | null,
  cpus: '2.00' as string | null,
  pidsLimit: 256 as number | null,
  tmpMb: 256 as number | null,
  workMb: 512 as number | null,
  outputMb: 256 as number | null,
  envMb: 512 as number | null,
  commandTimeoutSeconds: 600 as number | null,
  jobTimeoutSeconds: 1800 as number | null,
  changeReason: '',
})
const form = ref(emptyForm())

watch(
  modelValue,
  (value) => {
    form.value = value
      ? {
          id: value.id,
          version: value.version ?? 0,
          name: value.name,
          isActive: value.isActive ?? true,
          image: value.image,
          memoryMb: value.memoryMb ?? null,
          cpus: value.cpus ?? null,
          pidsLimit: value.pidsLimit ?? null,
          tmpMb: value.tmpMb ?? null,
          workMb: value.workMb ?? null,
          outputMb: value.outputMb ?? null,
          envMb: value.envMb ?? null,
          commandTimeoutSeconds: value.commandTimeoutSeconds ?? null,
          jobTimeoutSeconds: value.jobTimeoutSeconds ?? null,
          changeReason: '',
        }
      : emptyForm()
  },
  { immediate: true },
)

const isEdit = computed(() => Boolean(form.value.id))
const RESOURCES = [
  'memoryMb',
  'cpus',
  'pidsLimit',
  'tmpMb',
  'workMb',
  'outputMb',
  'envMb',
  'commandTimeoutSeconds',
  'jobTimeoutSeconds',
] as const

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ id, version, ...rest }) => {
    // 空欄の上限は送らない（作成ではサーバーの既定値、更新では今の版の値になる）
    const data: RuntimeProfileWrite = { name: rest.name, isActive: rest.isActive, image: rest.image.trim() }
    for (const key of RESOURCES) {
      const value = rest[key]
      if (value !== null && value !== '') Object.assign(data, { [key]: value })
    }
    if (rest.changeReason) data.changeReason = rest.changeReason
    return id ? api.manage.runtimeProfiles(id).patch({ ...data, version }) : api.manage.runtimeProfiles().create(data)
  },
  onSuccess: (result) => {
    if (form.value.id) emit('updated', result)
    else emit('created', result)
    modelValue.value = result
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

// 必須項目が埋まっているかだけを確認する。形式とイメージの確認はサーバーに任せる
const canPost = computed(() => Boolean(form.value.name && form.value.image.trim()))

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />
    <DSwitch v-model="form.isActive" :label="$t('有効')" :error-messages="errors.isActive" />

    <DFormSection
      :title="$t('イメージと資源の上限')"
      :description="
        isEdit
          ? $t('この欄から下を変えて保存すると、新しい版になります。始まっている実行は元の版を使い続けます')
          : undefined
      "
    >
      <!-- vue-i18n では @ が特別な意味を持つので、イメージの例は引数で渡す（frontend-conventions.md §10） -->
      <DTextField
        v-model="form.image"
        :label="$t('イメージ')"
        required
        :hint="
          $t('「{format}」の形で入れます（例：{example}）。タグだけでは登録できません', {
            format: 'name@sha256:digest',
            example: 'yoriki/yoriki-sandbox-docs@sha256:…',
          })
        "
        :error-messages="errors.image"
        class="max-w-none"
      />
      <dl v-if="modelValue" class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt class="text-muted">{{ $t('契約番号') }}</dt>
        <dd>{{ modelValue.contract }}</dd>
        <dt class="text-muted">{{ $t('対応platform') }}</dt>
        <dd class="font-mono">{{ modelValue.platforms.join(', ') }}</dd>
        <dt class="text-muted">{{ $t('Pythonの版') }}</dt>
        <dd>{{ modelValue.pythonVersion || '-' }}</dd>
      </dl>
      <div class="grid grid-cols-3 gap-4">
        <DNumberField
          v-model="form.memoryMb"
          :label="$t('メモリ（MB）')"
          :min="512"
          :error-messages="errors.memoryMb"
        />
        <DDecimalField
          v-model="form.cpus"
          :label="$t('CPU')"
          :decimal-places="2"
          :min="0.25"
          :error-messages="errors.cpus"
        />
        <DNumberField
          v-model="form.pidsLimit"
          :label="$t('プロセス数の上限')"
          :min="64"
          :error-messages="errors.pidsLimit"
        />
      </div>
      <p class="text-xs text-muted">
        {{ $t('次の4つの領域はメモリの上にあり、メモリの上限に含まれます') }}
      </p>
      <div class="grid grid-cols-4 gap-4">
        <DNumberField v-model="form.tmpMb" :label="$t('/tmp（MB）')" :min="16" :error-messages="errors.tmpMb" />
        <DNumberField v-model="form.workMb" :label="$t('work（MB）')" :min="64" :error-messages="errors.workMb" />
        <DNumberField v-model="form.outputMb" :label="$t('output（MB）')" :min="16" :error-messages="errors.outputMb" />
        <DNumberField v-model="form.envMb" :label="$t('env（MB）')" :min="16" :error-messages="errors.envMb" />
      </div>
      <div class="grid grid-cols-2 gap-4">
        <DNumberField
          v-model="form.commandTimeoutSeconds"
          :label="$t('コマンドの待ち時間の上限（秒）')"
          :hint="$t('これを過ぎたコマンドは、止めずにバックグラウンドのジョブにします')"
          :min="10"
          :error-messages="errors.commandTimeoutSeconds"
        />
        <DNumberField
          v-model="form.jobTimeoutSeconds"
          :label="$t('ジョブの実行時間の上限（秒）')"
          :min="60"
          :error-messages="errors.jobTimeoutSeconds"
        />
      </div>
    </DFormSection>

    <DTextarea
      v-model="form.changeReason"
      :label="$t('変更理由')"
      :hint="$t('版の履歴に残ります')"
      :error-messages="errors.changeReason"
    />
  </DFormStack>
</template>
