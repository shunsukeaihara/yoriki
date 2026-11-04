<script setup lang="ts">
/** 鍵を作った人がタスクを管理できない入口の警告（自動では止めない。authorization.md §5.5）。鍵を作り直すと消える */
const props = defineProps<{ createdBy: MinimumUser | null }>()
const { t } = useI18n()
const description = computed(() =>
  props.createdBy
    ? t(
        '鍵を作った {name} さんは、今はこのタスクを管理できません。手元に鍵が残っているおそれがあります。鍵を作り直すかを確かめてください',
        { name: props.createdBy.displayName || props.createdBy.email },
      )
    : t('この鍵を誰が作ったかの記録がなく、誰の手元にあるか分かりません。鍵を作り直すかを確かめてください'),
)
</script>

<template>
  <UAlert
    color="warning"
    variant="subtle"
    icon="i-lucide-key-round"
    :title="$t('鍵の作り直しを確かめてください')"
    :description="description"
  />
</template>
