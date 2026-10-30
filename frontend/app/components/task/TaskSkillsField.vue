<script setup lang="ts">
/**
 * タスクに登録するSkillと固定の版（design.md §4.1）。候補は、タスクの範囲に登録できる公開中のSkill（部門のタスクは
 * その部門と全社、全社は全社、システムはシステム。authorization.md §5.4）。版はSkillが新しい版を公開しても変わらず、
 * 次にタスクを保存し直すまで固定する。登録できるかは保存のときにサーバーが確かめる
 */
const props = withDefaults(
  defineProps<{
    label: string
    hint?: string
    choices: readonly TaskSkillChoice[]
    /** 保存済みの登録の名前（候補にないSkillの名前を出すため） */
    info: readonly TaskReference[]
    max?: number
    errorMessages?: string[]
  }>(),
  { hint: undefined, max: MAX_TASK_SKILLS, errorMessages: undefined },
)
const modelValue = defineModel<TaskSkill[]>({ required: true })
const { t } = useI18n()

const choiceOf = (id: string) => props.choices.find((c) => c.id === id)
const nameOf = (id: string) =>
  choiceOf(id)?.name ?? props.info.find((i) => i.id === id)?.name ?? t('（削除されたSkill）')
// 候補にない（停止・削除・範囲の外になった）Skill。保存のときにサーバーが断る
const unavailable = (id: string) => !choiceOf(id)
const versionItems = (entry: TaskSkill) => {
  const choice = choiceOf(entry.skill)
  const numbers = choice ? choice.versions : [entry.version]
  return numbers.map((n) => ({
    value: n,
    title: n === choice?.version ? t('版 {number}（公開中）', { number: n }) : t('版 {number}', { number: n }),
  }))
}
const isOld = (entry: TaskSkill) => {
  const choice = choiceOf(entry.skill)
  return Boolean(choice) && entry.version !== choice!.version
}

const addItems = computed(() =>
  props.choices
    .filter((c) => !modelValue.value.some((e) => e.skill === c.id))
    .map((c) => ({
      value: c.id,
      title: c.scope === TaskScope.DEPARTMENT ? `${c.name}（${c.departmentInfo?.name ?? ''}）` : c.name,
      description: c.description,
    })),
)
const adding = ref<string | null>(null)
watch(adding, (id) => {
  if (!id) return
  const choice = choiceOf(id)
  // 並びは入れ替えて渡す（親が computed の setter で受け取っても変更が伝わるように、その場で書き換えない）
  if (choice) modelValue.value = [...modelValue.value, { skill: id, version: choice.version }]
  nextTick(() => (adding.value = null))
})
const remove = (index: number) => (modelValue.value = modelValue.value.filter((_, i) => i !== index))
</script>

<template>
  <DFormField :label="label" :hint="hint" :error-messages="errorMessages">
    <div class="grid gap-2">
      <div
        v-for="(entry, index) in modelValue"
        :key="entry.skill"
        class="flex flex-wrap items-center gap-2 rounded-md border border-default px-3 py-2"
      >
        <span class="min-w-0 flex-1 truncate font-medium">{{ nameOf(entry.skill) }}</span>
        <UBadge v-if="unavailable(entry.skill)" color="error" variant="subtle" size="sm">
          {{ $t('このタスクでは使えません') }}
        </UBadge>
        <UBadge v-else-if="isOld(entry)" color="warning" variant="subtle" size="sm">
          {{ $t('新しい版があります') }}
        </UBadge>
        <DSelect
          v-model="entry.version"
          :label="$t('版')"
          :show-label="false"
          :items="versionItems(entry)"
          class="w-44"
        />
        <DBtn color="error" variant="ghost" icon="i-lucide-x" :aria-label="$t('外す')" @click="remove(index)" />
      </div>
      <DSelect
        v-if="modelValue.length < max"
        v-model="adding"
        :label="$t('Skillを足す')"
        :show-label="false"
        :placeholder="addItems.length ? $t('Skillを足す') : $t('足せるSkillはありません')"
        :items="addItems"
        :disabled="!addItems.length"
        searchable
      />
    </div>
  </DFormField>
</template>
