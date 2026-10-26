<script setup lang="ts">
/**
 * Skillを作る。作ると最初のDraftが開き、公開するまで誰も使えない。範囲は後から変えられない（authorization.md §5.1）。
 * Toolや手順は、作った後のDraftで編集する
 */
const props = withDefaults(defineProps<{ scopes?: SkillScopeEnum[] }>(), {
  // 業務画面で作る範囲。システムのSkillは管理画面（/manage/system_skills）で作る
  scopes: () => [SkillScope.PERSONAL, SkillScope.DEPARTMENT, SkillScope.COMPANY],
})
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const emit = defineEmits<{ created: [value: Skill]; failed: [error: unknown] }>()

// 作れる範囲と部門だけを出す（サーバーでも権限を確かめ、なければ403）
const creatable = useSkillCreatableScopes()
const departmentItems = computed(() => creatable.departments.value.map((d) => ({ value: d.id, title: d.name })))
const scopeItems = computed(() =>
  skillScopeItems(t).filter((item) => props.scopes.includes(item.value) && creatable.scopes.value.includes(item.value)),
)

const form = ref({
  scope: (scopeItems.value[0]?.value ?? SkillScope.PERSONAL) as SkillScopeEnum,
  department: null as string | null,
  name: '',
  description: '',
})

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ department, ...data }) =>
    api.skills().create({ ...data, department: data.scope === SkillScope.DEPARTMENT ? department : null }),
  onSuccess: (result) => {
    emit('created', result)
    toast.add({ title: t('Skillを作りました。公開するまで、誰も使えません'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

const canPost = computed(
  () =>
    Boolean(form.value.name.trim()) && (form.value.scope !== SkillScope.DEPARTMENT || Boolean(form.value.department)),
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
      :hint="$t('後から変えられません。広げるときは、その範囲に新しいSkillを作ります')"
      :items="scopeItems"
      variant="card"
      :error-messages="errors.scope"
    />
    <DSelect
      v-if="form.scope === SkillScope.DEPARTMENT"
      v-model="form.department"
      :label="$t('部門')"
      required
      :items="departmentItems"
      :error-messages="errors.department"
    />
    <DTextField
      v-model="form.name"
      :label="$t('名前')"
      required
      :hint="$t('会話の入力欄の / で指定するときに使います')"
      :error-messages="errors.name"
    />
    <DTextarea
      v-model="form.description"
      :label="$t('説明')"
      :hint="$t('何をするSkillか、どんな依頼のときに使うか。エージェントと社員がSkillを選ぶのに使います')"
      :error-messages="errors.description"
    />
  </DFormStack>
</template>
