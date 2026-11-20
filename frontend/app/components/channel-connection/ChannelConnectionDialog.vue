<script setup lang="ts">
import ChannelConnectionForm from './ChannelConnectionForm.vue'
import DDialog from '../common/DDialog.vue'

const emit = defineEmits<{ saved: [value: ChannelConnection] }>()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof ChannelConnectionForm> | null>(null)
const editing = ref<ChannelConnection | null>(null)
// 作成した後は、Slackのアプリに設定する受け口のURLを表示してから閉じる
const created = ref<ChannelConnection | null>(null)

// 開くたびに空のフォームにし、前回の入力やエラーを残さない
const onOpen = (open: boolean) => {
  if (open) {
    editing.value = null
    created.value = null
  }
}

const onCreated = (value: ChannelConnection) => {
  emit('saved', value)
  created.value = value
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
    <template #header>{{ created ? $t('連携の接続を追加しました') : $t('連携の接続を追加') }}</template>
    <template #content>
      <div v-if="created" class="grid gap-3">
        <p class="text-sm">
          {{ $t('「{workspace}」につながりました。', { workspace: created.workspaceName }) }}
        </p>
        <ChannelConnectionReceiveUrl :url="created.receiveUrl" :extension="created.extension" />
      </div>
      <ChannelConnectionForm v-else ref="formRef" v-model="editing" @created="onCreated" />
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
