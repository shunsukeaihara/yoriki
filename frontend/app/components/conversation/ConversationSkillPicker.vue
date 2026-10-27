<script setup lang="ts">
/**
 * 入力欄の `/` の一覧（design.md §11.2「Skill の指定」、§11.6）。ボタンか、入力欄で `/` を打つと開き、名前と説明で
 * 絞り込める。使う Skill（公開中で停止していないもの）を選ぶほか、編集できる Skill の「編集」と「新しい Skill を作る」
 * を選ぶと、そのメッセージは Skill の編集の依頼になる（別の Run で行う）。
 * 機密の Skill は個人情報・機密情報を扱う会話でだけ、宣言した Connector を使えない Skill は使えないので、理由を添えて
 * 選べない項目として出す（design.md §4冒頭・§4.9.1）。
 */
const props = defineProps<{
  /** 使える Skill */
  skills: Skill[]
  /** 編集できる Skill */
  editable: Skill[]
  /** 使う Skill として選んでいる ID（一覧から外す） */
  selected: string[]
  /** 個人情報・機密情報を扱う会話 */
  sensitive: boolean
  /** 使う Skill をこれ以上選べない */
  full?: boolean
  disabled?: boolean
}>()
const open = defineModel<boolean>('open', { default: false })
const searchTerm = defineModel<string>('searchTerm', { default: '' })
const emit = defineEmits<{ select: [skill: Skill]; edit: [skill: Skill]; create: [] }>()
const { t } = useI18n()

const scopeLabel = (skill: Skill) => {
  const scope = skill.departmentInfo ? `${skill.scopeDisplay} ${skill.departmentInfo.name}` : skill.scopeDisplay
  return skill.sensitive ? `${scope}・${t('機密')}` : scope
}
/** 使えない理由。使えれば null */
const unusableReason = (skill: Skill) => {
  if (skill.sensitive && !props.sensitive) return t('機密のSkillは、個人情報・機密情報を扱う会話でだけ使えます')
  if (!skill.connectorsUsable) return t('このSkillが使うConnectorを使えません')
  return null
}

const choose = (action: () => void) => () => {
  action()
  open.value = false
  searchTerm.value = ''
}

const groups = computed(() => [
  {
    id: 'use',
    label: t('使う'),
    items: props.full
      ? []
      : props.skills
          .filter((s) => !props.selected.includes(s.id))
          .map((skill) => {
            const reason = unusableReason(skill)
            return {
              id: `use-${skill.id}`,
              label: skill.name,
              suffix: scopeLabel(skill),
              description: reason ?? skill.description ?? undefined,
              icon: skill.sensitive ? 'i-lucide-shield' : 'i-lucide-sparkles',
              disabled: Boolean(reason),
              onSelect: choose(() => emit('select', skill)),
            }
          }),
  },
  {
    id: 'edit',
    label: t('編集する（今後の依頼に反映）'),
    items: [
      ...props.editable.map((skill) => {
        // 機密の Skill は、機密の会話でだけ編集する（harness.md §10.9。Draft だけが機密のものはサーバーが断る）
        const blocked = skill.sensitive && !props.sensitive
        return {
          id: `edit-${skill.id}`,
          label: skill.name,
          suffix: scopeLabel(skill),
          description: blocked
            ? t('機密のSkillは、個人情報・機密情報を扱う会話で編集します')
            : skill.version
              ? t('第{number}版を元に編集', { number: skill.version })
              : t('未公開'),
          icon: 'i-lucide-pencil',
          disabled: blocked,
          onSelect: choose(() => emit('edit', skill)),
        }
      }),
      {
        id: 'create',
        label: t('新しいSkillを作る'),
        icon: 'i-lucide-plus',
        onSelect: choose(() => emit('create')),
      },
    ],
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
        :empty="t('該当するSkillがありません')"
        class="max-h-80 w-[min(28rem,calc(100vw-2rem))]"
      />
    </template>
  </UPopover>
</template>
