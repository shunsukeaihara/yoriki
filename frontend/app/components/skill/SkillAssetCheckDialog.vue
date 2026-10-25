<script setup lang="ts">
/**
 * 資料の検証の結果と、前の版の同じ資料との差分（design.md §3.9・§11.8）。
 * MDは本文の差分、CSVは行ごとの差分（キーの列があればキーで対応付ける）、PPTXはスライドごとのテキストと構造の差分
 */
const props = defineProps<{ assetKey: string; validation: AssetValidation | null | undefined; baseLabel: string }>()
const { t } = useI18n()

const state = computed(() => assetCheckState(props.validation, t))
const diff = computed(() => props.validation?.diff ?? null)
const info = computed(() => (props.validation?.info ?? {}) as Record<string, unknown>)

const changeLabel = (change: string) => ({ added: t('追加'), removed: t('削除'), changed: t('変更') })[change] ?? change
const changeColor = (change: string) =>
  ({ added: 'success', removed: 'error', changed: 'warning' })[change] as 'success' | 'error' | 'warning'

const elementLabel = (kind: string) =>
  ({
    picture: t('画像'),
    chart: t('グラフ'),
    table: t('表'),
    graphic: t('SmartArt等'),
    media: t('動画・音声'),
    group: t('グループ'),
    shape: t('図形・テキスト'),
  })[kind] ?? kind

const infoLines = computed(() => {
  const value = info.value
  const lines: string[] = []
  if (value.encoding) lines.push(t('文字コード：{value}', { value: value.encoding }))
  if (value.delimiter) {
    const delimiter = ({ comma: t('カンマ'), tab: t('タブ'), semicolon: t('セミコロン') } as Record<string, string>)[
      String(value.delimiter)
    ]
    lines.push(t('区切り：{value}', { value: delimiter ?? value.delimiter }))
  }
  if (typeof value.rows === 'number') lines.push(t('{n}行', { n: value.rows }))
  if (Array.isArray(value.columns)) lines.push(t('列：{value}', { value: value.columns.join(', ') }))
  if (typeof value.lines === 'number') lines.push(t('{n}行', { n: value.lines }))
  if (typeof value.slides === 'number') lines.push(t('スライド{n}枚', { n: value.slides }))
  return lines
})

const lineClass = (line: string) => {
  if (line.startsWith('+')) return 'bg-success/10 text-success'
  if (line.startsWith('-')) return 'bg-error/10 text-error'
  if (line.startsWith('@@')) return 'text-muted'
  return ''
}
</script>

