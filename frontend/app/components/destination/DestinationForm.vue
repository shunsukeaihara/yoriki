<script setup lang="ts">
/**
 * 出口の作成と編集（連携の管理者。authorization.md §5.5、design.md §4.4）。種類・範囲・部門は作成した後は変えられない。
 * 送り先のURLと「機密の結果を送ってよい」を変えて保存すると新しい版になる。URLは書くだけで、サーバーはホストと
 * 末尾の4文字しか返さないので、編集では空欄なら変えない。
 *
 * 対話の出口（Slack・Discordのチャネル。design.md §4.6）は、URLの代わりに連携の接続とチャネルのIDを持つ。チャネルは、接続を
 * 選ぶとチャットツールから取った一覧から名前で絞って選ぶ（一覧に出ないもののためにIDの入力も残す）。サーバーは、botが
 * そのチャネルに参加しているかを確かめてチャネルの名前を記録するので、接続とチャネルは変えたときだけ送る
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
  // 作成のときだけ送る（種類はPluginのAdapterのID）
  extension: BuiltinExtension.WEBHOOK as string,
  scope: DestinationScope.DEPARTMENT as ConnectorScopeEnum,
  department: null as string | null,
  url: '',
  // 対話の出口：連携の接続とチャネルのID
  connection: null as string | null,
  channelId: '',
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
        extension: value.extension,
        connection: value.connection ?? null,
        channelId: value.channelId ?? '',
        allowsSensitiveResults: value.allowsSensitiveResults ?? false,
      }
    : emptyForm()
const form = ref(emptyForm())
/**
 * 入力を捨てて、今の出口に合わせ直す。送り先のURLはサーバーが返さず入力し直せないので、競合の後の読み直しでは
 * keepUrl で入力したURLを残す（他の人の版と比べる値がないので、送るかどうかは利用者が決める）
 */
