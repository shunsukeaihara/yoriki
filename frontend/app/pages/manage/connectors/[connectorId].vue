<script setup lang="ts">
import { AxiosError } from 'axios'
import ConnectorForm from '~/components/connector/ConnectorForm.vue'
import ConnectorDepartmentsForm from '~/components/connector/ConnectorDepartmentsForm.vue'
import ConnectorTablesForm from '~/components/connector/ConnectorTablesForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const connectorId = route.params.connectorId as string

const {
  data: connector,
  error,
  refresh,
} = await useAsyncData(`connector-${connectorId}`, () => api.manage.connectors(connectorId).get())
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof ConnectorForm> | null>(null)
const tablesRef = ref<InstanceType<typeof ConnectorTablesForm> | null>(null)
const departmentsRef = ref<InstanceType<typeof ConnectorDepartmentsForm> | null>(null)
const tab = ref('settings')
const versionsReloadKey = ref(0)
// 編集の間に他の人が版を作っていた（409）。最新の状態を読み込んでから編集し直す
const conflicted = ref(false)

// 編集はサーバー側でも権限と所属を確認し、足りなければ403・404になる。権限がなければ保存のボタンを出さない
const canChange = computed(() => hasPermission(PermissionCode.CHANGE_CONNECTOR))

// 公開する表のタブを初めて開いたときに、接続先の表・ビューの一覧を読む（編集する人だけ）
watch(tab, (value) => {
  if (value === 'tables' && canChange.value) tablesRef.value?.loadOnce()
})

// 保存の対象はタブごとのフォーム。試し実行と版の履歴のタブでは保存しない
const activeForm = computed(() =>
  canChange.value
    ? ({ settings: formRef.value, tables: tablesRef.value, departments: departmentsRef.value }[tab.value] ?? null)
    : null,
)

const close = () => router.push('/manage/connectors')

const onUpdated = () => {
  conflicted.value = false
  versionsReloadKey.value = Date.now()
  emit('changed')
}
const onFailed = (err: unknown) => {
  conflicted.value = err instanceof AxiosError && err.response?.status === 409
}
// 他の人の版を読み込み、すべてのタブの未保存の入力を捨てて合わせ直す
const reloadLatest = async () => {
  await refresh()
  for (const form of [formRef.value, tablesRef.value, departmentsRef.value]) form?.reset()
  conflicted.value = false
  versionsReloadKey.value = Date.now()
}

const onDelete = async () => {
  const ok = await confirm({
    title: t('Connectorを削除しますか？'),
    description: t('版の履歴は残ります'),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.manage.connectors(connectorId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    emit('changed')
    close()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}

const tabs = computed(() => [
  { label: t('設定'), value: 'settings', slot: 'settings' as const },
  { label: t('公開する表'), value: 'tables', slot: 'tables' as const },
  { label: t('使える部門'), value: 'departments', slot: 'departments' as const },
  { label: t('試し実行'), value: 'query', slot: 'query' as const },
  { label: t('版の履歴'), value: 'versions', slot: 'versions' as const },
])
</script>

<template>
  <DDrawer
    :open="true"
    :title="connector?.name ?? $t('Connector')"
    :description="connector ? $t('現在の版：{number}', { number: connector.version ?? 0 }) : undefined"
    :ui="{ content: 'sm:max-w-3xl' }"
    @update:open="(open) => !open && close()"
  >
    <template #content>
      <UAlert
        v-if="conflicted"
        color="warning"
        variant="subtle"
        icon="i-lucide-refresh-cw"
        class="mb-4"
        :title="$t('編集の間に、他の人が新しい版を作りました')"
        :actions="[{ label: $t('最新の状態を読み込む'), color: 'warning', variant: 'outline', onClick: reloadLatest }]"
      />
      <UTabs v-model="tab" :items="tabs" variant="link" :unmount-on-hide="false">
        <template #settings>
          <div class="pt-4">
            <ConnectorForm ref="formRef" v-model="connector" @updated="onUpdated" @failed="onFailed" />
          </div>
        </template>
        <template #tables>
          <div class="pt-4">
            <ConnectorTablesForm ref="tablesRef" v-model="connector" @updated="onUpdated" @failed="onFailed" />
          </div>
        </template>
        <template #departments>
          <div class="pt-4">
            <ConnectorDepartmentsForm
              ref="departmentsRef"
              v-model="connector"
              @updated="emit('changed')"
              @failed="onFailed"
            />
          </div>
        </template>
        <template #query>
          <div class="pt-4">
            <ConnectorQueryPanel v-if="connector" :connector="connector" />
          </div>
        </template>
        <template #versions>
          <div class="pt-4">
            <ConnectorVersionList v-model:reload-key="versionsReloadKey" :connector-id="connectorId" />
          </div>
        </template>
      </UTabs>
    </template>
    <template #footer>
      <!-- 削除はサーバー側でも権限を確認し、権限がなければ403になる -->
      <DBtn v-if="hasPermission(PermissionCode.DELETE_CONNECTOR)" color="error" variant="ghost" @click="onDelete">
        {{ $t('削除') }}
      </DBtn>
      <div class="flex-1" />
      <DBtn
        v-if="activeForm"
        :disabled="!activeForm.canPost"
        :loading="activeForm.loading"
        @click="activeForm.onSubmit()"
      >
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
