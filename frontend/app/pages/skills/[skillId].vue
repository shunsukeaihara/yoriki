<script setup lang="ts">
/**
 * Skillの詳細。公開中の版の中身、Draftの編集と資料・公開、版の履歴と切戻し、変更の記録。
 * 何ができるかは Skill.access で決まり（design.md §3.6）、ボタンを隠すだけで、サーバーでも必ず確かめる（403）
 */
import { AxiosError } from 'axios'
import SkillDraftForm from '~/components/skill/SkillDraftForm.vue'

const route = useRoute()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()
const skillId = route.params.skillId as string

const { data: skill, error, refresh } = await useAsyncData(`skill-${skillId}`, () => api.skills(skillId).get())
if (error.value) throw createError(error.value) // 404 / 403 はエラー画面にする

const canEdit = computed(() => (skill.value?.access ?? 0) >= SkillAccess.EDIT)
const canPublish = computed(() => (skill.value?.access ?? 0) >= SkillAccess.PUBLISH)

// 公開中の版の中身
const current = ref<SkillVersion | null>(null)
const loadCurrent = async () => {
  const number = skill.value?.version ?? 0
  current.value = number ? await api.skills(skillId).versions(number).get() : null
}
// 開いているDraft。Draftを編集できる人にだけ Skill.draft が入る
const draft = ref<SkillDraft | null>(null)
const loadDraft = async () => {
  const draftId = skill.value?.draft
  draft.value = draftId ? await api.skills(skillId).drafts(draftId).get() : null
}
await Promise.all([loadCurrent(), loadDraft()])

const tab = ref(draft.value ? 'draft' : 'current')
const tabs = computed(() => [
  { label: t('公開中の版'), value: 'current', slot: 'current' as const },
  ...(canEdit.value ? [{ label: t('編集中のDraft'), value: 'draft', slot: 'draft' as const }] : []),
  { label: t('版の履歴'), value: 'versions', slot: 'versions' as const },
  { label: t('変更の記録'), value: 'changes', slot: 'changes' as const },
])
const versionsReloadKey = ref(0)
const changesReloadKey = ref(0)

const formRef = ref<InstanceType<typeof SkillDraftForm> | null>(null)
// 編集の間に他の人がDraftを変えた・公開した（409・404）。最新の状態を読み込んでから編集し直す
const conflicted = ref(false)
const onFailed = (err: unknown) => {
  const status = err instanceof AxiosError ? err.response?.status : undefined
  conflicted.value = status === 409 || status === 404
}
// Draftを開いた後に別の版が公開されていたら、このDraftは公開できない（サーバーも409で断る）
const staleDraft = computed(() => Boolean(draft.value) && (draft.value!.baseVersion ?? 0) !== skill.value?.version)

