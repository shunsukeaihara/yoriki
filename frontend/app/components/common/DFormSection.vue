<script setup lang="ts">
/** フォームの区切り。見出しと説明の下に入力を並べる */
defineProps<{ title?: string; description?: string }>()
</script>

<template>
  <section class="grid gap-3">
    <div v-if="title || description || $slots.title">
      <h3 class="text-sm font-semibold text-highlighted">
        <slot name="title">{{ title }}</slot>
      </h3>
      <p v-if="description" class="text-xs text-muted">{{ description }}</p>
    </div>
    <div class="grid gap-4">
      <slot />
    </div>
  </section>
</template>
