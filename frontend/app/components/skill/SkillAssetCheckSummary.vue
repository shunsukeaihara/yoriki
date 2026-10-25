<script setup lang="ts">
/** 資料の検証の状態と、前の版との差分の数（一覧の1行） */
const props = defineProps<{ validation: AssetValidation | null | undefined }>()
const { t } = useI18n()
const state = computed(() => assetCheckState(props.validation, t))
const diff = computed(() => {
  const value = props.validation?.diff
  if (!value) return ''
  return value.identical ? t('中身の変更なし') : assetDiffText(value, t)
})
</script>

<template>
  <div class="flex flex-wrap items-center gap-2 text-xs">
    <UBadge :color="state.color" variant="subtle" size="sm" :icon="state.icon">{{ state.label }}</UBadge>
    <span v-if="diff" class="text-muted">{{ diff }}</span>
  </div>
</template>
