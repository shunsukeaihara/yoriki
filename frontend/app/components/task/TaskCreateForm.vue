<script setup lang="ts">
/**
 * タスクを作る。範囲は後から変えられない（authorization.md §5.3）。作ると最初の版ができ、Skill・Connector・入力の項目・
 * 通知は、作った後のタスクの画面で編集する
 */
const props = withDefaults(defineProps<{ scopes?: SkillScopeEnum[] }>(), {
  // 業務画面で作る範囲。システムのタスクは管理画面（/manage/system_tasks）で作る
  scopes: () => [TaskScope.PERSONAL, TaskScope.DEPARTMENT, TaskScope.COMPANY],
})
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const emit = defineEmits<{ created: [value: TaskDetail]; failed: [error: unknown] }>()

// 作れる範囲と部門だけを出す（サーバーでも権限を確かめ、なければ403）
const creatable = useTaskCreatableScopes()
const departmentItems = computed(() => creatable.departments.value.map((d) => ({ value: d.id, title: d.name })))
const scopeItems = computed(() =>
  taskScopeItems(t).filter((item) => props.scopes.includes(item.value) && creatable.scopes.value.includes(item.value)),
)
const models = ref<AppModelProfile[]>([])
const modelItems = computed(() =>
  models.value.map((m) => ({
    value: m.id,
    title: m.isLocal ? `${m.name}（${t('ローカルLLM')}）` : m.name,
  })),
)

const form = ref({
  scope: (scopeItems.value[0]?.value ?? TaskScope.PERSONAL) as SkillScopeEnum,
  department: null as string | null,
  name: '',
  modelProfile: null as string | null,
  promptTemplate: '',
})
onMounted(async () => {
  try {
    models.value = await api.modelProfiles.list()
    form.value.modelProfile ??= models.value.find((m) => m.isDefault)?.id ?? null
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
})

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ department, modelProfile, ...data }) =>
    api.tasks().create({
      ...data,
      department: data.scope === TaskScope.DEPARTMENT ? department : null,
      modelProfile: modelProfile ?? '',
      description: '',
      effort: '',
      handlesSensitiveData: false,
      notifyContent: NotifyContent.STATUS_AND_RESULT,
    }),
  onSuccess: (result) => {
    emit('created', result)
    toast.add({ title: t('タスクを作りました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

const canPost = computed(
  () =>
    Boolean(form.value.name.trim()) &&
    Boolean(form.value.modelProfile) &&
    Boolean(form.value.promptTemplate.trim()) &&
    (form.value.scope !== TaskScope.DEPARTMENT || Boolean(form.value.department)),
)

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DRadioGroup
      v-if="scopeItems.length > 1"
      v-model="form.scope"
      :label="$t('範囲')"
      :hint="$t('後から変えられません。使えるSkill・Connector・通知先は範囲で決まります')"
      :items="scopeItems"
      variant="card"
      :error-messages="errors.scope"
    />
    <DSelect
      v-if="form.scope === TaskScope.DEPARTMENT"
      v-model="form.department"
      :label="$t('部門')"
      required
      :items="departmentItems"
      :error-messages="errors.department"
    />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />
    <DSelect
      v-model="form.modelProfile"
      :label="$t('モデル')"
      required
      :items="modelItems"
      :error-messages="errors.modelProfile"
    />
    <DTextarea
      v-model="form.promptTemplate"
      :label="$t('依頼文')"
      required
      :rows="4"
      :hint="$t('Runの最初の依頼文です。入力の項目は、作った後に足します')"
      :error-messages="errors.promptTemplate"
      class="max-w-none"
    />
  </DFormStack>
</template>
