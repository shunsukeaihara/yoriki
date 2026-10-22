<script setup lang="ts">
/** Runtime Profileの版の履歴（読み取り専用）。新しい版から順に並べる */
const props = defineProps<{ runtimeProfileId: string }>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
const api = useAPI()

const {
  items: versions,
  loading,
  fetchAll,
} = useFetchAll(() => api.manage.runtimeProfiles(props.runtimeProfileId).versions().list())
watch(reloadKey, fetchAll, { immediate: true })
</script>

<template>
  <div class="grid gap-3">
    <p v-if="loading && !versions.length" class="text-sm text-muted">{{ $t('読み込み中') }}</p>
    <UCard v-for="v in versions" :key="v.id" :ui="{ body: 'p-3 sm:p-3' }">
      <div class="flex items-center justify-between gap-2">
        <span class="font-semibold">{{ $t('版 {number}', { number: v.number }) }}</span>
        <span class="text-xs text-muted">{{ formatDatetime(v.created) }}</span>
      </div>
      <p v-if="v.changeReason" class="mt-1 whitespace-pre-wrap text-sm">{{ v.changeReason }}</p>
      <dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt class="text-muted">{{ $t('イメージ') }}</dt>
        <dd class="font-mono break-all">{{ v.image }}</dd>
        <dt class="text-muted">{{ $t('契約番号・platform') }}</dt>
        <dd>{{ v.contract }}・{{ v.platforms.join(', ') }}</dd>
        <dt class="text-muted">{{ $t('資源') }}</dt>
        <dd>
          {{
            $t('メモリ {memory}MB・CPU {cpus}・プロセス {pids}', {
              memory: v.memoryMb,
              cpus: v.cpus,
              pids: v.pidsLimit,
            })
          }}
        </dd>
        <dt class="text-muted">{{ $t('領域') }}</dt>
        <dd>
          {{
            $t('/tmp {tmp}MB・work {work}MB・output {output}MB・env {env}MB', {
              tmp: v.tmpMb,
              work: v.workMb,
              output: v.outputMb,
              env: v.envMb,
            })
          }}
        </dd>
        <dt class="text-muted">{{ $t('時間の上限') }}</dt>
        <dd>
          {{
            $t('コマンド {command}秒・ジョブ {job}秒', {
              command: v.commandTimeoutSeconds,
              job: v.jobTimeoutSeconds,
            })
          }}
        </dd>
      </dl>
    </UCard>
  </div>
</template>