<template>
  <DDialog :ui="{ content: 'sm:max-w-3xl' }">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ $t('資料「{key}」の検証と差分', { key: assetKey }) }}</template>
    <template #content>
      <div class="grid gap-4">
        <div class="flex flex-wrap items-center gap-2">
          <UBadge :color="state.color" variant="subtle" :icon="state.icon">{{ state.label }}</UBadge>
          <span v-for="line in infoLines" :key="line" class="text-xs text-muted">{{ line }}</span>
        </div>
        <p v-if="!validation?.format" class="text-sm text-muted">
          {{ $t('この資料は、検証の仕組みを入れる前に公開されたため、検証の結果がありません。') }}
        </p>
        <UAlert
          v-if="validation?.errors?.length"
          color="error"
          variant="subtle"
          icon="i-lucide-circle-x"
          :title="$t('誤り（直すまで公開できません）')"
        >
          <template #description>
            <ul class="grid list-disc gap-0.5 pl-4">
              <li v-for="error in validation.errors" :key="error">{{ error }}</li>
            </ul>
          </template>
        </UAlert>
        <UAlert
          v-if="validation?.warnings?.length"
          color="warning"
          variant="subtle"
          icon="i-lucide-triangle-alert"
          :title="$t('注意')"
        >
          <template #description>
            <ul class="grid list-disc gap-0.5 pl-4">
              <li v-for="warning in validation.warnings" :key="warning">{{ warning }}</li>
            </ul>
          </template>
        </UAlert>

        <section class="grid gap-2">
          <h3 class="text-sm font-semibold text-muted">{{ $t('{base}との差分', { base: baseLabel }) }}</h3>
          <p v-if="!diff" class="text-sm text-muted">
            {{ $t('比べる前の版がありません（新しく加えた資料か、{base}と同じ原本です）', { base: baseLabel }) }}
          </p>
          <template v-else>
            <p class="text-sm">{{ assetDiffText(diff, $t) }}</p>

            <!-- MD -->
            <pre
              v-if="diff.text?.length"
              class="max-h-96 overflow-auto rounded-md bg-elevated p-2 text-xs"
            ><span v-for="(line, i) in diff.text" :key="i" class="block whitespace-pre-wrap" :class="lineClass(line)">{{ line || ' ' }}</span></pre>

            <!-- CSV -->
            <p v-if="diff.columnsAdded?.length" class="text-xs">
              {{ $t('列の追加：{columns}', { columns: diff.columnsAdded.join(', ') }) }}
            </p>
            <p v-if="diff.columnsRemoved?.length" class="text-xs text-error">
              {{ $t('列の削除：{columns}', { columns: diff.columnsRemoved.join(', ') }) }}
            </p>
            <div v-if="diff.rows?.length" class="max-h-96 overflow-auto rounded-md border border-default">
              <table class="w-full text-xs">
                <thead class="bg-elevated text-left text-muted">
                  <tr>
                    <th class="px-2 py-1">{{ $t('変更') }}</th>
                    <th class="px-2 py-1">{{ $t('キー・行') }}</th>
                    <th class="px-2 py-1">{{ $t('内容') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, i) in diff.rows" :key="i" class="border-t border-default align-top">
                    <td class="px-2 py-1">
                      <UBadge :color="changeColor(row.change)" variant="subtle" size="sm">
                        {{ changeLabel(row.change) }}
                      </UBadge>
                    </td>
                    <td class="px-2 py-1 font-mono">
                      {{ row.key || (row.line ? $t('{n}行目', { n: row.line }) : '') }}
                    </td>
                    <td class="px-2 py-1">
                      <div v-for="cell in row.cells" :key="cell.column" class="break-all">
                        <span class="text-muted">{{ cell.column }}：</span>
                        <template v-if="row.change === 'changed'">
                          <span class="text-error line-through">{{ cell.before }}</span>
                          →
                          <span class="text-success">{{ cell.after }}</span>
                        </template>
                        <span v-else>{{ row.change === 'added' ? cell.after : cell.before }}</span>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- PPTX -->
            <div v-for="slide in diff.slides ?? []" :key="`${slide.change}-${slide.slide}`" class="grid gap-1">
              <div class="flex flex-wrap items-center gap-2 text-sm">
                <UBadge :color="changeColor(slide.change)" variant="subtle" size="sm">
                  {{ changeLabel(slide.change) }}
                </UBadge>
                <span class="font-medium">{{ $t('{n}枚目', { n: slide.slide }) }}</span>
                <span
                  v-if="slide.change === 'changed' && slide.layoutBefore !== slide.layoutAfter"
                  class="text-xs text-muted"
                >
                  {{ $t('レイアウト：{before} → {after}', { before: slide.layoutBefore, after: slide.layoutAfter }) }}
                </span>
                <span v-else class="text-xs text-muted">{{ slide.layoutAfter ?? slide.layoutBefore }}</span>
              </div>
              <div v-for="(text, i) in slide.texts" :key="i" class="grid gap-0.5 pl-4 text-xs">
                <span class="text-muted">{{ text.shape }}</span>
                <span v-if="text.before !== null" class="whitespace-pre-wrap text-error line-through">{{
                  text.before
                }}</span>
                <span v-if="text.after !== null" class="whitespace-pre-wrap text-success">{{ text.after }}</span>
              </div>
              <p v-for="element in slide.elements" :key="element.kind" class="pl-4 text-xs">
                {{
                  $t('{kind}：{before} → {after}', {
                    kind: elementLabel(element.kind),
                    before: element.before,
                    after: element.after,
                  })
                }}
              </p>
            </div>

            <p v-if="diff.truncated" class="text-xs text-muted">
              {{ $t('変更が多いため、一部だけを表示しています') }}
            </p>
          </template>
        </section>
      </div>
    </template>
  </DDialog>
</template>
