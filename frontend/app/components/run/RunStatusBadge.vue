<script setup lang="ts">
const props = defineProps<{ run: Pick<Run, 'status' | 'statusDisplay'> }>()
const active = computed(() => isActiveRun(props.run))
</script>

<template>
  <UBadge
    :color="runStatusColor(run.status)"
    variant="subtle"
    :icon="active ? 'i-lucide-loader-circle' : undefined"
    :ui="{ leadingIcon: 'animate-spin' }"
  >
    {{ run.statusDisplay }}
  </UBadge>
</template>
