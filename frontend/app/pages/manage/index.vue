<script setup lang="ts">
// /manage は、メニューの最初の項目へ移動する（middleware/manage.ts で1つ以上あることを確認済み）
const { items } = useManageMenu()
const first = items.value[0]
if (first) await navigateTo(first.to, { replace: true })
</script>

<template>
  <div />
</template>
