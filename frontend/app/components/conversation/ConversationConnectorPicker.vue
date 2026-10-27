<script setup lang="ts">
/**
 * 入力欄の `@` の一覧（harness.md §10.2）。ボタンか、入力欄で `@` を打つと開き、名前と説明で絞り込める。
 * 利用者が会話で使える Connector を選ぶ。機密の Connector は、会話のモデルがローカル LLM のときだけ選べる
 * （選べないものも、理由を添えて一覧に出す）。
 */
const props = defineProps<{
  /** 使える Connector */
  connectors: AppConnector[]
  /** 選んでいる ID（一覧から外す） */
  selected: string[]
  /** 会話のモデルがローカル LLM */
  localModel: boolean
  /** これ以上選べない */
  full?: boolean
  disabled?: boolean
}>()
const open = defineModel<boolean>('open', { default: false })
const searchTerm = defineModel<string>('searchTerm', { default: '' })
const emit = defineEmits<{ select: [connector: AppConnector] }>()
const { t } = useI18n()

const scopeLabel = (connector: AppConnector) =>
  connector.departmentInfo ? `${connector.scopeDisplay} ${connector.departmentInfo.name}` : connector.scopeDisplay

const groups = computed(() => [
  {
    id: 'connectors',
    label: t('Connectorを使う'),
    items: props.full
      ? []
      : props.connectors
          .filter((c) => !props.selected.includes(c.id))
          .map((connector) => {
            const usable = isConnectorUsableWith(connector, props.localModel)
            return {
              id: connector.id,
              label: connector.name,
              suffix: connector.isSensitive ? `${scopeLabel(connector)}・${t('機密')}` : scopeLabel(connector),
              description: usable
                ? connector.description || undefined
                : t('機密のConnectorは、ローカルLLMのモデルの会話でだけ使えます'),
              icon: connector.isSensitive ? 'i-lucide-shield' : 'i-lucide-database',
              disabled: !usable,
              onSelect: () => {
                emit('select', connector)
                open.value = false
                searchTerm.value = ''
              },
            }
          }),
  },
])
</script>

<template>
  <UPopover v-model:open="open" :content="{ side: 'top', align: 'start' }">
    <DBtn
      icon="i-lucide-database"
      variant="ghost"
      color="neutral"
      size="sm"
      :disabled="disabled"
      :aria-label="$t('Connectorを指定')"
    />
    <template #content>
      <UCommandPalette
        v-model:search-term="searchTerm"
        :groups="groups"
        :fuse="{ fuseOptions: { keys: ['label', 'description', 'suffix'] } }"
        :placeholder="t('Connectorを検索')"
        :empty="full ? t('これ以上指定できません') : t('使えるConnectorがありません')"
        class="max-h-80 w-[min(28rem,calc(100vw-2rem))]"
      />
    </template>
  </UPopover>
</template>
