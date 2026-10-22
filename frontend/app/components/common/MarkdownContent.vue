<script setup lang="ts">
/** Markdown を表示する。HTML への変換と安全のための制限は renderMarkdown()（composables/markdown.ts） */
const props = defineProps<{ text: string }>()
const html = computed(() => renderMarkdown(props.text))
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -- renderMarkdown() は生の HTML を書いたまま表示し、画像も出さない -->
  <div class="markdown-content" v-html="html" />
</template>

<style scoped>
.markdown-content {
  overflow-wrap: anywhere;
  line-height: 1.75;
}
.markdown-content :deep(> :first-child) {
  margin-top: 0;
}
.markdown-content :deep(> :last-child) {
  margin-bottom: 0;
}
.markdown-content :deep(p),
.markdown-content :deep(ul),
.markdown-content :deep(ol),
.markdown-content :deep(pre),
.markdown-content :deep(blockquote),
.markdown-content :deep(table) {
  margin-block: 0.75em;
}
.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4) {
  margin-block: 1em 0.5em;
  font-weight: 700;
}
.markdown-content :deep(h1) {
  font-size: 1.25em;
}
.markdown-content :deep(h2) {
  font-size: 1.15em;
}
.markdown-content :deep(ul) {
  padding-left: 1.5em;
  list-style: disc;
}
.markdown-content :deep(ol) {
  padding-left: 1.5em;
  list-style: decimal;
}
.markdown-content :deep(a) {
  color: var(--ui-primary);
  text-decoration: underline;
}
.markdown-content :deep(code) {
  padding: 0.1em 0.3em;
  border-radius: var(--ui-radius);
  background: var(--ui-bg-elevated);
  font-size: 0.9em;
}
.markdown-content :deep(pre) {
  overflow-x: auto;
  padding: 0.75em 1em;
  border: 1px solid var(--ui-border);
  border-radius: calc(var(--ui-radius) * 2);
  background: var(--ui-bg-muted);
}
.markdown-content :deep(pre code) {
  padding: 0;
  background: none;
}
.markdown-content :deep(blockquote) {
  padding-left: 1em;
  border-left: 3px solid var(--ui-border-accented);
  color: var(--ui-text-muted);
}
.markdown-content :deep(table) {
  display: block;
  overflow-x: auto;
  border-collapse: collapse;
}
.markdown-content :deep(th),
.markdown-content :deep(td) {
  padding: 0.25em 0.75em;
  border: 1px solid var(--ui-border);
}
.markdown-content :deep(hr) {
  margin-block: 1em;
  border-color: var(--ui-border);
}
</style>
