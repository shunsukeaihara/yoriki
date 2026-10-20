<script setup lang="ts">
import ManageUserForm from './ManageUserForm.vue'
import DDialog from '../common/DDialog.vue'

const emit = defineEmits<{ saved: [value: ManageUser] }>()

const dialog = ref<InstanceType<typeof DDialog> | null>(null)
const formRef = ref<InstanceType<typeof ManageUserForm> | null>(null)
const editing = ref<ManageUser | null>(null)
// 作成した後は、招待のリンク（SSO が有効なら案内）を表示してから閉じる
const created = ref<ManageUserCreated | null>(null)

// 開くたびに空のフォームにし、前回の入力やエラーを残さない
const onOpen = (open: boolean) => {
  if (open) {
    editing.value = null
    created.value = null
  }
}

const onCreated = (value: ManageUserCreated) => {
  created.value = value
  emit('saved', value)
}
</script>

<template>
  <DDialog ref="dialog" @update:open="onOpen">
    <template #trigger="{ on }">
      <slot name="trigger" :on="on" />
    </template>
    <template #header>{{ created ? $t('ユーザーを追加しました') : $t('ユーザーを追加') }}</template>
    <template #content>
      <div v-if="created" class="grid gap-3">
        <ManageUserInvitationLink v-if="created.invitation" :link="created.invitation" />
        <UAlert
          v-else
          color="info"
          variant="subtle"
          :description="
            $t(
              'SSOが有効なので、招待のリンクはありません。本人がSSOで初めてログインしたときに、このアカウントに紐付きます',
            )
          "
        />
      </div>
      <ManageUserForm v-else ref="formRef" v-model="editing" @created="onCreated" />
    </template>
    <template #footer="{ closeDialog }">
      <DBtn v-if="created" @click="closeDialog()">{{ $t('閉じる') }}</DBtn>
      <template v-else>
        <DBtn color="neutral" variant="ghost" @click="closeDialog()">{{ $t('キャンセル') }}</DBtn>
        <DBtn :disabled="!formRef?.canPost" :loading="formRef?.loading" @click="formRef?.onSubmit()">
          {{ $t('保存') }}
        </DBtn>
      </template>
    </template>
  </DDialog>
</template>
