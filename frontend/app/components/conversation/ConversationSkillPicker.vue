<script setup lang="ts">
/**
 * 入力欄で使う Skill を選ぶ一覧（design.md §11.2「Skill の指定」）。ボタンか、入力欄で `/` を打つと開き、
 * 名前と説明で絞り込める。選べるのは利用者が使える（公開中で停止していない）Skill だけ。
 */
const props = defineProps<{
  skills: Skill[]
  /** 選んでいる Skill の ID（一覧から外す） */
  selected: string[]
  disabled?: boolean
}>()
const open = defineModel<boolean>('open', { default: false })
const searchTerm = defineModel<string>('searchTerm', { default: '' })
const emit = defineEmits<{ select: [skill: Skill] }>()
const { t } = useI18n()

const groups = computed(() => [
  {
    id: 'skills',
    items: props.skills
      .filter((s) => !props.selected.includes(s.id))
      .map((skill) => ({
        id: skill.id,
        label: skill.name,
        suffix: skill.departmentInfo ? `${skill.scopeDisplay} ${skill.departmentInfo.name}` : skill.scopeDisplay,
        description: skill.description ?? undefined,
        icon: 'i-lucide-sparkles',
        onSelect: () => {
          emit('select', skill)
          open.value = false
          searchTerm.value = ''
        },
      })),
  },
])
</script>

<template>
  <UPopover v-model:open="open" :content="{ side: 'top', align: 'start' }">
    <DBtn
      icon="i-lucide-sparkles"
      variant="ghost"
      color="neutral"
      size="sm"
      :disabled="disabled"
      :aria-label="$t('Skillを指定')"
    />
    <template #content>
      <UCommandPalette
        v-model:search-term="searchTerm"
        :groups="groups"
        :fuse="{ fuseOptions: { keys: ['label', 'description', 'suffix'] } }"
        :placeholder="t('Skillを検索')"
        :empty="t('使えるSkillがありません')"
        class="max-h-80 w-[min(28rem,calc(100vw-2rem))]"
      />
    </template>
  </UPopover>
</template>
