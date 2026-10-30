<script setup lang="ts">
/**
 * タスクの中身の編集（design.md §4.1・§11.9）。保存すると新しい版ができ、次のRunから使う。保存では編集を始めた時点の
 * version を送り、その間に他の人が版を作っていたら409になる。範囲と部門は変えられない。
 *
 * 選べるSkill・Connector・通知先は、タスクの範囲で決まる候補のAPIから読む（authorization.md §5.4・§7.2）。Skillの
 * 宣言したConnector・Toolとの組合せ、機密とモデル、通知の条件は、保存のときにサーバーが確かめて項目ごとに返す
 */
const props = defineProps<{ task: TaskDetail }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const emit = defineEmits<{ updated: [value: TaskDetail]; failed: [error: unknown] }>()

const toForm = (task: TaskDetail) => ({
  name: task.name,
  description: task.description,
  promptTemplate: task.promptTemplate,
  inputs: toInputRows(task.inputs),
  skills: (task.skills ?? []).map((s) => ({ ...s })),
  connectors: [...(task.connectors ?? [])],
  tools: [...(task.tools ?? [])] as string[],
  modelProfile: task.modelProfile,
  effort: task.effort,
  handlesSensitiveData: task.handlesSensitiveData,
  outputSchema: schemaToText(task.outputSchema),
  destination: task.destination ?? null,
  notifyEvents: [...(task.notifyEvents ?? [])] as string[],
  notifyContent: task.notifyContent,
  notificationSkill: task.notificationSkill ? { ...task.notificationSkill } : null,
  changeReason: '',
})
const form = ref(toForm(props.task))
const saved = ref(JSON.stringify(form.value))
const version = ref(props.task.version ?? 0)
// 名前は版の外にあり、名前だけの変更では版が増えないので、変えたときだけ送る（古い画面から、別の人が変えた名前を
// 黙って戻さないように）
const savedName = ref(props.task.name)

/** 中身を入れ直す（保存の後、最新の状態を読み込んだ後） */
const reset = (task: TaskDetail = props.task) => {
  form.value = toForm(task)
  saved.value = JSON.stringify(form.value)
  version.value = task.version ?? 0
  savedName.value = task.name
  errors.value = {}
  loadChoices()
}
const dirty = computed(() => JSON.stringify(form.value) !== saved.value)

