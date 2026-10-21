<script setup lang="ts">
// 作業の計画（エージェントが todo_write で書いたもの）
defineProps<{ todos: Todo[] }>()

const icon = (status: Todo['status']) => {
  switch (status) {
    case TodoStatus.COMPLETED:
      return 'i-lucide-circle-check'
    case TodoStatus.IN_PROGRESS:
      return 'i-lucide-circle-dot'
    default:
      return 'i-lucide-circle'
  }
}
</script>

<template>
  <ul class="grid gap-1 text-sm">
    <li v-for="(todo, index) in todos" :key="index" class="flex items-start gap-2">
      <UIcon
        :name="icon(todo.status)"
        :class="['mt-0.5 size-4 shrink-0', todo.status === TodoStatus.IN_PROGRESS ? 'text-primary' : 'text-muted']"
      />
      <span
        :class="{
          'text-muted line-through': todo.status === TodoStatus.COMPLETED,
          'font-medium': todo.status === TodoStatus.IN_PROGRESS,
        }"
      >
        {{ todo.content }}
      </span>
    </li>
  </ul>
</template>
