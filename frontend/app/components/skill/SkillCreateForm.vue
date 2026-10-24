<script setup lang="ts">
/**
 * Skillを作る。作ると最初のDraftが開き、公開するまで誰も使えない。公開範囲は後から変えられない（design.md §3.6）。
 * Toolや手順は、作った後のDraftで編集する
 */
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const account = useAccountStore()
const { hasPermission, hasAny } = usePermission()
const emit = defineEmits<{ created: [value: Skill]; failed: [error: unknown] }>()

// 部門のSkillを作れる部門。基盤管理者はすべての部門（所属しなくても公開の役割として扱う）、それ以外は編集以上の所属
const { items: allDepartments, fetchAll } = useFetchAll(() => api.manage.departments().list())
if (hasPermission(PermissionCode.CHANGE_DEPARTMENT))
  fetchAll().catch((err) => toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' }))
const departmentItems = computed(() =>
  hasPermission(PermissionCode.CHANGE_DEPARTMENT)
    ? allDepartments.value.map((d) => ({ value: d.id, title: d.name }))
    : (account.user?.departments ?? [])
        .filter((d) => d.role >= DepartmentRole.EDITOR)
        .map((d) => ({ value: d.id, title: d.name })),
)
// 作れる範囲だけを出す（サーバーでも権限を確かめ、なければ403）
const scopeItems = computed(() =>
  skillScopeItems(t).filter(
    (item) =>
      item.value === SkillScope.PERSONAL ||
      (item.value === SkillScope.DEPARTMENT && departmentItems.value.length > 0) ||
      (item.value === SkillScope.COMPANY &&
        hasAny([PermissionCode.EDIT_COMPANY_SKILLS, PermissionCode.PUBLISH_COMPANY_SKILLS])),
  ),
)

const form = ref({
  scope: SkillScope.PERSONAL as SkillScopeEnum,
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
      v-model="form.scope"
      :label="$t('公開範囲')"
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