// 候補。タスクの範囲で決まり、範囲は変わらないので最初に読む
const choiceParams = taskChoiceParams(props.task)
const { items: skillChoices, fetchAll: fetchSkills } = useFetchAll(() => api.tasks().skillChoices(choiceParams))
const { items: connectorChoices, fetchAll: fetchConnectors } = useFetchAll(() =>
  api.tasks().connectorChoices(choiceParams),
)
const { items: destinationChoices, fetchAll: fetchDestinations } = useFetchAll(() =>
  api.destinations.list(choiceParams),
)
const models = ref<AppModelProfile[]>([])
// 候補は、開いたときと、タスクを読み直したとき（保存・最新の状態の読み込み）に読む（その間に公開された版等を出す）
const loadChoices = async () => {
  try {
    await Promise.all([
      fetchSkills(),
      fetchConnectors(),
      fetchDestinations(),
      api.modelProfiles.list().then((list) => (models.value = list)),
    ])
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
onMounted(loadChoices)

const connectorItems = computed(() => {
  const items = connectorChoices.value.map((c) => ({
    value: c.id,
    title: c.isSensitive ? `${c.name}（${t('機密')}）` : c.name,
    description: c.description,
  }))
  // 候補にない（無効・削除・範囲の外になった）登録済みのものも、外せるように出す
  for (const info of props.task.connectorsInfo) {
    if (!items.some((i) => i.value === info.id) && form.value.connectors.includes(info.id))
      items.push({ value: info.id, title: `${info.name}（${t('このタスクでは使えません')}）`, description: '' })
  }
  return items
})

const model = computed(() => models.value.find((m) => m.id === form.value.modelProfile))
const modelItems = computed(() => {
  const items = models.value.map((m) => ({
    value: m.id,
    title: m.isLocal ? `${m.name}（${t('ローカルLLM')}）` : m.name,
  }))
  const current = props.task.modelProfileInfo
  if (current && !items.some((i) => i.value === current.id)) items.push({ value: current.id, title: current.name })
  return items
})
const effortItems = computed(() => [
  { value: '', title: t('モデルの既定') },
  ...(model.value?.effortValues ?? []).map((value) => ({ value, title: value })),
])
watch(
  () => form.value.modelProfile,
  () => {
    // 新しいモデルが受け付けないeffortは既定に戻す
    if (model.value && form.value.effort && !model.value.effortValues.includes(form.value.effort))
      form.value.effort = ''
  },
)

const destinationItems = computed(() => {
  const items = destinationChoices.value.map((d) => ({
    value: d.id,
    title: d.allowsSensitiveResults ? `${d.name}（${t('機密の結果を送れます')}）` : d.name,
  }))
  const current = props.task.destinationInfo
  if (current && !items.some((i) => i.value === current.id))
    items.push({ value: current.id, title: `${current.name}（${t('このタスクでは使えません')}）` })
  return items
})
watch(
  () => form.value.destination,
  (value) => {
    if (!value) {
      form.value.notifyEvents = []
      form.value.notificationSkill = null
    }
  },
)

// 通知Skillは1つ。Skillの選択欄を使い回すため、並びとして扱う
const notificationSkills = computed({
  get: () => (form.value.notificationSkill ? [form.value.notificationSkill] : []),
  set: (value: TaskSkill[]) => (form.value.notificationSkill = value[0] ?? null),
})
const canUseNotificationSkill = computed(
  () =>
    Boolean(form.value.destination) &&
    form.value.notifyEvents.includes(NotifyEvent.COMPLETED) &&
    form.value.notifyContent !== NotifyContent.STATUS,
)

// JSONとして読めるかだけを入力中に示す。JSON Schemaとして正しいかはサーバーが確かめる
const outputSchema = computed(() => parseSchemaText(form.value.outputSchema, t))

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) =>
    api.tasks(props.task.id).patch({
      version: version.value,
      ...(data.name !== savedName.value ? { name: data.name } : {}),
      description: data.description,
      promptTemplate: data.promptTemplate,
      inputs: fromInputRows(data.inputs),
      skills: data.skills,
      connectors: data.connectors,
      tools: data.tools as ToolName[],
      modelProfile: data.modelProfile,
      effort: data.effort,
      handlesSensitiveData: data.handlesSensitiveData,
      outputSchema: outputSchema.value.value,
      destination: data.destination,
      notifyEvents: data.destination ? (data.notifyEvents as NotifyEventEnum[]) : [],
      notifyContent: data.notifyContent,
      notificationSkill: data.notificationSkill,
      changeReason: data.changeReason,
    }),
  onSuccess: (result) => {
    reset(result)
    emit('updated', result)
    toast.add({
      title:
        result.version === props.task.version
          ? t('保存しました')
          : t('版 {number} を作りました', { number: result.version }),
      color: 'success',
    })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

const canPost = computed(
  () =>
    dirty.value &&
    Boolean(form.value.name.trim()) &&
    Boolean(form.value.promptTemplate.trim()) &&
    !outputSchema.value.error,
)

defineExpose({ onSubmit, loading, canPost, dirty, reset })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />
    <DTextarea
      v-model="form.description"
      :label="$t('説明')"
      :hint="$t('何をするタスクか。管理する人が一覧で見分けるのに使います')"
      :error-messages="errors.description"
      class="max-w-none"
    />

    <DFormSection :title="$t('依頼')">
      <DTextarea
        v-model="form.promptTemplate"
        :label="$t('依頼文')"
        required
        :rows="6"
        :hint="
          $t('Runの最初の依頼文です。{example} と書くと、入力の値を文字として埋め込みます', {
            example: '{{ inputs.名前 }}',
          })
        "
        :error-messages="errors.promptTemplate"
        class="max-w-none font-mono"
      />
    </DFormSection>

    <TaskInputsField v-model="form.inputs" :errors="errors.inputs" />

    <DFormSection
      :title="$t('使うもの')"
      :description="$t('エージェントは、ここで選んだSkill・Connector・Toolだけを使い、ほかのものを探して使いません')"
    >
      <TaskSkillsField
        v-model="form.skills"
        :label="$t('Skill')"
        :hint="$t('版を固定します。Skillが新しい版を公開しても、タスクを保存し直すまで変わりません')"
        :choices="skillChoices"
        :info="task.skillsInfo"
        :exclude="form.notificationSkill ? [form.notificationSkill.skill] : []"
        :error-messages="flattenRowErrors(errors.skills)"
      />
      <DSelect
        v-model="form.connectors"
        :label="$t('Connector')"
        :hint="$t('Skillが使うConnectorは、ここにも選びます')"
        :items="connectorItems"
        multiple
        clearable
        searchable
        :error-messages="errors.connectors"
      />
      <DCheckboxGroup
        v-model="form.tools"
        :label="$t('使ってよいTool')"
        :hint="$t('登録したSkillの「使ってよいTool」にもあるものだけを使います')"
        :items="skillToolItems(t)"
        orientation="horizontal"
        :error-messages="errors.tools"
      />
    </DFormSection>

    <DFormSection :title="$t('モデル')">
      <DSelect
        v-model="form.modelProfile"
        :label="$t('モデル')"
        required
        :items="modelItems"
        :error-messages="errors.modelProfile"
      />
      <DSelect
        v-if="model?.effortValues.length"
        v-model="form.effort"
        :label="$t('effort')"
        :items="effortItems"
        :error-messages="errors.effort"
      />
      <DCheckbox
        v-model="form.handlesSensitiveData"
        :label="$t('個人情報・機密情報を扱う')"
        :hint="
          $t(
            '機密のSkill・Connectorを使えるようになり、ローカルLLMのモデルだけを使います。結果は、機密の結果を送ってよい通知先にだけ送ります',
          )
        "
        :error-messages="errors.handlesSensitiveData"
      />
      <DTextarea
        v-model="form.outputSchema"
        :label="$t('業務結果のSchema')"
        :hint="
          $t(
            'JSON Schemaで書きます。空ならSkillのものを使います。ファイルの項目（format: file）は、通知先へ渡す成果物を指します',
          )
        "
        :rows="4"
        :error-messages="outputSchema.error ? [outputSchema.error] : errors.outputSchema"
        class="max-w-none font-mono"
      />
    </DFormSection>

    <DFormSection
      :title="$t('結果の通知')"
      :description="
        $t('通知先は連携の管理者が登録したものから選びます。Webhookの入口があるタスクは、失敗の通知が必要です')
      "
    >
      <DSelect
        v-model="form.destination"
        :label="$t('通知先')"
        :items="destinationItems"
        clearable
        :placeholder="$t('通知しない')"
        :error-messages="errors.destination"
      />
      <template v-if="form.destination">
        <DCheckboxGroup
          v-model="form.notifyEvents"
          :label="$t('通知するとき')"
          :items="notifyEventItems(t)"
          orientation="horizontal"
          :error-messages="errors.notifyEvents"
        />
        <DRadioGroup
          v-model="form.notifyContent"
          :label="$t('通知の内容')"
          :items="notifyContentItems(t)"
          :error-messages="errors.notifyContent"
        />
        <TaskSkillsField
          v-if="canUseNotificationSkill || form.notificationSkill"
          v-model="notificationSkills"
          :label="$t('通知Skill')"
          :hint="
            $t(
              '完了の通知の文章と、渡す成果物を決めるSkillです（任意）。業務のRunの最後に使います。なければ決まった形で送ります',
            )
          "
          :choices="skillChoices"
          :info="task.skillsInfo"
          :exclude="form.skills.map((s) => s.skill)"
          :max="1"
          :error-messages="flattenRowErrors(errors.notificationSkill)"
        />
      </template>
    </DFormSection>

    <DTextField
      v-model="form.changeReason"
      :label="$t('変更の理由')"
      :hint="$t('版の履歴に残します（任意）')"
      :error-messages="errors.changeReason"
    />
  </DFormStack>
</template>
