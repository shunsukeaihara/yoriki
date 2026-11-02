<script setup lang="ts">
/**
 * タスクの編集の Run で保存したタスクの版（design.md §11.11）。タスクの名前・範囲・版と、変えた項目の変更の前後を
 * 出し、タスクの画面（版の履歴）へのリンクを置く。新しい版は次の実行から使う。
 */
const props = defineProps<{ changes: RunTaskChange[] }>()
const { t } = useI18n()

const fieldLabel = (change: RunTaskChange['changes'][number]) => {
  switch (change.field) {
    case 'prompt_template':
      return t('依頼文')
    case 'notification_instructions':
      return t('通知の書き方')
    default:
      return t('入力の項目「{name}」の説明', { name: change.input })
  }
}

const items = computed(() =>
  props.changes.map((change) => ({
    change,
    fields: change.changes.map((c, index) => ({ key: `${c.field}-${c.input}-${index}`, label: fieldLabel(c), ...c })),
  })),
)
</script>

<template>
  <ul class="grid gap-2">
    <li v-for="{ change, fields } in items" :key="change.task">
      <UCard :ui="{ body: 'grid gap-2 p-3 sm:p-3' }">
        <div class="flex flex-wrap items-center gap-2">
          <UIcon name="i-lucide-workflow" class="size-4 text-primary" />
          <ULink :to="`/tasks/${change.task}`" class="font-medium">{{ change.name }}</ULink>
          <span class="text-xs text-muted">{{ change.scope }}</span>
          <UBadge color="success" variant="subtle" size="sm">
            {{ $t('第{number}版を保存・次の実行から反映', { number: change.version }) }}
          </UBadge>
        </div>
        <UCollapsible v-for="field in fields" :key="field.key">
          <DBtn variant="link" color="neutral" size="sm" class="px-0" trailing-icon="i-lucide-chevron-down">
            {{ field.label }}
          </DBtn>
          <template #content>
            <div class="mt-1 grid gap-2 text-xs sm:grid-cols-2">
              <div class="grid min-w-0 gap-1">
                <span class="text-muted">{{ $t('変更前') }}</span>
                <p class="max-h-60 overflow-auto rounded-md bg-muted p-2 whitespace-pre-wrap">
                  {{ field.before || $t('（なし）') }}
                </p>
              </div>
              <div class="grid min-w-0 gap-1">
                <span class="text-muted">{{ $t('変更後') }}</span>
                <p class="max-h-60 overflow-auto rounded-md bg-muted p-2 whitespace-pre-wrap">
                  {{ field.after || $t('（なし）') }}
                </p>
              </div>
            </div>
          </template>
        </UCollapsible>
        <p class="text-xs text-muted">
          {{ $t('「もう一度動かす」で、直した定義で動かせます。版の履歴はタスクの画面で確かめられます。') }}
        </p>
      </UCard>
    </li>
  </ul>
</template>
