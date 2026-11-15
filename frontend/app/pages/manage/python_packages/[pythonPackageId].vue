<script setup lang="ts">
import PythonPackageForm from '~/components/python-package/PythonPackageForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const pythonPackageId = route.params.pythonPackageId as string

const { data: pythonPackage, error } = await useAsyncData(`python-package-${pythonPackageId}`, () =>
  api.manage.pythonPackages(pythonPackageId).get(),
)
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof PythonPackageForm> | null>(null)
const close = () => router.push('/manage/python_packages')

const onDelete = async () => {
  const ok = await confirm({
    title: t('Pythonのpackageを削除しますか？'),
    description: t(
      '新しく追加できなくなり、追加済みの会話でも次に作業環境を作るときに入れません。一時的に止めるだけなら「有効」を外してください',
    ),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.manage.pythonPackages(pythonPackageId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    emit('changed')
    close()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <DDrawer :open="true" :title="$t('Pythonのpackage')" @update:open="(open) => !open && close()">
    <template #content>
      <PythonPackageForm ref="formRef" v-model="pythonPackage" @updated="emit('changed')" />
    </template>
    <template #footer>
      <!-- 削除はサーバー側でも権限を確認し、権限がなければ403になる -->
      <DBtn v-if="hasPermission(PermissionCode.DELETE_PYTHON_PACKAGE)" color="error" variant="ghost" @click="onDelete">
        {{ $t('削除') }}
      </DBtn>
      <div class="flex-1" />
      <DBtn
        v-if="hasPermission(PermissionCode.CHANGE_PYTHON_PACKAGE)"
        :disabled="!formRef?.canPost"
        :loading="formRef?.loading"
        @click="formRef?.onSubmit()"
      >
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
