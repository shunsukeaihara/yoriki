<script setup lang="ts">
/**
 * Skill の編集の Run で変えた Skill（design.md §11.6）。Skill の名前・範囲・反映の状態・版・変更点を出し、Skill の画面
 * （版の中身・変更の記録・切戻し）へのリンクを置く。公開していなければ「変更案を保存」と示し、公開済みと混同させない。
 */
const props = defineProps<{ changes: RunSkillChange[] }>()
const { t } = useI18n()

const stateOf = (change: RunSkillChange) => {
  switch (change.state) {
    case 'published':
      return { label: t('第{number}版を公開・次の依頼から反映', { number: change.version }), color: 'success' as const }
    case 'rolled_back':
      return {
        label: t('第{number}版として切戻し・次の依頼から反映', { number: change.version }),
        color: 'success' as const,
      }
    case 'created':
      return { label: t('作成（未公開）'), color: 'warning' as const }
    default:
      return { label: t('変更案を保存（未公開）'), color: 'warning' as const }
  }
}

const items = computed(() =>
  props.changes.map((change) => ({ change, state: stateOf(change), lines: skillChangeLines(change.summary, t) })),
)
</script>

<template>
  <ul class="grid gap-2">
    <li v-for="{ change, state, lines } in items" :key="change.skill">
      <UCard :ui="{ body: 'grid gap-1 p-3 sm:p-3' }">
        <div class="flex flex-wrap items-center gap-2">
          <UIcon name="i-lucide-sparkles" class="size-4 text-primary" />
          <ULink :to="`/skills/${change.skill}`" class="font-medium">{{ change.name }}</ULink>
          <span class="text-xs text-muted">{{ change.scope }}</span>
          <UBadge :color="state.color" variant="subtle" size="sm">{{ state.label }}</UBadge>
        </div>
        <ul v-if="lines.length" class="grid gap-0.5 text-xs text-muted">
          <li v-for="line in lines" :key="line">{{ line }}</li>
        </ul>
        <p v-if="change.state === 'draft_saved' || change.state === 'created'" class="text-xs text-muted">
          {{ $t('公開されるまで、依頼には使われません。内容はSkillの画面で確かめられます。') }}
        </p>
      </UCard>
    </li>
  </ul>
</template>
