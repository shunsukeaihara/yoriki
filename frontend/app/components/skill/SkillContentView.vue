<script setup lang="ts">
/** 公開した版の中身（読み取り専用） */
defineProps<{ skillId: string; version: SkillVersion }>()
const { t } = useI18n()
const requirementLabel = (value: number) => skillRequirementItems(t).find((i) => i.value === value)?.title ?? value
</script>

<template>
  <div class="grid gap-5">
    <section class="grid gap-1">
      <h3 class="text-sm font-semibold text-muted">{{ $t('説明') }}</h3>
      <p class="whitespace-pre-wrap text-sm">{{ version.description }}</p>
    </section>
    <section class="grid gap-1">
      <h3 class="text-sm font-semibold text-muted">{{ $t('手順') }}</h3>
      <pre class="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded-md bg-elevated p-3 text-xs">{{
        version.instructions
      }}</pre>
    </section>
    <section class="grid gap-1">
      <h3 class="text-sm font-semibold text-muted">{{ $t('使ってよいTool') }}</h3>
      <div v-if="version.tools.length" class="flex flex-wrap gap-1">
        <UBadge v-for="tool in version.tools" :key="tool" color="neutral" variant="subtle" class="font-mono">
          {{ tool }}
        </UBadge>
      </div>
      <p v-else class="text-sm text-muted">{{ $t('なし（コマンドを使わないSkill）') }}</p>
    </section>
    <section v-if="version.requires.length" class="grid gap-1">
      <h3 class="text-sm font-semibold text-muted">{{ $t('必要な能力') }}</h3>
      <div class="flex flex-wrap gap-1">
        <UBadge v-for="r in version.requires" :key="r" color="neutral" variant="subtle">
          {{ requirementLabel(r) }}
        </UBadge>
      </div>
    </section>
    <section class="grid gap-1">
      <h3 class="text-sm font-semibold text-muted">{{ $t('資料') }}</h3>
      <p v-if="!version.assets.length" class="text-sm text-muted">{{ $t('資料はありません') }}</p>
      <div
        v-for="asset in version.assets"
        :key="asset.key"
        class="grid gap-0.5 rounded-md border border-default px-3 py-2"
      >
        <div class="flex items-center gap-2">
          <span class="font-mono text-sm font-semibold">{{ asset.key }}</span>
          <UBadge color="neutral" variant="subtle" size="sm">{{ asset.roleDisplay }}</UBadge>
        </div>
        <p v-if="asset.description" class="text-xs text-muted">{{ asset.description }}</p>
        <SkillFileLink :skill-id="skillId" :file="asset.file" />
        <div class="flex flex-wrap items-center gap-2">
          <SkillAssetCheckSummary :validation="asset.validation" />
          <SkillAssetCheckDialog :asset-key="asset.key" :validation="asset.validation" :base-label="$t('前の版')">
            <template #trigger="{ on }">
              <DBtn size="xs" color="neutral" variant="link" icon="i-lucide-file-diff" v-on="on">
                {{ $t('検証・差分') }}
              </DBtn>
            </template>
          </SkillAssetCheckDialog>
        </div>
      </div>
    </section>
    <section v-if="version.inputSchema" class="grid gap-1">
      <h3 class="text-sm font-semibold text-muted">{{ $t('入力のSchema') }}</h3>
      <pre class="overflow-auto rounded-md bg-elevated p-3 text-xs">{{ schemaToText(version.inputSchema) }}</pre>
    </section>
    <section v-if="version.outputSchema" class="grid gap-1">
      <h3 class="text-sm font-semibold text-muted">{{ $t('業務結果のSchema') }}</h3>
      <pre class="overflow-auto rounded-md bg-elevated p-3 text-xs">{{ schemaToText(version.outputSchema) }}</pre>
    </section>
  </div>
</template>
