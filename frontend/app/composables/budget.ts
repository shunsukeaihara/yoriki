/** 予算の金額（米ドルの10進数の文字列。design.md §9.1）。空の上限・残額は無制限 */
export const formatUsd = (value: string | null | undefined, unlimited: string): string => {
  if (value === null || value === undefined) return unlimited
  const amount = Number(value)
  const sign = amount < 0 ? '-' : ''
  return `${sign}$${Math.abs(amount).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** 上限に対する利用の割合（0〜100）。無制限なら null */
export const budgetUsedPercent = (status: BudgetStatus): number | null => {
  if (status.limit === null) return null
  const limit = Number(status.limit)
  if (limit <= 0) return 100
  return Math.min(100, Math.max(0, ((limit - Number(status.remaining ?? 0)) / limit) * 100))
}

/**
 * 会話・個人タスク・個人のSkillの評価で、予算を計上する部門の選択肢（所属している部門）。所属が2つ以上のときだけ選ばせる
 * （1つならサーバーがその部門にし、なければシステムの枠に入る。design.md §9.1）
 */
export const useBudgetDepartments = () => {
  const account = useAccountStore()
  const items = computed(() => (account.user?.departments ?? []).map((d) => ({ value: d.id, title: d.name })))
  const choosable = computed(() => items.value.length > 1)
  return { items, choosable }
}
