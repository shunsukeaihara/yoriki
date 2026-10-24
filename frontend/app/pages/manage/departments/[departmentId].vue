<script setup lang="ts">
import DepartmentForm from '~/components/department/DepartmentForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const departmentId = route.params.departmentId as string

const { data: department, error } = await useAsyncData(`department-${departmentId}`, () =>
  api.manage.departments(departmentId).get(),
)
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof DepartmentForm> | null>(null)
const close = () => router.push('/manage/departments')

const onDelete = async () => {
  const ok = await confirm({
    title: t('部門を削除しますか？'),
    description: t('この部門のSkillは、誰も使えなくなります'),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.manage.departments(departmentId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    emit('changed')
    close()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <DDrawer :open="true" :title="$t('部門')" :ui="{ content: 'sm:max-w-xl' }" @update:open="(open) => !open && close()">
    <template #content>
      <div class="grid gap-6">
        <DepartmentForm ref="formRef" v-model="department" @updated="emit('changed')" />
        <USeparator />
        <DepartmentMemberList :department-id="departmentId" @changed="emit('changed')" />
      </div>
    </template>
    <template #footer>
      <!-- 削除はサーバー側でも権限を確認し、権限がなければ403になる -->
      <DBtn v-if="hasPermission(PermissionCode.CHANGE_DEPARTMENT)" color="error" variant="ghost" @click="onDelete">
        {{ $t('削除') }}
      </DBtn>
      <div class="flex-1" />
      <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
