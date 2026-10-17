<script setup lang="ts" generic="T extends Record<string, any>, P">
/**
 * API と連動した表。fetch に ViewSet の list、params に検索条件を渡す。
 * DRF のカーソル方式のページングで前後のページに移動する。
 * reloadKey を変えると、検索条件で1ページ目から読み直す。
 */
import type { TableColumn } from '@nuxt/ui'
import type { Ref } from 'vue'

const props = defineProps<{
  columns: TableColumn<T>[]
  fetch: (params: P) => Promise<Paginated<T>>
  params: P
}>()
const reloadKey = defineModel<number>('reloadKey', { default: 0 })
defineOptions({ inheritAttrs: false })

const { items, loading, error, hasNext, hasPrevious, loadNext, loadPrevious, reload } = useCursorList(
  props.fetch,
  toRef(props, 'params') as Ref<P>,
)
watch(reloadKey, reload, { immediate: true })
defineExpose({ reload })
</script>

<template>
  <div class="grid gap-2">
    <DFormAlert v-if="error" :errors="getValidationError(error)" />
    <UTable :data="items" :columns="columns" :loading="loading" v-bind="$attrs">
      <template v-for="(_, name) in $slots" #[name]="slotProps">
        <slot :name="name" v-bind="slotProps ?? {}" />
      </template>
    </UTable>
    <div v-if="hasPrevious || hasNext" class="flex justify-end gap-2">
      <DBtn color="neutral" variant="ghost" icon="i-lucide-chevron-left" :disabled="!hasPrevious" @click="loadPrevious">
        {{ $t('前へ') }}
      </DBtn>
      <DBtn
        color="neutral"
        variant="ghost"
        trailing-icon="i-lucide-chevron-right"
        :disabled="!hasNext"
        @click="loadNext"
      >
        {{ $t('次へ') }}
      </DBtn>
    </div>
  </div>
</template>
