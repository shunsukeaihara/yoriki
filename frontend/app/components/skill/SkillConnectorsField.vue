<script setup lang="ts">
/**
 * Skill が使う Connector と操作・表の宣言（design.md §4.9.1）。候補は、編集する人が使える Connector のうち、Skill の
 * 範囲で宣言できるもの（部門の Skill はその部門か全社、全社の Skill は全社、システムの Skill はシステム・部門・全社。
 * authorization.md §5.4）。システムの Connector は、見る権限を持つ人にだけ返る。
 * 表は Connector の公開する表から選び、選ばなければ公開する表のどれでも使う。宣言は要件で、宣言した Connector を
 * 使えない人には、その Skill は出ない。範囲・境界・Tool は公開するときにサーバーが確かめる。
 */
const props = defineProps<{
  /** 宣言する Skill の範囲と部門 */
  skill?: Pick<Skill, 'scope' | 'departmentInfo'>
  /** 保存済みの宣言の名前（候補にない Connector の名前を出すため） */
  info: readonly DeclaredConnector[]
  errorMessages?: string[]
}>()
const modelValue = defineModel<ConnectorDeclaration[]>({ required: true })
const api = useAPI()
const { t } = useI18n()

const isSystem = computed(() => props.skill?.scope === SkillScope.SYSTEM)
const { items: usable, fetchAll } = useFetchAll(() => api.connectors.list({ pageSize: 200 }))
const { items: system, fetchAll: fetchSystem } = useFetchAll(() =>
  api.connectors.list({ pageSize: 200, scope: SkillScope.SYSTEM }),
)
/** Skill の範囲で宣言できるか（サーバーも公開するときに確かめる） */
const fits = (connector: AppConnector) => {
  switch (props.skill?.scope) {
    case SkillScope.DEPARTMENT:
      return connector.scope === SkillScope.COMPANY || connector.departmentInfo?.id === props.skill.departmentInfo?.id
    case SkillScope.COMPANY:
      return connector.scope === SkillScope.COMPANY
    default:
      return true
  }
}
// 宣言した Connector の公開する表（使えない Connector は null）
const details = ref<Record<string, AppConnectorDetail | null>>({})
const loadDetail = async (id: string) => {
  if (id in details.value) return
  details.value[id] = null
  try {
    details.value[id] = await api.connectors.get(id)
  } catch {
    // 使えない（所属を外れた・無効にされた）Connector。名前は保存済みの宣言から出す
  }
}
onMounted(async () => {
  await Promise.all([fetchAll(), isSystem.value ? fetchSystem() : null])
  await Promise.all(modelValue.value.map((d) => loadDetail(d.connector)))
})
watch(
  () => modelValue.value.map((d) => d.connector),
  (ids) => ids.forEach((id) => loadDetail(id)),
)

const nameOf = (id: string) =>
  details.value[id]?.name ??
  usable.value.find((c) => c.id === id)?.name ??
  props.info.find((i) => i.connector === id)?.name ??
  t('（削除されたConnector）')
const isSensitive = (id: string) =>
  details.value[id]?.isSensitive ?? props.info.find((i) => i.connector === id)?.isSensitive ?? false

const operationItems = computed(() =>
  skillToolItems(t).filter((i) => i.value === 'datasource_describe' || i.value === 'datasource_query'),
)
const tableItems = (id: string) =>
  (details.value[id]?.tables ?? []).map((table) => ({ value: table.name, title: table.name, description: table.description }))

const choices = computed(() =>
  [...(isSystem.value ? system.value : []), ...usable.value]
    .filter((c) => fits(c) && !modelValue.value.some((d) => d.connector === c.id))
    .map((c) => ({ value: c.id, title: c.isSensitive ? `${c.name}（${t('機密')}）` : c.name, description: c.description })),
)
const adding = ref<string | null>(null)
const add = (id: string | number | boolean | (string | number | boolean)[] | null | undefined) => {
  if (typeof id !== 'string' || !id) return
  modelValue.value = [...modelValue.value, { connector: id, operations: ['datasource_describe', 'datasource_query'], tables: [] }]
  nextTick(() => (adding.value = null))
}
const update = (index: number, change: Partial<ConnectorDeclaration>) => {
  modelValue.value = modelValue.value.map((d, i) => (i === index ? { ...d, ...change } : d))
}
const setOperations = (index: number, value: string[]) => {
  update(index, { operations: value as ConnectorDeclaration['operations'] })
}
const remove = (index: number) => {
  modelValue.value = modelValue.value.filter((_d, i) => i !== index)
}
</script>

<template>
  <DFormSection
    :title="$t('使うConnector')"
    :description="
      $t('このSkillが読むConnectorと表を宣言します。宣言したConnectorを使えない人には、このSkillは出ません')
    "
  >
    <p v-for="message in errorMessages ?? []" :key="message" class="text-sm text-error">{{ message }}</p>
    <div
      v-for="(declaration, index) in modelValue"
      :key="declaration.connector"
      class="grid gap-3 rounded-md border border-default p-3"
    >
      <div class="flex items-center gap-2">
        <UIcon :name="isSensitive(declaration.connector) ? 'i-lucide-shield' : 'i-lucide-database'" />
        <span class="font-semibold">{{ nameOf(declaration.connector) }}</span>
        <UBadge v-if="isSensitive(declaration.connector)" color="warning" variant="subtle" size="sm">
          {{ $t('機密') }}
        </UBadge>
        <UBadge v-if="details[declaration.connector] === null" color="error" variant="subtle" size="sm">
          {{ $t('使えません') }}
        </UBadge>
        <DBtn
          class="ml-auto"
          size="xs"
          color="neutral"
          variant="ghost"
          icon="i-lucide-x"
          :aria-label="$t('外す')"
          @click="remove(index)"
        />
      </div>
      <DCheckboxGroup
        :model-value="declaration.operations"
        :label="$t('使う操作')"
        :items="operationItems"
        orientation="horizontal"
        @update:model-value="(value: string[]) => setOperations(index, value)"
      />
      <DSelect
        :model-value="declaration.tables ?? []"
        :label="$t('使う表')"
        :hint="$t('選ばなければ、このConnectorが公開する表のどれでも使います')"
        :items="tableItems(declaration.connector)"
        item-description="description"
        multiple
        searchable
        clearable
        @update:model-value="(value) => update(index, { tables: (value ?? []) as string[] })"
      />
    </div>
    <DSelect
      v-if="modelValue.length < 5"
      v-model="adding"
      :label="$t('Connectorを加える')"
      :items="choices"
      item-description="description"
      searchable
      :placeholder="choices.length ? $t('Connectorを選ぶ') : $t('使えるConnectorはありません')"
      :disabled="!choices.length"
      @update:model-value="add"
    />
  </DFormSection>
</template>
