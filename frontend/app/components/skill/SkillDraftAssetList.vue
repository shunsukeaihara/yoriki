<script setup lang="ts">
/**
 * Draftの資料の一覧。追加・変更（ファイルの置換）・外す。
 * 操作が通れば Draft の revision はちょうど1つ増える（サーバーが編集を始めた時点の revision を確かめている）ので、
 * v-model の Draft の revision と資料だけを書き換え、入力中の本文は残す
 */
const props = defineProps<{ skillId: string }>()
const draft = defineModel<SkillDraft>({ required: true })
const emit = defineEmits<{ failed: [error: unknown] }>()
const api = useAPI()
const toast = useToast()
const confirm = useConfirmDialog()
const { t } = useI18n()

const replaceAsset = (asset: SkillDraftAsset) => {
  const others = draft.value.assets.filter((a) => a.key !== asset.key)
  draft.value = {
    ...draft.value,
    revision: asset.draftRevision,
    assets: [...others, asset].sort((a, b) => a.key.localeCompare(b.key)),
  }
}

const onRemove = async (asset: SkillDraftAsset) => {
  const ok = await confirm({
    title: t('資料「{key}」をDraftから外しますか？', { key: asset.key }),
    description: t('公開済みの版はこの資料を使い続けます。原本は残ります'),
    confirmLabel: t('外す'),
    color: 'error',
  })
  if (!ok) return
  try {
    await api.skills(props.skillId).drafts(draft.value.id).assets(asset.key).delete(draft.value.revision)
    draft.value = {
      ...draft.value,
      revision: draft.value.revision + 1,
      assets: draft.value.assets.filter((a) => a.key !== asset.key),
    }
    toast.add({ title: t('外しました'), color: 'success' })
  } catch (err) {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  }
}
</script>

<template>
  <DFormSection
    :title="$t('資料')"
    :description="$t('マスター・テンプレート等。実行するときに、公開した版の原本をSandboxに読取り専用で置きます')"
  >
    <div class="grid gap-2">
      <p v-if="!draft.assets.length" class="text-sm text-muted">{{ $t('資料はありません') }}</p>
      <div
        v-for="asset in draft.assets"
        :key="asset.key"
        class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-default px-3 py-2"
      >
        <div class="grid min-w-0 flex-1 gap-0.5">
          <div class="flex items-center gap-2">
            <span class="font-mono text-sm font-semibold">{{ asset.key }}</span>
            <UBadge color="neutral" variant="subtle" size="sm">{{ asset.roleDisplay }}</UBadge>
          </div>
          <p v-if="asset.description" class="text-xs text-muted">{{ asset.description }}</p>
          <SkillFileLink :skill-id="skillId" :file="asset.fileInfo" />
        </div>
        <div class="flex gap-1">
          <SkillAssetDialog
            :skill-id="skillId"
            :draft-id="draft.id"
            :revision="draft.revision"
            :asset="asset"
            @saved="replaceAsset"
            @failed="(err) => emit('failed', err)"
          >
            <template #trigger="{ on }">
              <DBtn size="sm" color="neutral" variant="ghost" icon="i-lucide-replace" v-on="on">
                {{ $t('変更・置換') }}
              </DBtn>
            </template>
          </SkillAssetDialog>
          <DBtn
            size="sm"
            color="error"
            variant="ghost"
            icon="i-lucide-trash-2"
            :aria-label="$t('外す')"
            @click="onRemove(asset)"
          />
        </div>
      </div>
      <div>
        <SkillAssetDialog
          :skill-id="skillId"
          :draft-id="draft.id"
          :revision="draft.revision"
          @saved="replaceAsset"
          @failed="(err) => emit('failed', err)"
        >
          <template #trigger="{ on }">
            <DBtn icon="i-lucide-plus" color="neutral" variant="outline" v-on="on">{{ $t('資料を追加') }}</DBtn>
          </template>
        </SkillAssetDialog>
      </div>
    </div>
  </DFormSection>
</template>
