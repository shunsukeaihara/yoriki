<script setup lang="ts">
// /manage 以下の全ページに適用される（route meta は親から子に引き継がれる）
definePageMeta({ layout: 'manage', middleware: 'manage' })
</script>

<template>
  <NuxtPage />
</template>
