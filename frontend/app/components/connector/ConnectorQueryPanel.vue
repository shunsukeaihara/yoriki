<script setup lang="ts">
/**
 * エージェントから見える内容の確認。公開している表・列の説明（datasource_describe）と、SELECT文の試し実行
 * （datasource_query）を、保存した版の範囲・上限でエージェントと同じ検査を通して行う
 */
const props = defineProps<{ connector: Connector }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()

const waitSeconds = computed(() => connectorWaitSeconds(props.connector))

// --- 公開している表・列の説明 ---
const described = ref<ConnectorTables | null>(null)
const describing = ref(false)
const onDescribe = async () => {
  describing.value = true
  try {
    described.value = await api.manage.connectors(props.connector.id).describe(waitSeconds.value)
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    describing.value = false
  }
}

// --- 試し実行 ---
const sql = ref('')
const paramsText = ref('')
const params = computed(() => parseQueryParams(paramsText.value, t))
const result = ref<ConnectorQueryResult | null>(null)
const running = ref(false)
const onRun = async () => {
  running.value = true
  result.value = null
  try {
    result.value = await api.manage
      .connectors(props.connector.id)
      .query({ sql: sql.value, params: params.value.value }, waitSeconds.value)
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  } finally {
    running.value = false
  }
}

const formatBytes = (bytes: number) =>
  bytes < 1024 * 1024 ? t('{n}KB', { n: Math.ceil(bytes / 1024) }) : t('{n}MB', { n: (bytes / 1024 / 1024).toFixed(1) })
</script>

<template>
  <div class="grid gap-6">
    <DFormSection
      :title="$t('エージェントに見える表・列')"
      :description="$t('公開している表・ビューの列と説明です。保存した版の内容を表示します')"
    >
      <div>
        <DBtn color="neutral" variant="outline" icon="i-lucide-table-2" :loading="describing" @click="onDescribe">
          {{ described ? $t('表示し直す') : $t('表示する') }}
        </DBtn>
      </div>
      <UAlert
        v-if="described && !described.ok"
        color="error"
        variant="subtle"
        icon="i-lucide-circle-x"
        :title="$t('読み込めませんでした')"
        :description="described.detail"
      />
      <p v-else-if="described && !described.tables.length" class="text-sm text-muted">
        {{ $t('公開している表・ビューはありません') }}
      </p>
      <UCard v-for="table in described?.tables ?? []" :key="tableLabel(table)" :ui="{ body: 'p-3 sm:p-3' }">
        <div class="flex items-center gap-2">
          <span class="font-mono text-sm font-semibold">{{ tableLabel(table) }}</span>
          <UBadge v-if="table.kind === 'view'" color="neutral" variant="subtle" size="sm">{{ $t('ビュー') }}</UBadge>
        </div>
        <p v-if="table.description" class="mt-1 text-sm whitespace-pre-wrap">{{ table.description }}</p>
        <dl class="mt-2 grid grid-cols-[auto_auto_1fr] gap-x-3 gap-y-1 text-xs">
          <template v-for="column in table.columns" :key="column.name">
            <dt class="font-mono">{{ column.name }}</dt>
            <dd class="text-muted">{{ column.type }}</dd>
            <dd>{{ column.description }}</dd>
          </template>
        </dl>
      </UCard>
    </DFormSection>

    <DFormSection
      :title="$t('試し実行')"
      :description="
        $t('エージェントと同じ検査と上限で実行します。公開した表・ビューだけを使えます。表示は先頭の200行までです')
      "
    >
      <DTextarea
        v-model="sql"
        :label="$t('SELECT文')"
        :rows="5"
        :hint="$t('引数の場所は ? で書きます')"
        placeholder="SELECT code, name FROM vendors WHERE code = ?"
        class="font-mono"
      />
      <DTextField
        v-model="paramsText"
        :label="$t('引数')"
        :hint="$t('JSONの配列で書きます。引数がなければ空欄にします')"
        placeholder='["A01"]'
        :error-messages="params.error ? [params.error] : undefined"
        class="font-mono"
      />
      <div>
        <DBtn icon="i-lucide-play" :disabled="!sql.trim() || Boolean(params.error)" :loading="running" @click="onRun">
          {{ $t('実行') }}
        </DBtn>
      </div>

      <UAlert
        v-if="result && !result.ok"
        color="error"
        variant="subtle"
        icon="i-lucide-circle-x"
        :title="$t('実行できませんでした')"
        :description="result.detail"
      />
      <div v-else-if="result" class="grid gap-2">
        <p class="text-sm">
          {{
            $t('{count}行・{size}・{ms}ミリ秒', {
              count: result.rowCount,
              size: formatBytes(result.csvBytes),
              ms: result.elapsedMs,
            })
          }}
        </p>
        <UAlert
          v-if="result.truncated"
          color="warning"
          variant="subtle"
          icon="i-lucide-scissors"
          :title="$t('行数・サイズの上限で打ち切りました')"
        />
        <UCollapsible>
          <DBtn color="neutral" variant="link" size="xs" icon="i-lucide-chevron-down" class="px-0">
            {{ $t('実行したSQL') }}
          </DBtn>
          <template #content>
            <pre class="mt-1 overflow-x-auto rounded-md bg-elevated p-2 text-xs">{{ result.sql }}</pre>
          </template>
        </UCollapsible>
        <div v-if="result.columns.length" class="max-h-96 overflow-auto rounded-md border border-default">
          <table class="w-full text-xs">
            <thead class="sticky top-0 bg-elevated text-left text-muted">
              <tr>
                <th v-for="column in result.columns" :key="column" class="px-2 py-1 font-mono whitespace-nowrap">
                  {{ column }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in result.rows" :key="i" class="border-t border-default align-top">
                <td v-for="(value, j) in row" :key="j" class="px-2 py-1 whitespace-pre-wrap">{{ value }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="result.previewTruncated" class="text-xs text-muted">
          {{ $t('先頭の{shown}行を表示しています', { shown: result.rows.length }) }}
        </p>
      </div>
    </DFormSection>
  </div>
</template>
