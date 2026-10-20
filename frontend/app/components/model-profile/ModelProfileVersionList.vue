<script setup lang="ts">
/** モデル設定の版の履歴（読み取り専用）。新しい版から順に並べる */
const props = defineProps<{ modelProfileId: string }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const api = useAPI()
const { t, locale } = useI18n()
const formatNumber = (value: number) => new Intl.NumberFormat(locale.value).format(value)

const {
  items: versions,
  loading,
  fetchAll,
} = useFetchAll(() => api.manage.modelProfiles(props.modelProfileId).versions().list())
watch(reloadKey, fetchAll, { immediate: true })

const capabilities = (v: ModelProfileVersion) =>
  [
    v.supportsTools && t('Tool呼出し'),
    v.supportsImages && t('画像入力'),
    v.supportsStructuredOutput && t('構造化出力'),
    v.supportsPromptCaching && t('prompt caching'),
    v.supportsReasoning && t('推論の制御'),
  ].filter((label): label is string => Boolean(label))
</script>

<template>
  <div class="grid gap-3">
    <p v-if="loading && !versions.length" class="text-sm text-muted">{{ $t('読み込み中') }}</p>
    <UCard v-for="v in versions" :key="v.id" :ui="{ body: 'p-3 sm:p-3' }">
      <div class="flex items-center justify-between gap-2">
        <span class="font-semibold">{{ $t('版 {number}', { number: v.number }) }}</span>
        <span class="text-xs text-muted">{{ formatDatetime(v.created) }}</span>
      </div>
      <p v-if="v.changeReason" class="mt-1 whitespace-pre-wrap text-sm">{{ v.changeReason }}</p>
      <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt class="text-muted">{{ $t('LLMの接続') }}</dt>
        <dd>{{ v.connectionInfo.name }}（{{ v.connectionInfo.kindDisplay }}）</dd>
        <dt class="text-muted">{{ $t('モデル名') }}</dt>
        <dd class="font-mono">{{ v.modelName }}</dd>
        <dt class="text-muted">{{ $t('最大token') }}</dt>
        <dd>
          {{
            $t('入力 {input}・出力 {output}', {
              input: formatNumber(v.maxInputTokens),
              output: formatNumber(v.maxOutputTokens),
            })
          }}
        </dd>
        <dt class="text-muted">{{ $t('単価') }}</dt>
        <dd>
          {{
            $t('入力 {input}・キャッシュ {cached}・出力 {output}', {
              input: formatPrice(v.inputPrice),
              cached: formatPrice(v.cachedInputPrice),
              output: formatPrice(v.outputPrice),
            })
          }}
        </dd>
        <dt class="text-muted">{{ $t('対応する能力') }}</dt>
        <dd>{{ capabilities(v).join('、') || $t('なし') }}</dd>
      </dl>
    </UCard>
  </div>
</template>