const reset = ({ keepUrl = false }: { keepUrl?: boolean } = {}) => {
  const url = form.value.url
  form.value = toForm(modelValue.value)
  if (keepUrl) form.value.url = url
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
// 選べる種類（停止中・廃止予定のPluginのものは出ない）。結果の通知の出口と、対話の出口（チャネル）
const notification = useSelectableExtensions('notification')
const channel = useSelectableExtensions('channel')
const extensions = computed(() => [...notification.extensions.value, ...channel.extensions.value])
const find = (id: string) => notification.find(id) ?? channel.find(id)
// 対話の出口か。編集では出口の値、作成では選んだ種類で決める
const isChannel = computed(() =>
  isEdit.value ? Boolean(modelValue.value?.isChannel) : find(form.value.extension)?.type === 'channel',
)
const isDiscord = computed(() => form.value.extension === BuiltinExtension.DISCORD)
// 宛先のURLを持つ種類か。編集では出口の値、作成では選んだ種類の対応能力で決める
const hasUrl = computed(() =>
  isEdit.value
    ? Boolean(modelValue.value?.isExternal) && !isChannel.value
    : hasCapability(find(form.value.extension), Capability.URL),
)
// 種類を選び直したら、別の種類の接続を外す
watch(
  () => form.value.extension,
  () => {
    if (!isEdit.value) form.value.connection = null
  },
)
// 編集で選び直す前の接続の表示名
const initialConnection = computed(() =>
  modelValue.value?.connection
    ? { id: modelValue.value.connection, name: modelValue.value.connectionName ?? '' }
    : null,
)

// チャネルの一覧（capability list_channels の種類）。接続を選ぶとチャットツールから取り、名前で絞って選ぶ。一覧に出ない
// チャネル・一覧を取れないときのために、IDの入力に切り替えられる
const canListChannels = computed(
  () => isChannel.value && hasCapability(channel.find(form.value.extension), Capability.LIST_CHANNELS),
)
const channelList = ref<ChannelList | null>(null)
const channelListError = ref('')
const channelListLoading = ref(false)
const enterChannelId = ref(false)
let channelListRequest = 0
const loadChannels = async () => {
  const current = ++channelListRequest
  channelList.value = null
  channelListError.value = ''
  const connection = form.value.connection
  if (!connection || !canListChannels.value) return
  channelListLoading.value = true
  try {
    const found = await api.manage.channelConnections(connection).channels()
    if (current === channelListRequest) channelList.value = found
  } catch (err) {
    if (current === channelListRequest) channelListError.value = makeErrorMsg(err)
  } finally {
    if (current === channelListRequest) channelListLoading.value = false
  }
}
watch([() => form.value.connection, canListChannels], loadChannels, { immediate: true })
const showChannelSelect = computed(() => canListChannels.value && !enterChannelId.value && !channelListError.value)
// 選択肢。今のチャネルが一覧にない（botが外された等）ときも、選んだ状態を表示できるように足す
const channelItems = computed(() => {
  const items = (channelList.value?.channels ?? []).map((c) => ({
    value: c.channelId,
    title: c.name,
    description: c.unavailable || undefined,
    disabled: Boolean(c.unavailable),
  }))
  const channelId = form.value.channelId
  if (channelId && !items.some((i) => i.value === channelId)) {
    const name = channelId === modelValue.value?.channelId ? modelValue.value?.channelName : ''
    items.unshift({
      value: channelId,
      title: name || channelId,
      description: t('一覧にありません'),
      disabled: false,
    })
  }
  return items
})

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
    // 対話の出口の接続とチャネル。編集では変えたときだけ送る（送るとサーバーがチャットツールでチャネルを確かめ直す）
    const channelId = f.channelId.trim()
    const current = modelValue.value
    const target = !isChannel.value
      ? {}
      : !f.id || f.connection !== (current?.connection ?? null) || channelId !== (current?.channelId ?? '')
        ? { connection: f.connection, channelId }
        : {}
    if (!f.id) {
      const department = f.scope === DestinationScope.DEPARTMENT ? f.department : null
      return api.manage
        .destinations()
        .create({ ...common, ...target, extension: f.extension, scope: f.scope, department })
    }
    // 名前は版の外なので、変えたときだけ送る（古い画面からの保存で、別の人が変えた名前を戻さない）
    const name = f.name !== modelValue.value?.name ? f.name : undefined
    return api.manage.destinations(f.id).patch({ ...common, ...target, name, version: f.version })
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

// 必須項目が埋まっているかだけを確認する。URLの形と送ってよい宛先、botがチャネルにいるかはサーバーが確かめる
const canPost = computed(
  () =>
    Boolean(form.value.name.trim()) &&
    (!isChannel.value || (Boolean(form.value.connection) && Boolean(form.value.channelId.trim()))) &&
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
        v-model="form.extension"
        :label="$t('種類')"
        :hint="$t('作成した後は変更できません')"
        :items="extensionItems(extensions, t)"
        variant="card"
        :error-messages="errors.extension"
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
      <span>{{ modelValue?.extensionName }}</span>
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
      <template v-if="isChannel">
        <DAutocomplete
          :key="form.extension"
          v-model="form.connection"
          :label="$t('連携の接続')"
          required
          :search-fn="api.manage.channelConnections().list"
          :query-params="{ extension: form.extension }"
          item-description="workspaceName"
          :initial-selected="initialConnection"
          :hint="
            isDiscord
              ? $t('DiscordのBotとサーバー。「連携の接続」で登録したものから選びます')
              : $t('Slackのアプリ（Workspace）。「連携の接続」で登録したものから選びます')
          "
          :error-messages="errors.connection"
          @update:model-value="(value) => value !== form.connection && (form.channelId = '')"
        />
        <template v-if="showChannelSelect">
          <DSelect
            v-model="form.channelId"
            :label="$t('チャネル')"
            required
            searchable
            :items="channelItems"
            item-disabled="disabled"
            item-description="description"
            :disabled="!form.connection"
            :loading="channelListLoading"
            :placeholder="form.connection ? $t('チャネルを選択してください') : $t('先に連携の接続を選んでください')"
            :hint="
              [
                isDiscord
                  ? $t('Botが見られて、投稿とスレッドの作成ができるテキストチャネルだけを出します。')
                  : $t('公開チャネルを出します。botが参加していないものは選べません（チャネルでアプリを追加します）。'),
                channelList?.truncated ? $t('多すぎて一部だけを出しています。') : '',
              ].join('')
            "
            :error-messages="errors.channelId"
          />
          <div class="flex gap-2">
            <DBtn
              variant="link"
              color="neutral"
              class="px-0"
              :label="$t('IDで入力する')"
              @click="enterChannelId = true"
            />
            <DBtn
              variant="link"
              color="neutral"
              class="px-0"
              icon="i-lucide-refresh-cw"
              :label="$t('一覧を読み直す')"
              :disabled="!form.connection || channelListLoading"
              @click="loadChannels"
            />
          </div>
        </template>
        <DTextField
          v-else
          v-model="form.channelId"
          :label="$t('チャネルのID')"
          required
          :placeholder="isDiscord ? '123456789012345678' : 'C0123456789'"
          :hint="
            [
              isEdit && modelValue?.channelName ? $t('今のチャネル：{name}。', { name: modelValue.channelName }) : '',
              isDiscord
                ? $t(
                    'Discordの開発者モードで、テキストチャネルの右クリックの「IDをコピー」で取れる数字。Botがチャネルを見られて、投稿とスレッドの作成ができることを確かめて登録します',
                  )
                : $t('Slackのチャネルの詳細の下にあるID。botをチャネルに追加してから登録します'),
            ].join('')
          "
          :error-messages="errors.channelId"
        />
        <div v-if="canListChannels && !showChannelSelect" class="flex flex-wrap items-center gap-2 text-sm">
          <span v-if="channelListError" class="text-error">
            {{ $t('チャネルの一覧を取れませんでした：{error}', { error: channelListError }) }}
          </span>
          <DBtn
            variant="link"
            color="neutral"
            class="px-0"
            :label="channelListError ? $t('もう一度読む') : $t('一覧から選ぶ')"
            :disabled="!form.connection"
            @click="
              () => {
                enterChannelId = false
                if (channelListError) loadChannels()
              }
            "
          />
        </div>
      </template>
      <DSwitch
        v-model="form.allowsSensitiveResults"
        :label="$t('機密の結果を送ってよい')"
        :hint="
          isChannel
            ? $t('個人情報・機密情報を扱うタスクの質問と結果を、このチャネルへ投稿してよいときだけ付けます')
            : hasUrl
              ? $t('個人情報・機密情報を扱うタスクの結果を、この送り先へ送ってよいときだけ付けます')
              : $t(
                  '画面の通知はログインした画面の中だけで、結果を見られる人にだけ届くので、この指定によらず機密のタスクにも使えます',
                )
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
