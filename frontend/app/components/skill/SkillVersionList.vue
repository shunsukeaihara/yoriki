<script setup lang="ts">
/** 公開した版の履歴。新しい版から順に並べる。中身を開く、過去の版に戻す、過去の版から編集を始める */
const props = defineProps<{ skill: Skill }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const emit = defineEmits<{ rolledBack: [value: SkillVersion]; openDraft: [number: number]; failed: [error: unknown] }>()
const api = useAPI()
const toast = useToast()

const { items: versions, loading, fetchAll } = useFetchAll(() => api.skills(props.skill.id).versions().list())
const load = () => fetchAll().catch((err) => toast.add({ title: makeErrorMsg(err), color: 'error' }))
watch(reloadKey, load, { immediate: true })

// 開いた版の中身。版は不変なので、一度読んだものを使い回す
const opened = ref<number | null>(null)
const contents = ref<Record<number, SkillVersion>>({})
const toggle = async (number: number) => {
  if (opened.value === number) {
    opened.value = null
    return
  }
  opened.value = number
  if (contents.value[number]) return
  try {
    contents.value = { ...contents.value, [number]: await api.skills(props.skill.id).versions(number).get() }
  } catch (err) {
    toast.add({ title: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <div class="grid gap-3">
    <p v-if="loading && !versions.length" class="text-sm text-muted">{{ $t('読み込み中') }}</p>
    <p v-else-if="!versions.length" class="text-sm text-muted">{{ $t('まだ公開していません') }}</p>
    <UCard v-for="v in versions" :key="v.id" :ui="{ body: 'p-3 sm:p-3' }">
      <div class="flex flex-wrap items-center gap-2">
        <span class="font-semibold">{{ $t('版 {number}', { number: v.number }) }}</span>
        <UBadge v-if="v.number === skill.version" color="primary" variant="subtle" size="sm">{{ $t('公開中') }}</UBadge>
        <UBadge v-if="v.rolledBackFrom" color="neutral" variant="subtle" size="sm">
          {{ $t('版 {number} に戻した', { number: v.rolledBackFrom }) }}
        </UBadge>
        <span class="text-xs text-muted">
          {{ formatDatetime(v.created) }}・{{ v.createdByInfo.displayName || v.createdByInfo.email }}
        </span>
        <div class="flex-1" />
        <DBtn
          size="sm"
          color="neutral"
          variant="ghost"
          :icon="opened === v.number ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'"
          @click="toggle(v.number)"
        >
          {{ $t('中身') }}
        </DBtn>
        <!-- 編集・切戻しはサーバー側でも権限を確認し、権限がなければ403になる -->
        <DBtn
          v-if="skill.canEdit && !skill.draft"
          size="sm"
          color="neutral"
          variant="ghost"
          icon="i-lucide-pencil"
          @click="emit('openDraft', v.number)"
        >
          {{ $t('この版から編集') }}
        </DBtn>
        <SkillRollbackDialog
          v-if="skill.canPublish && v.number !== skill.version"
          :skill="skill"
          :number="v.number"
          @rolled-back="(value) => emit('rolledBack', value)"
          @failed="(err) => emit('failed', err)"
        >
          <template #trigger="{ on }">
            <DBtn size="sm" color="neutral" variant="ghost" icon="i-lucide-undo-2" v-on="on">
              {{ $t('この版に戻す') }}
            </DBtn>
          </template>
        </SkillRollbackDialog>
      </div>
      <p v-if="v.changeReason" class="mt-1 whitespace-pre-wrap text-sm">{{ v.changeReason }}</p>
      <div v-if="opened === v.number" class="mt-3 border-t border-default pt-3">
        <SkillContentView v-if="contents[v.number]" :skill-id="skill.id" :version="contents[v.number]!" />
        <p v-else class="text-sm text-muted">{{ $t('読み込み中') }}</p>
      </div>
    </UCard>
  </div>
</template>