const reloadAll = async () => {
  try {
    await refresh()
    await Promise.all([loadCurrent(), loadDraft()])
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
  await nextTick()
  formRef.value?.reset(draft.value)
  conflicted.value = false
  versionsReloadKey.value = Date.now()
  changesReloadKey.value = Date.now()
}
const reloadLatest = async () => {
  if (formRef.value?.dirty) {
    const ok = await confirm({
      title: t('保存していない変更を捨てて、最新の状態を読み込みますか？'),
      confirmLabel: t('読み込む'),
      color: 'error',
    })
    if (!ok) return
  }
  await reloadAll()
}

// Draftを開く。版を指定しなければ公開中の版から（未公開なら空で）
const opening = ref(false)
const openDraft = async (baseVersion?: number) => {
  opening.value = true
  try {
    await api
      .skills(skillId)
      .drafts()
      .create(baseVersion ? { baseVersion } : {})
    await reloadAll()
    tab.value = 'draft'
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
    await reloadAll()
  } finally {
    opening.value = false
  }
}

const onDiscard = async () => {
  if (!draft.value) return
  const ok = await confirm({
    title: t('Draftを破棄しますか？'),
    description: t('保存した変更と、Draftで追加・置換した資料が公開されずに消えます。公開済みの版は変わりません'),
    confirmLabel: t('破棄'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.skills(skillId).drafts(draft.value.id).delete(draft.value.revision)
    toast.add({ title: t('破棄しました'), color: 'success' })
    await reloadAll()
  } catch (err) {
    onFailed(err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}

const onPublished = async () => {
  await reloadAll()
  tab.value = 'current'
}
const onRolledBack = async () => {
  await reloadAll()
}
const onSkillUpdated = (value: Skill) => {
  skill.value = value
  changesReloadKey.value = Date.now()
}

const onDelete = async () => {
  const ok = await confirm({
    title: t('Skillを削除しますか？'),
    description: t('誰も使えなくなります。始まっている実行は、固定した版を使い続けます'),
    confirmLabel: t('削除'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.skills(skillId).delete()
    toast.add({ title: t('削除しました'), color: 'success' })
    await navigateTo('/skills')
  } catch (err) {
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <UDashboardPanel :id="`skill-${skillId}`">
    <template #header>
      <UDashboardNavbar :title="skill?.name">
        <template #leading>
          <DBtn to="/skills" color="neutral" variant="ghost" icon="i-lucide-arrow-left" :aria-label="$t('一覧へ')" />
        </template>
        <template #right>
          <!-- 名前の変更・停止・削除は公開の権限が要る。サーバー側でも確認し、権限がなければ403になる -->
          <template v-if="skill && canPublish">
            <SkillRenameDialog :skill="skill" @updated="onSkillUpdated">
              <template #trigger="{ on }">
                <DBtn color="neutral" variant="ghost" icon="i-lucide-pencil" :aria-label="$t('名前を変更')" v-on="on">
                  <span class="hidden sm:inline">{{ $t('名前を変更') }}</span>
                </DBtn>
              </template>
            </SkillRenameDialog>
            <SkillSuspendDialog v-if="skill.version || skill.suspended" :skill="skill" @updated="onSkillUpdated">
              <template #trigger="{ on }">
                <DBtn
                  :color="skill.suspended ? 'primary' : 'error'"
                  variant="ghost"
                  :icon="skill.suspended ? 'i-lucide-play' : 'i-lucide-octagon-pause'"
                  :aria-label="skill.suspended ? $t('再開') : $t('停止')"
                  v-on="on"
                >
                  <span class="hidden sm:inline">{{ skill.suspended ? $t('再開') : $t('停止') }}</span>
                </DBtn>
              </template>
            </SkillSuspendDialog>
            <DBtn color="error" variant="ghost" icon="i-lucide-trash-2" :aria-label="$t('削除')" @click="onDelete">
              <span class="hidden sm:inline">{{ $t('削除') }}</span>
            </DBtn>
          </template>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UContainer v-if="skill" class="grid max-w-4xl min-w-0 gap-4">
        <div class="flex flex-wrap items-center gap-2 text-sm">
          <UBadge color="neutral" variant="subtle">{{ skill.scopeDisplay }}</UBadge>
          <span v-if="skill.scope !== SkillScope.COMPANY" class="text-muted">{{ skillOwnerLabel(skill, t) }}</span>
          <UBadge v-if="skill.version" color="primary" variant="subtle">
            {{ $t('版 {number} を公開中', { number: skill.version }) }}
          </UBadge>
          <UBadge v-else color="neutral" variant="subtle">{{ $t('未公開') }}</UBadge>
        </div>
        <UAlert
          v-if="skill.suspended"
          color="error"
          variant="subtle"
          icon="i-lucide-octagon-pause"
          :title="$t('停止中です。新しい実行でも、進行中の実行の次の呼出しでも使いません')"
        />

        <UTabs
          v-model="tab"
          :items="tabs"
          variant="link"
          :unmount-on-hide="false"
          :ui="{ list: 'overflow-x-auto', trigger: 'shrink-0', root: 'min-w-0' }"
        >
          <template #current>
            <div class="pt-4">
              <SkillContentView v-if="current" :skill-id="skillId" :version="current" />
              <p v-else class="text-sm text-muted">
                {{ $t('まだ公開していません。Draftを編集して公開すると、使えるようになります') }}
              </p>
            </div>
          </template>

          <template #draft>
            <div class="grid gap-4 pt-4">
              <template v-if="draft">
                <UAlert
                  v-if="staleDraft"
                  color="warning"
                  variant="subtle"
                  icon="i-lucide-triangle-alert"
                  :title="
                    $t(
                      'このDraftを開いた後に、版 {number} が公開されました。このDraftは公開できません。破棄して開き直してください',
                      { number: skill.version },
                    )
                  "
                />
                <p class="text-xs text-muted">
                  {{
                    $t('版 {base} から編集中・最後の保存 {updated}（{user}）', {
                      base: draft.baseVersion ?? 0,
                      updated: formatDatetime(draft.updated),
                      user: draft.updatedByInfo.displayName || draft.updatedByInfo.email,
                    })
                  }}
                </p>
                <SkillDraftForm
                  ref="formRef"
                  v-model="draft"
                  :skill-id="skillId"
                  :can-change-tools="canPublish"
                  @failed="onFailed"
                />
                <SkillDraftAssetList v-model="draft" :skill-id="skillId" @failed="onFailed" />
                <UAlert
                  v-if="conflicted"
                  color="warning"
                  variant="subtle"
                  icon="i-lucide-refresh-cw"
                  :title="$t('編集の間に、他の人がDraftを変えたか公開しました')"
                  :actions="[
                    { label: $t('最新の状態を読み込む'), color: 'warning', variant: 'outline', onClick: reloadLatest },
                  ]"
                />
                <div class="flex flex-wrap items-center gap-2 border-t border-default pt-4">
                  <DBtn color="error" variant="ghost" icon="i-lucide-trash-2" @click="onDiscard">
                    {{ $t('Draftを破棄') }}
                  </DBtn>
                  <div class="flex-1" />
                  <span v-if="formRef?.dirty" class="text-xs text-warning">{{
                    $t('保存していない変更があります')
                  }}</span>
                  <DBtn
                    :disabled="!formRef?.canPost"
                    :loading="formRef?.loading"
                    variant="outline"
                    @click="formRef?.onSubmit()"
                  >
                    {{ $t('保存') }}
                  </DBtn>
                  <SkillPublishDialog
                    v-if="canPublish"
                    :skill="skill"
                    :draft="draft"
                    @published="onPublished"
                    @failed="onFailed"
                  >
                    <template #trigger="{ on }">
                      <DBtn icon="i-lucide-upload" :disabled="formRef?.dirty || staleDraft" v-on="on">
                        {{ $t('公開') }}
                      </DBtn>
                    </template>
                  </SkillPublishDialog>
                </div>
                <p v-if="!canPublish" class="text-right text-xs text-muted">
                  {{
                    $t('公開するには、この範囲の公開の権限が要ります。保存したDraftは、公開の権限を持つ人が公開します')
                  }}
                </p>
              </template>
              <div v-else class="grid justify-items-start gap-2">
                <p class="text-sm text-muted">
                  {{
                    skill.version
                      ? $t('編集中のDraftはありません。公開中の版から編集を始めます')
                      : $t('編集中のDraftはありません')
                  }}
                </p>
                <DBtn icon="i-lucide-pencil" :loading="opening" @click="openDraft()">{{ $t('編集を始める') }}</DBtn>
              </div>
            </div>
          </template>

          <template #versions>
            <div class="pt-4">
              <SkillVersionList
                v-model:reload-key="versionsReloadKey"
                :skill="skill"
                @rolled-back="onRolledBack"
                @open-draft="openDraft"
                @failed="onFailed"
              />
            </div>
          </template>

          <template #changes>
            <div class="pt-4">
              <SkillChangeList v-model:reload-key="changesReloadKey" :skill-id="skillId" />
            </div>
          </template>
        </UTabs>
      </UContainer>
    </template>
  </UDashboardPanel>
</template>
