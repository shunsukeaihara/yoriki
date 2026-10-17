<script setup lang="ts">
/** プロジェクト標準のボタン。サイズの既定値をそろえる以外は UButton と同じ */
defineOptions({ inheritAttrs: false })
</script>

<template>
  <UButton size="sm" v-bind="$attrs">
    <template v-for="(_, name) in $slots" #[name]="slotProps">
      <slot :name="name" v-bind="slotProps ?? {}" />
    </template>
  </UButton>
</template>
