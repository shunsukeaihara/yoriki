<script setup lang="ts">
/**
 * 会話の添付と成果物（design.md §11.2「添付と成果物」）。会話を遡らずに開けるよう、入力ファイルと生成物を分けて
 * 新しい順に並べる。成果物は作ったときの日時を添え、作り直した前後を区別できるようにする。
 * どちらもエージェントが見る名前（成果物は /workspace/output からのパス、添付は同じ名前を分けた名前）で出す。
 * エージェントが Connector で読んだ結果（CSV）も、読んだ日時を添えて別に並べる（あるときだけ）
 */
const props = defineProps<{ conversationId: string; items: Artifact[]; loading?: boolean }>()

const outputs = computed(() => props.items.filter((a) => a.kind === ArtifactKind.OUTPUT))
const attachments = computed(() => props.items.filter((a) => a.kind === ArtifactKind.ATTACHMENT))
const connectorResults = computed(() => props.items.filter((a) => a.kind === ArtifactKind.CONNECTOR_RESULT))
</script>

<template>
  <div class="grid gap-6">
    <section>
      <h3 class="mb-1 px-2 text-xs font-semibold text-muted">{{ $t('成果物') }}</h3>
      <ul v-if="outputs.length">
        <li v-for="artifact in outputs" :key="artifact.id">
          <ArtifactLink :conversation-id="conversationId" :artifact="artifact" show-path />
          <p class="px-2 pb-1 ps-8 text-xs text-dimmed">{{ formatDatetime(artifact.created) }}</p>
        </li>
      </ul>
      <p v-else-if="!loading" class="px-2 text-sm text-dimmed">{{ $t('まだありません') }}</p>
    </section>
    <section>
      <h3 class="mb-1 px-2 text-xs font-semibold text-muted">{{ $t('添付') }}</h3>
      <ul v-if="attachments.length">
        <li v-for="artifact in attachments" :key="artifact.id">
          <ArtifactLink :conversation-id="conversationId" :artifact="artifact" show-path />
        </li>
      </ul>
      <p v-else-if="!loading" class="px-2 text-sm text-dimmed">{{ $t('まだありません') }}</p>
    </section>
    <section v-if="connectorResults.length">
      <h3 class="mb-1 px-2 text-xs font-semibold text-muted">{{ $t('Connectorの結果') }}</h3>
      <ul>
        <li v-for="artifact in connectorResults" :key="artifact.id">
          <ArtifactLink :conversation-id="conversationId" :artifact="artifact" />
          <p class="px-2 pb-1 ps-8 text-xs text-dimmed">{{ formatDatetime(artifact.created) }}</p>
        </li>
      </ul>
    </section>
  </div>
</template>
