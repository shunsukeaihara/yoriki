<script setup lang="ts">
/**
 * 値を埋め込んだタスクの依頼文。埋め込んだ値（信頼しない内容として囲んだもの）を目立たせ、どこから来た値かを
 * 添える。文字として表示する（HTML にしない）
 */
const props = defineProps<{ text: string }>()
const { t } = useI18n()
const segments = computed(() => splitUntrusted(props.text))
const sourceLabel = (source: string) =>
  source.startsWith('input:') ? t('入力「{name}」の値', { name: source.slice('input:'.length) }) : source
</script>

<template>
  <span class="whitespace-pre-wrap"
    ><template v-for="(segment, index) in segments" :key="index"
      ><mark
        v-if="segment.source !== null"
        class="rounded bg-primary/10 px-0.5 text-highlighted"
        :title="sourceLabel(segment.source)"
        >{{ segment.text }}</mark
      ><template v-else>{{ segment.text }}</template></template
    ></span
  >
</template>
