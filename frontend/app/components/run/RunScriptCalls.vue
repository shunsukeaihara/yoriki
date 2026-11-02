<script setup lang="ts">
/**
 * Skill のスクリプトからの Connector の呼出し（design.md §4.9.6）。作業の記録の中で、まとめて折り畳んで出す。
 * 結果の行はモデルにもここにも出さず、行数と失敗の理由だけを出す。
 */
const props = withDefaults(defineProps<{ calls: ScriptCall[]; during?: boolean }>(), { during: false })
const { t } = useI18n()

// 1回の Run で 1,000 回まで呼べるので、先頭から決まった数ずつ出す
const PAGE = 100
const limit = ref(PAGE)

const failed = computed(() => props.calls.filter((c) => c.status === ToolStatus.FAILED).length)
const shown = computed(() => props.calls.slice(0, limit.value))

// コマンドの下に出すときは、その終わりを待つ間に処理した照会（バックグラウンドのジョブからの照会も含みうる。
// どのジョブからの要求かは記録していない）
const summary = computed(() => {
  const n = props.calls.length
  if (props.during)
    return failed.value
      ? t('この間に処理したスクリプトからの照会（{n}件、失敗 {failed}件）', { n, failed: failed.value })
      : t('この間に処理したスクリプトからの照会（{n}件）', { n })
  return failed.value
    ? t('スクリプトからの照会（{n}件、失敗 {failed}件）', { n, failed: failed.value })
    : t('スクリプトからの照会（{n}件）', { n })
})

const outcome = (call: ScriptCall) => {
  switch (call.status) {
    case ToolStatus.SUCCEEDED:
      if (call.operation === 'datasource_describe') return t('表 {n}個', { n: call.tables ?? 0 })
      return call.truncated ? t('{n}行（上限で打ち切り）', { n: call.rows ?? 0 }) : t('{n}行', { n: call.rows ?? 0 })
    case ToolStatus.FAILED:
      return call.errorCode ? t('失敗（{code}）', { code: call.errorCode }) : t('失敗')
    default:
      return call.statusDisplay
  }
}
</script>

<template>
  <UCollapsible>
    <DBtn
      variant="link"
      color="neutral"
      size="xs"
      class="px-0"
      icon="i-lucide-database"
      trailing-icon="i-lucide-chevron-down"
    >
      {{ summary }}
    </DBtn>
    <template #content>
      <ul class="mt-1 grid gap-2 border-l-2 border-default pl-3 text-xs">
        <li v-for="call in shown" :key="call.id" class="grid gap-1">
          <p class="flex flex-wrap items-center gap-x-2">
            <UIcon
              :name="call.status === ToolStatus.FAILED ? 'i-lucide-triangle-alert' : 'i-lucide-database'"
              :class="['size-3.5 shrink-0', call.status === ToolStatus.FAILED ? 'text-error' : 'text-muted']"
            />
            <span>{{ toolLabel({ name: call.operation, arguments: { connector: call.connector } }, t) }}</span>
            <span :class="call.status === ToolStatus.FAILED ? 'text-error' : 'text-muted'">{{ outcome(call) }}</span>
          </p>
          <pre v-if="call.sql" class="max-h-40 overflow-auto rounded-md bg-muted p-2 whitespace-pre-wrap">{{
            call.params.length ? `${call.sql}\n-- params: ${JSON.stringify(call.params)}` : call.sql
          }}</pre>
          <p v-if="call.error" class="whitespace-pre-wrap text-muted">{{ call.error }}</p>
        </li>
        <li v-if="calls.length > limit">
          <DBtn variant="link" color="neutral" size="xs" class="px-0" @click="limit += PAGE">
            {{ $t('さらに表示（残り {n}件）', { n: calls.length - limit }) }}
          </DBtn>
        </li>
      </ul>
    </template>
  </UCollapsible>
</template>
