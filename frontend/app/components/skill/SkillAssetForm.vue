<script setup lang="ts">
/**
 * Draftの資料の追加と変更。原本のbytesをそのまま保存する。変更でファイルを選ぶと、同じ資料（keyは同じ）の新しい版で
 * 置き換える。公開済みの版は前の原本を使い続ける（design.md §3.10）
 */
const props = defineProps<{ skillId: string; draftId: string; revision: number }>()
const api = useAPI()
const toast = useToast()
const { t } = useI18n()
const modelValue = defineModel<SkillDraftAsset | null>()
const emit = defineEmits<{
  created: [value: SkillDraftAsset]
  updated: [value: SkillDraftAsset]
  failed: [error: unknown]
}>()

const emptyForm = () => ({
  key: null as string | null,
  newKey: '',
  role: SkillAssetRole.REFERENCE as SkillAssetRoleEnum,
  description: '',
  file: null as File | File[] | null,
})
const form = ref(emptyForm())

watch(
  modelValue,
  (value) => {
    form.value = value
      ? { key: value.key, newKey: value.key, role: value.role, description: value.description ?? '', file: null }
      : emptyForm()
  },
  { immediate: true },
)

const selectedFile = computed(() => (Array.isArray(form.value.file) ? form.value.file[0] : form.value.file) ?? null)

const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ key, newKey, role, description }) => {
    const draft = api.skills(props.skillId).drafts(props.draftId)
    return key
      ? draft.assets(key).patch({ role, description, file: selectedFile.value, revision: props.revision })
      : draft
          .assets()
          .create({ key: newKey.trim(), role, description, file: selectedFile.value!, revision: props.revision })
  },
  onSuccess: (result) => {
    if (form.value.key) emit('updated', result)
    else emit('created', result)
    modelValue.value = result
    toast.add({ title: t('保存しました'), color: 'success' })
  },
  onError: (err) => {
    emit('failed', err)
    toast.add({ title: t('エラー'), description: makeErrorMsg(err), color: 'error' })
  },
})

// 追加ではkeyとファイルが要る
const canPost = computed(() => Boolean(form.value.key || (form.value.newKey.trim() && selectedFile.value)))

defineExpose({ onSubmit, loading, canPost })
</script>

<template>
  <DFormStack>
    <DFormAlert :errors="errors" />
    <DTextField
      v-if="!form.key"
      v-model="form.newKey"
      :label="$t('key')"
      required
      :hint="
        $t('Skillの中で資料を指す名前です。英小文字・数字・-・_ で書き、後から変えられません（例：{example}）', {
          example: 'vendor-master',
        })
      "
      :error-messages="errors.key"
    />
    <DRadioGroup
      v-model="form.role"
      :label="$t('用途')"
      :items="skillAssetRoleItems(t)"
      variant="card"
      :error-messages="errors.role"
    />
    <DTextField
      v-model="form.description"
      :label="$t('説明')"
      :hint="$t('エージェントがどの資料を使うか判断するのに使います')"
      :error-messages="errors.description"
      class="max-w-none"
    />
    <DFileInputField
      v-model="form.file"
      :label="form.key ? $t('置き換えるファイル') : $t('ファイル')"
      :required="!form.key"
      :hint="form.key ? $t('選んだときだけ、新しい版で置き換えます') : undefined"
      :preview="false"
      :error-messages="errors.file"
    />
  </DFormStack>
</template>
