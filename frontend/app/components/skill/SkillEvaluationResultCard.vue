<script setup lang="ts">
/**
 * 評価の1つのケースの結果。公開中の版とDraftの結果を並べ、機械的な検証（完了・業務結果・期待値）と、
 * LLMの判定（参考）を示す。通過かどうかはDraftの側の機械的な検証で決まる（design.md §3.7.1）
 */
const props = defineProps<{ result: SkillEvaluationResult; hasBase: boolean }>()
const { t } = useI18n()

const done = computed(() => props.result.state === EvaluationResultState.DONE)
const sides = computed(() => [
  ...(props.hasBase
    ? [{ key: 'base', title: t('公開中の版'), check: props.result.baseCheck, status: props.result.baseStatus }]
    : []),
  { key: 'draft', title: t('Draft'), check: props.result.draftCheck, status: props.result.draftStatus },
])

const formatJson = (value: unknown) => JSON.stringify(value, null, 2)
const mismatchText = (m: unknown) => {
  const item = m as { path: string; expected: unknown; actual: unknown; missing?: boolean }
  return item.missing
    ? t('{path}：業務結果にありません（期待：{expected}）', { path: item.path, expected: formatJson(item.expected) })
    : t('{path}：期待 {expected}・実際 {actual}', {
        path: item.path,
        expected: formatJson(item.expected),
        actual: formatJson(item.actual),
      })
}
</script>

<template>
  <UCard :ui="{ body: 'p-3 sm:p-4' }">
    <div class="grid gap-3">
      <div class="flex flex-wrap items-center gap-2">
        <span class="font-semibold">{{ result.caseName }}</span>
        <UBadge v-if="result.required" color="warning" variant="subtle" size="sm" icon="i-lucide-shield-check">
          {{ $t('必須') }}
        </UBadge>
        <UBadge v-if="!done" color="primary" variant="subtle" size="sm" icon="i-lucide-loader-circle">
          {{ result.stateDisplay }}
        </UBadge>
        <UBadge v-else-if="result.passed" color="success" variant="subtle" size="sm" icon="i-lucide-circle-check">
          {{ $t('通過') }}
        </UBadge>
        <UBadge v-else color="error" variant="subtle" size="sm" icon="i-lucide-circle-x">{{ $t('通過せず') }}</UBadge>
        <UBadge v-if="result.verdict" :color="evaluationVerdictColor(result.verdict)" variant="outline" size="sm">
          {{ $t('判定：{verdict}', { verdict: result.verdictDisplay }) }}
        </UBadge>
      </div>
      <p v-if="result.judgeReason" class="text-sm">
        <span class="text-muted">{{ $t('判定の理由（参考）：') }}</span
        >{{ result.judgeReason }}
      </p>
      <p v-else-if="result.judgeNote" class="text-xs text-muted">{{ result.judgeNote }}</p>

      <UCollapsible>
        <DBtn variant="link" color="neutral" size="sm" class="px-0" trailing-icon="i-lucide-chevron-down">
          {{ $t('依頼と確かめること') }}
        </DBtn>
        <template #content>
          <dl class="mt-2 grid gap-2 text-sm">
            <div>
              <dt class="text-xs text-muted">{{ $t('依頼の文') }}</dt>
              <dd class="whitespace-pre-wrap">{{ result.caseData.prompt }}</dd>
            </div>
            <div v-if="result.caseData.files.length">
              <dt class="text-xs text-muted">{{ $t('添付') }}</dt>
              <dd>{{ result.caseData.files.map((f) => f.name).join('、') }}</dd>
            </div>
            <div v-if="result.caseData.criteria">
              <dt class="text-xs text-muted">{{ $t('確認する観点') }}</dt>
              <dd class="whitespace-pre-wrap">{{ result.caseData.criteria }}</dd>
            </div>
            <div v-if="result.caseData.expectedResult != null">
              <dt class="text-xs text-muted">{{ $t('期待する業務結果') }}</dt>
              <dd>
                <pre class="overflow-x-auto rounded-md bg-muted p-2 text-xs">{{
                  formatJson(result.caseData.expectedResult)
                }}</pre>
              </dd>
            </div>
          </dl>
        </template>
      </UCollapsible>

      <div class="grid gap-3" :class="{ 'md:grid-cols-2': sides.length > 1 }">
        <div
          v-for="side in sides"
          :key="side.key"
          class="grid min-w-0 content-start gap-2 rounded-md border border-default p-3"
        >
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-sm font-semibold">{{ side.title }}</span>
            <span class="text-xs text-muted">{{ side.check?.statusDisplay || side.status }}</span>
          </div>
          <template v-if="side.check">
            <ul class="grid gap-1 text-xs">
              <li class="flex items-center gap-1.5">
                <UIcon
                  :name="side.check.completed ? 'i-lucide-circle-check' : 'i-lucide-circle-x'"
                  :class="side.check.completed ? 'text-success' : 'text-error'"
                />
                {{ side.check.completed ? $t('完了した') : $t('完了しなかった') }}
              </li>
              <li v-if="side.check.resultRequired" class="flex items-center gap-1.5">
                <UIcon
                  :name="side.check.resultSubmitted ? 'i-lucide-circle-check' : 'i-lucide-circle-x'"
                  :class="side.check.resultSubmitted ? 'text-success' : 'text-error'"
                />
                {{ side.check.resultSubmitted ? $t('業務結果を確定した') : $t('業務結果を確定しなかった') }}
              </li>
              <li v-if="side.check.expectedChecked" class="flex items-start gap-1.5">
                <UIcon
                  :name="side.check.mismatches?.length ? 'i-lucide-circle-x' : 'i-lucide-circle-check'"
                  :class="side.check.mismatches?.length ? 'text-error' : 'text-success'"
                  class="mt-0.5 shrink-0"
                />
                <span v-if="!side.check.mismatches?.length">{{ $t('期待する業務結果と一致した') }}</span>
                <span v-else class="grid gap-0.5">
                  <span>{{ $t('期待する業務結果と違う') }}</span>
                  <span v-for="(m, i) in side.check.mismatches" :key="i" class="font-mono break-all">
                    {{ mismatchText(m) }}
                  </span>
                </span>
              </li>
            </ul>
            <p v-if="side.check.error" class="text-xs text-error">{{ side.check.error }}</p>
            <div v-if="side.check.reply" class="max-h-80 overflow-y-auto rounded-md bg-elevated/50 p-2 text-sm">
              <MarkdownContent :text="side.check.reply" />
            </div>
            <UCollapsible v-if="side.check.result != null">
              <DBtn variant="link" color="neutral" size="sm" class="px-0" trailing-icon="i-lucide-chevron-down">
                {{ $t('業務結果') }}
              </DBtn>
              <template #content>
                <pre class="mt-1 max-h-60 overflow-auto rounded-md bg-muted p-2 text-xs">{{
                  formatJson(side.check.result)
                }}</pre>
              </template>
            </UCollapsible>
          </template>
          <p v-else class="text-xs text-muted">{{ $t('終わるのを待っています') }}</p>
        </div>
      </div>
    </div>
  </UCard>
</template>
