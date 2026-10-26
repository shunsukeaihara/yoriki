# フロントエンド実装例（Nuxt / Nuxt UI）

[frontend-conventions.md](frontend-conventions.md) の規約に沿った典型的な実装例。題材はバックエンドの [backend-examples.md](backend-examples.md) に合わせている。

| 例 | パターン | 対応するAPI |
|---|---|---|
| [共通部品](#共通部品) | 実装済みの部品の場所と、これから作る部品 | — |
| [例1](#例1-管理画面のcrud通知先) | 管理画面のCRUD（一覧＋検索、新規ダイアログ、詳細ドロワー、削除の確認、権限による出し分け） | backend 例1 `v1/destinations` |
| [例2](#例2-社員画面のactionrunの停止と回答) | 社員画面のaction（一覧、詳細ドロワー、停止ダイアログ、質問への回答） | backend 例3 `app/v1/runs` |

画面の文言は frontend-conventions.md §10 のとおり、日本語の文をそのままキーにして `$t('...')` で書く。

---

## 共通部品

### 実装済み

コードは次のファイルを見る。

| 部品 | ファイル |
|---|---|
| `createSubmit`、`getValidationError`、`makeErrorMsg` | `frontend/app/composables/form.ts` |
| `useCursorList`、`useFetchAll` | `frontend/app/composables/list.ts` |
| `useConfirmDialog` | `frontend/app/composables/confirm.ts`、`frontend/app/components/common/DConfirmModal.vue` |
| `D*` ラッパー | `frontend/app/components/common/`（一覧は frontend-conventions.md §7.2） |
| `useAPI`、`resource`、`readOnlyResource`、`Paginated` | `frontend/app/api/` |
| `useAccountStore` | `frontend/app/composables/account.ts`（frontend-conventions.md §4） |
| `usePermission`、`PermissionCode` | `frontend/app/composables/permission.ts`（frontend-conventions.md §6） |
| 管理画面のlayoutとメニュー | `frontend/app/layouts/manage.vue`、`frontend/app/composables/manage.ts`、`frontend/app/middleware/manage.ts`、`frontend/app/pages/manage.vue` |
| エラー画面 | `frontend/app/error.vue`、`frontend/app/plugins/error-handler.ts` |
| `ListPageTitle`、`TableSearchForm` | `frontend/app/components/common/` |
| `formatDatetime` | `frontend/app/composables/date.ts` |
| 管理画面のCRUDの実装例（例1と同じ形） | `frontend/app/pages/manage/llm_connections.vue`、`model_profiles.vue`、`frontend/app/components/llm-connection/`、`model-profile/` |
| `useEventStream`、`useUserEvents` | `frontend/app/composables/stream.ts`（[realtime.md](realtime.md) §5） |
| `RunStatus`、`ClarificationStatus`、`ClarificationKind`、`isCancellable`、`isActiveRun` | `frontend/app/composables/run.ts` |
| `RunStatusBadge`、`RunEventLog`（作業の記録）、`RunWork`（会話の中の作業）、`RunClarificationForm`（確認への回答） | `frontend/app/components/run/` |
| `MarkdownContent`、`renderMarkdown` | `frontend/app/components/common/MarkdownContent.vue`、`frontend/app/composables/markdown.ts`（frontend-conventions.md §7.5） |
| Skillの画面（Draft の revision での競合の検出、multipart の資料、operation key、権限による出し分け） | `frontend/app/pages/skills/`、`frontend/app/components/skill/`、`frontend/app/composables/skill.ts` |
| 会話の画面（REST で取得し、ストリームのイベントで取得し直す実装例） | `frontend/app/pages/conversations/[conversationId].vue`、`frontend/app/composables/conversation.ts`、`frontend/app/components/conversation/` |

### これから作るもの

| 部品 | 置く場所 |
|---|---|
| `runStatusItems`（実行履歴の検索条件） | `frontend/app/composables/run.ts` |

---

## 例1 管理画面のCRUD（通知先）

### composables/delivery.ts

```ts
// バックエンドの DestinationKind と同じ値
export const DestinationKind = {
  WEBHOOK: 1,
  SLACK: 2,
  TEAMS: 3,
  DISCORD: 4,
  EMAIL: 5,
} as const

// 検索条件の選択肢。一覧の表示はサーバーの kindDisplay を使う
export const destinationKindItems = (t: (key: string) => string) => [
  { value: DestinationKind.WEBHOOK, title: t('Webhook') },
  { value: DestinationKind.SLACK, title: t('Slack') },
  { value: DestinationKind.TEAMS, title: t('Teams') },
  { value: DestinationKind.DISCORD, title: t('Discord') },
  { value: DestinationKind.EMAIL, title: t('メール') },
]
```

### components/destination/DestinationForm.vue

```vue
<script setup lang="ts">
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<Destination | null>()
const emit = defineEmits<{
  created: [value: Destination]
  updated: [value: Destination]
  failed: [error: unknown]
}>()

const emptyForm = () => ({
  id: null as string | null,
  name: '',
  kind: DestinationKind.WEBHOOK as number,
  address: '',
  isActive: true,
})
const form = ref(emptyForm())

watch(
  modelValue,
  (value) => {
    form.value = value
      ? { id: value.id, name: value.name, kind: value.kind, address: value.address, isActive: value.isActive }
      : emptyForm()
  },
  { immediate: true },
)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ id, ...data }) =>
    id ? api.manage.destinations(id).patch(data) : api.manage.destinations().create(data),
  onSuccess: (result) => {
    // emit の名前を三項演算子で選ぶと型が合わないので、分けて呼ぶ
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

// 必須項目が埋まっているかだけを確認する。形式の検証はサーバーに任せる
const canPost = computed(() => Boolean(form.value.name && form.value.address))

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />
    <DSelect v-model="form.kind" :label="$t('種別')" :items="destinationKindItems(t)" :error-messages="errors.kind" />
    <DTextField
      v-model="form.address"
      :label="$t('宛先')"
      required
      :hint="$t('WebhookとTeamsはhttps://で始まるURL')"
      :error-messages="errors.address"
    />
    <DSwitch v-model="form.isActive" :label="$t('有効')" :error-messages="errors.isActive" />
  </DFormStack>
</template>
```

### components/destination/DestinationDialog.vue

```vue
<script setup lang="ts">
import DestinationForm from './DestinationForm.vue'
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ destination?: Destination | null }>()
const emit = defineEmits<{ saved: [value: Destination] }>()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof DestinationForm> | null>(null)
const editing = ref<Destination | null>(null)
// 見出しとボタンは props で決める（保存後に editing が変わっても表示が切り替わらないように）
const isEdit = computed(() => Boolean(props.destination))

// 開くたびに対象を入れ直し、前回の入力やエラーを残さない
const onOpen = (open: boolean) => {
  if (open) editing.value = props.destination ?? null
}

const saved = (value: Destination) => {
  emit('saved', value)
  dialog.value?.closeDialog()
}
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ isEdit ? $t('通知先を編集') : $t('通知先を追加') }}</template>
    <template #content>
      <DestinationForm ref="formRef" v-model="editing" @created="saved" @updated="saved" />
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ isEdit ? $t('更新') : $t('保存') }}
      </DBtn>
    </template>
  </DDialog>
</template>
```

### composables/manage.ts（メニューに追加する）

`pages/manage.vue` と `middleware/manage.ts` は実装済みで、変更しない。ページを追加したら、メニューの項目を足す。`/manage` に入れるかどうかは、表示できる項目があるかで決まる。

```ts
const { $i18n } = useNuxtApp()
const all = computed<ManageMenuItem[]>(() => [
  {
    label: $i18n.t('通知先'),
    icon: 'i-lucide-send',
    to: '/manage/destinations',
    // リンク先のページの definePageMeta と同じ権限
    permission: PermissionCode.CHANGE_DESTINATION,
  },
])
```

### pages/manage/destinations.vue

```vue
<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

definePageMeta({ permission: PermissionCode.CHANGE_DESTINATION })

const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())

const emptyParams = () => ({
  name: undefined as string | undefined,
  kind: undefined as number | undefined,
  isActive: undefined as boolean | undefined,
})
const params = ref(emptyParams())
const clearParams = () => {
  params.value = emptyParams()
  reload()
}

const columns: TableColumn<Destination>[] = [
  { accessorKey: 'name', header: t('名前') },
  { accessorKey: 'kindDisplay', header: t('種別') },
  { accessorKey: 'isActive', header: t('有効') },
  { accessorKey: 'updated', header: t('更新日時') },
]
</script>

<template>
  <UContainer>
    <ListPageTitle :title="$t('通知先')">
      <template #end>
        <DestinationDialog @saved="reload">
          <template #trigger="{ on }">
            <DBtn icon="i-lucide-plus" v-on="on">{{ $t('通知先を追加') }}</DBtn>
          </template>
        </DestinationDialog>
      </template>
    </ListPageTitle>

    <TableSearchForm :reload="reload" :clear="clearParams">
      <DTextField v-model="params.name" :label="$t('名前')" />
      <DSelect v-model="params.kind" :label="$t('種別')" :items="destinationKindItems(t)" clearable />
    </TableSearchForm>

    <DDataTable
      v-model:reload-key="reloadKey"
      :columns="columns"
      :fetch="api.manage.destinations().list"
      :params="params"
    >
      <template #name-cell="{ row }">
        <NuxtLink :to="`/manage/destinations/${row.original.id}`" class="text-primary">
          {{ row.original.name }}
        </NuxtLink>
      </template>
      <template #isActive-cell="{ row }">
        <UBadge :color="row.original.isActive ? 'success' : 'neutral'" variant="subtle">
          {{ row.original.isActive ? $t('有効') : $t('無効') }}
        </UBadge>
      </template>
      <template #updated-cell="{ row }">{{ formatDatetime(row.original.updated) }}</template>
    </DDataTable>
  </UContainer>
  <NuxtPage @changed="reload" />
</template>
```

`kindDisplay` は、Serializerに `kind_display = serializers.CharField(source="get_kind_display", read_only=True)` を追加して返す（frontend-conventions.md §8.3）。

### pages/manage/destinations/[destinationId].vue

```vue
<script setup lang="ts">
import DestinationForm from '~/components/destination/DestinationForm.vue'

const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const { hasPermission } = usePermission()
const destinationId = route.params.destinationId as string

const { data: destination, error } = await useAsyncData(`destination-${destinationId}`, () =>
  api.manage.destinations(destinationId).get(),
)
if (error.value) throw createError(error.value)   // 404 / 403 はエラー画面にする

const formRef = ref<InstanceType<typeof DestinationForm> | null>(null)
const close = () => router.push('/manage/destinations')

const onDelete = async () => {
  const ok = await confirm({ title: t('通知先を削除しますか？'), confirmLabel: t('削除'), color: 'error' })
  if (!ok) return
  try {
    await api.manage.destinations(destinationId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    emit('changed')
    close()
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <DDrawer :open="true" :title="$t('通知先')" @update:open="(open) => !open && close()">
    <template #content>
      <DestinationForm ref="formRef" v-model="destination" @updated="emit('changed')" />
    </template>
    <template #footer>
      <!-- 削除はサーバー側でも権限を確認し、権限がなければ403になる -->
      <DBtn
        v-if="hasPermission(PermissionCode.DELETE_DESTINATION)"
        color="error"
        variant="ghost"
        @click="onDelete"
      >
        {{ $t('削除') }}
      </DBtn>
      <div class="flex-1" />
      <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
        {{ $t('更新') }}
      </DBtn>
    </template>
  </DDrawer>
</template>
```

要点：
- Formは新規（Dialog）と編集（Drawer）で同じものを使う。送信ボタンはFormの外に置き、`ref` から `onSubmit` を呼ぶ。
- 入力の形式の検証（`https://`、メールの形式）はサーバーで行い、返ってきたエラーをそのまま入力欄に表示する。フロントエンドの `canPost` は必須項目の確認だけにする。
- 詳細は子ルートのドロワーで開くので、URLを共有すれば同じ画面を開ける。更新や削除の後は `changed` をemitして一覧を再読込する。

---

## 例2 社員画面のaction（Runの停止と回答）

### pages/runs.vue

```vue
<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'

const api = useAPI()
const { t } = useI18n()
const reloadKey = ref(0)
const reload = () => (reloadKey.value = Date.now())
const params = ref({ status: undefined as number | undefined })

const columns: TableColumn<Run>[] = [
  { accessorKey: 'id', header: 'ID' },
  { accessorKey: 'statusDisplay', header: t('状態') },
  { accessorKey: 'created', header: t('開始日時') },
]
</script>

<template>
  <UContainer>
    <ListPageTitle :title="$t('実行履歴')" />
    <TableSearchForm :reload="reload" :clear="() => { params.status = undefined; reload() }">
      <DSelect v-model="params.status" :label="$t('状態')" :items="runStatusItems(t)" clearable />
    </TableSearchForm>
    <DDataTable v-model:reload-key="reloadKey" :columns="columns" :fetch="api.runs().list" :params="params">
      <template #id-cell="{ row }">
        <NuxtLink :to="`/runs/${row.original.id}`" class="text-primary">{{ row.original.id }}</NuxtLink>
      </template>
      <template #statusDisplay-cell="{ row }">
        <RunStatusBadge :run="row.original" />
      </template>
      <template #created-cell="{ row }">{{ formatDatetime(row.original.created) }}</template>
    </DDataTable>
  </UContainer>
  <NuxtPage @changed="reload" />
</template>
```

### components/run/RunCancelDialog.vue

停止は `POST app/v1/runs/{id}/cancel/` というactionで依頼する。画面から状態の値を送ることはない。

```vue
<script setup lang="ts">
import DDialog from '../common/DDialog.vue'

const props = defineProps<{ run: Run }>()
const emit = defineEmits<{ cancelled: [value: Run] }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const dialog = ref<InstanceType<typeof DDialog> | null>(null)

const form = ref<RunCancel>({ reason: '' })
const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: (data) => api.runs(props.run.id).cancel(data),
  onSuccess: (run) => {
    emit('cancelled', run)
    dialog.value?.closeDialog()
    // 実行中の場合はWorkerが停止を確認するまで「停止中」になる
    toast.add({ title: run.statusDisplay, color: 'success' })
  },
  onError: (err) => toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' }),
})
</script>

<template>
  <DDialog ref="dialog">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ $t('実行を停止') }}</template>
    <template #content>
      <DFormStack>
        <DFormAlert :errors="errors" />
        <DTextarea v-model="form.reason" :label="$t('理由（任意）')" :error-messages="errors.reason" />
      </DFormStack>
    </template>
    <template #footer="{ closeDialog }">
      <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
      <DBtn color="error" :loading="loading" @click="onSubmit">{{ $t('停止する') }}</DBtn>
    </template>
  </DDialog>
</template>
```

### components/run/RunClarificationForm.vue

確認への回答は、実装済みの `frontend/app/components/run/RunClarificationForm.vue` を使う。質問（`questions`）ごとに選択肢（1つ選ぶなら `DRadioGroup`、複数なら `DCheckboxGroup`）と自由記述を出し、質問と同じ順の `answers` と全体の補足（`comment`）を `createSubmit` で送る。409（Web画面と外部のスレッドから同時に回答された場合）も `DFormAlert` に表示する。

### pages/runs/[runId].vue

```vue
<script setup lang="ts">
const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const api = useAPI()
const runId = route.params.runId as string

const { data: run, error, refresh } = await useAsyncData(`run-${runId}`, () => api.runs(runId).get())
if (error.value) throw createError(error.value)

// 回答待ちの質問は件数が少ないので全件を取得する
const { items: clarifications, fetchAll } = useFetchAll(() =>
  api.runs(runId).clarifications().list({ status: ClarificationStatus.OPEN }),
)
await fetchAll()

// 表示はRESTで取得し、ストリームのイベントを受けたら取得し直す（realtime.md §5）
useEventStream(`app/v1/runs/${runId}/stream/`, {
  onEvent: (e) => {
    switch (e.type) {
      case 'run.status':
        refresh()
        break
      case 'clarification.requested':
        fetchAll()
        break
    }
  },
})

const onChanged = async () => {
  await Promise.all([refresh(), fetchAll()])
  emit('changed')
}
</script>

<template>
  <DDrawer :open="true" :title="$t('実行の詳細')" @update:open="(open) => !open && router.push('/runs')">
    <template #content>
      <div v-if="run" class="grid gap-4">
        <RunStatusBadge :run="run" />
        <RunClarificationForm
          v-for="c in clarifications"
          :key="c.id"
          :run-id="runId"
          :clarification="c"
          @answered="onChanged"
        />
        <RunWork :run="run" />
      </div>
    </template>
    <template #footer>
      <RunCancelDialog v-if="run && isCancellable(run)" :run="run" @cancelled="onChanged">
        <template #trigger="{ on }">
          <DBtn color="error" variant="ghost" icon="i-lucide-square" v-on="on">{{ $t('停止') }}</DBtn>
        </template>
      </RunCancelDialog>
    </template>
  </DDrawer>
</template>
```

要点：
- 停止・回答は、ViewSetの `@action` に対応するAPIメソッド（`api.runs(id).cancel()`、`.clarifications(cid).answer()`）を、同じ `createSubmit` で呼ぶ。
- 状態の表示はSerializerの `statusDisplay` を使う。停止ボタンを出すかどうかの判定（`isCancellable`）だけを、`composables/run.ts` に置く。このファイルには `RunStatus`、`ClarificationStatus`、`isCancellable` 等をまとめる。
- actionが返したRunで画面を更新し、一覧には `changed` で再読込を知らせる。
- 進捗は `useEventStream` で `app/v1/runs/{id}/stream/` から受け取り、`run.status` イベントで `refresh()`、`clarification.requested` イベントで `fetchAll()` を呼ぶ（[realtime.md](realtime.md) §3、§5）。ストリームはapiEndpoint（`/api/`）からの相対パスで渡す。
- 詳細の取得に失敗したら、`useAsyncData` の `error` を `createError` に渡してエラー画面にする（frontend-conventions.md §3.5）。
