<script setup lang="ts">
/**
 * サブエージェント（harness.md §10.11）。作業の記録の中で、起動した subagent の呼出しの下に、状態と子の記録を
 * 折り畳んで出す。子に任せた指示は呼出しの引数に、親への報告は子の記録の最後に出る。1つずつ止める操作は置かない
 * （design.md §11.2）。子の応答の途中経過は流さない。
 */
const props = withDefaults(
  defineProps<{ subagent: Subagent; events: RunStreamEvent[]; scriptCalls?: ScriptCall[]; active?: boolean }>(),
  { scriptCalls: () => [], active: false },
)

const live = computed(() => props.active && isLiveSubagent(props.subagent))
const failed = computed(() => props.subagent.status === SubagentStatus.FAILED)
</script>

<template>
  <UCollapsible>
    <DBtn
      variant="link"
      color="neutral"
      size="xs"
      class="px-0"
      icon="i-lucide-bot"
      trailing-icon="i-lucide-chevron-down"
    >
      <span class="flex flex-wrap items-center gap-x-2 gap-y-1 text-left">
        <span>{{ $t('サブエージェント「{description}」', { description: subagent.description }) }}</span>
        <UBadge :color="subagentStatusColor(subagent.status)" variant="subtle" size="sm">
          {{ subagent.statusDisplay }}
        </UBadge>
        <UChatShimmer v-if="live" :text="$t('作業しています')" />
      </span>
    </DBtn>
    <template #content>
      <div class="mt-1 grid gap-2 border-l-2 border-default pl-3">
        <p class="text-xs text-dimmed">
          {{ subagent.modelProfileName }}<template v-if="subagent.effort"> · {{ subagent.effort }}</template> ·
          {{ $t('{n}ステップ', { n: subagent.stepCount }) }}
        </p>
        <p v-if="subagent.error" :class="['text-xs whitespace-pre-wrap', failed ? 'text-error' : 'text-muted']">
          {{ subagent.error }}
        </p>
        <RunEventLog :events="events" :script-calls="scriptCalls" :subagent="subagent" :active="live" />
      </div>
    </template>
  </UCollapsible>
</template>
