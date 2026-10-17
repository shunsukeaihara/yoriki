<script setup lang="ts">
/** DRF の nonFieldErrors（項目に属さないエラー、403・409 等の detail）を表示する */
const props = defineProps<{ errors: ValidationErrors | null | undefined }>()
const messages = computed(() => props.errors?.nonFieldErrors ?? [])
</script>

<template>
  <UAlert v-if="messages.length" color="error" variant="subtle" icon="i-lucide-circle-alert">
    <template #description>
      <p v-for="(m, i) in messages" :key="i">{{ m }}</p>
    </template>
  </UAlert>
</template>
