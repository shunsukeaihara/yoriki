<script setup lang="ts">
import DestinationForm from './DestinationForm.vue'
import DDialog from '../common/DDialog.vue'

const emit = defineEmits<{ saved: [value: Destination] }>()
const { t } = useI18n()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof DestinationForm> | null>(null)
const editing = ref<Destination | null>(null)
// 作成した後は、署名の鍵（Webhookだけ。受け手が署名を確かめる）を一度だけ表示してから閉じる
const created = ref<DestinationSecret | null>(null)

// 開くたびに空のフォームにし、前回の入力やエラーを残さない
const onOpen = (open: boolean) => {
  if (open) {
    editing.value = null
    created.value = null
  }
}

const onCreated = (value: DestinationSecret) => {
  emit('saved', value)
  if (value.kind === DestinationKind.WEBHOOK) created.value = value
  else dialog.value?.closeDialog()
}
</script>

<template>
  <!-- 送信中は閉じられない（閉じて開き直すと、もう一度作れてしまう） -->
  <DDialog
    ref="dialog"
    :ui="{ content: 'sm:max-w-2xl' }"
    :dismissible="!formRef?.loading"
    :close="!formRef?.loading"
    @update:open="onOpen"
  >
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ created ? $t('出口を追加しました') : $t('出口を追加') }}</template>
    <template #content>
      <IntegrationIssuedSecret
        v-if="created"
        :label="$t('署名の鍵')"
        :value="created.signingSecret"
        :description="
          t(
            '受け手に設定し、届いた要求の署名（Standard Webhooks）を確かめてもらいます。成果物のファイルを取得するときも、この鍵を Authorization: Bearer で付けます。鍵はこの画面でしか表示できません',
          )
        "
      />
      <DestinationForm v-else ref="formRef" v-model="editing" @created="onCreated" />
    </template>
    <template #footer="{ closeDialog }">
      <DBtn v-if="created" @click="closeDialog()">{{ $t('閉じる') }}</DBtn>
      <template v-else>
        <DBtn color="neutral" variant="ghost" :disabled="formRef?.loading" @click="closeDialog()">
          {{ $t('キャンセル') }}
        </DBtn>
        <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
          {{ $t('保存') }}
        </DBtn>
      </template>
    </template>
  </DDialog>
</template>
