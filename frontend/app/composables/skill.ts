// 入力欄の `/` で1つのメッセージに指定できる Skill の数（バックエンドの harness.skills.MAX_EXPLICIT_SKILLS）
export const MAX_CHOSEN_SKILLS = 3

// バックエンドの skill.models.SkillScope と同じ値
export const SkillScope = {
  PERSONAL: 1,
  DEPARTMENT: 2,
  COMPANY: 3,
  SYSTEM: 4,
} as const satisfies Record<string, SkillScopeEnum>

// skill.models.SkillAssetRole と同じ値
export const SkillAssetRole = {
  REFERENCE: 1,
  MASTER: 2,
  TEMPLATE: 3,
  SCRIPT: 4,
} as const satisfies Record<string, SkillAssetRoleEnum>

// skill.models.SkillChangeKind と同じ値
export const SkillChangeKind = {
  PUBLISH: 1,
  ROLLBACK: 2,
  SUSPEND: 3,
  RESUME: 4,
} as const satisfies Record<string, SkillChangeKindEnum>

/** 入力欄で選んだ Skill の編集の依頼。skill が null なら新しい Skill を作る（design.md §11.6） */
export type SkillEditChoice = { skill: Skill | null }

type T = (key: string, params?: Record<string, unknown>) => string

/** 範囲の選択肢（検索条件と作成）。一覧の表示はサーバーの scopeDisplay を使う。システムは管理画面だけで使う */
export const skillScopeItems = (t: T) => [
  { value: SkillScope.PERSONAL, title: t('個人'), description: t('自分だけが使います') },
  { value: SkillScope.DEPARTMENT, title: t('部門'), description: t('部門に所属する人が使います') },
  { value: SkillScope.COMPANY, title: t('全社'), description: t('全社員が使います') },
  {
    value: SkillScope.SYSTEM,
    title: t('システム'),
    description: t('システムのタスクだけが使います（会話には出しません）'),
  },
]

/**
 * 利用者が作れる範囲と、部門のSkillを作れる部門（authorization.md §7.3.1）。個人はいつでも、部門は管理できる部門と
 * 部門のSkillの編集・公開のpermission、全社・システムはそれぞれのpermission。サーバーでも確かめ、なければ403
 */
export const useSkillCreatableScopes = () => {
  const account = useAccountStore()
  const { hasAny } = usePermission()
  const departments = computed(() =>
    hasAny([PermissionCode.EDIT_DEPARTMENT_SKILLS, PermissionCode.PUBLISH_DEPARTMENT_SKILLS])
      ? (account.user?.manageableDepartments ?? [])
      : [],
  )
  const scopes = computed<SkillScopeEnum[]>(() => [
    SkillScope.PERSONAL,
    ...(departments.value.length ? [SkillScope.DEPARTMENT] : []),
    ...(hasAny([PermissionCode.EDIT_COMPANY_SKILLS, PermissionCode.PUBLISH_COMPANY_SKILLS])
      ? [SkillScope.COMPANY]
      : []),
    ...(hasAny([PermissionCode.EDIT_SYSTEM_SKILLS, PermissionCode.PUBLISH_SYSTEM_SKILLS]) ? [SkillScope.SYSTEM] : []),
  ])
  return { scopes, departments }
}

export const skillAssetRoleItems = (t: T) => [
  { value: SkillAssetRole.REFERENCE, title: t('参照資料'), description: t('業務のルール・用語・手順（MD、PDF等）') },
  { value: SkillAssetRole.MASTER, title: t('マスターデータ'), description: t('照合・変換・計算に使う表（CSV等）') },
  { value: SkillAssetRole.TEMPLATE, title: t('テンプレート'), description: t('コピーして成果物を作る原本（PPTX等）') },
  { value: SkillAssetRole.SCRIPT, title: t('スクリプト'), description: t('Sandboxで動かすプログラム') },
]

export const skillRequirementItems = (t: T) =>
  [
    { value: 1, title: t('Tool呼出し') },
    { value: 2, title: t('画像入力') },
    { value: 3, title: t('文書入力') },
    { value: 4, title: t('構造化出力') },
  ] satisfies { value: SkillRequirementEnum; title: string }[]

/** 使ってよいToolの選択肢（バックエンドの harness.tools.SKILL_TOOLS） */
export const skillToolItems = (t: T) =>
  [
    { value: 'read', title: 'read', description: t('ファイルを読む') },
    { value: 'write', title: 'write', description: t('ファイルを書く') },
    { value: 'edit', title: 'edit', description: t('ファイルの一部を書き換える') },
    { value: 'glob', title: 'glob', description: t('ファイルを名前で探す') },
    { value: 'grep', title: 'grep', description: t('ファイルの中の文字列を探す') },
    { value: 'shell', title: 'shell', description: t('Sandboxでコマンドを動かす') },
    { value: 'job_output', title: 'job_output', description: t('バックグラウンドのジョブの出力を見る') },
    { value: 'job_kill', title: 'job_kill', description: t('バックグラウンドのジョブを止める') },
    { value: 'ask_user', title: 'ask_user', description: t('利用者に確認する') },
    { value: 'todo_write', title: 'todo_write', description: t('作業の計画を書く') },
    { value: 'datasource_describe', title: 'datasource_describe', description: t('Connectorの表・列の説明を見る') },
    { value: 'datasource_query', title: 'datasource_query', description: t('ConnectorでSELECT文を実行する') },
  ] satisfies { value: ToolName; title: string; description: string }[]

/** SkillChange の summary.fields の項目名 */
export const skillFieldLabel = (name: string, t: T): string => {
  const labels: Record<string, string> = {
    description: t('説明'),
    instructions: t('手順'),
    tools: t('使ってよいTool'),
    requires: t('必要な能力'),
    input_schema: t('入力のSchema'),
    output_schema: t('業務結果のSchema'),
  }
  return labels[name] ?? name
}

/** 変わった項目と資料の key（SkillChange・Runで変えたSkillの summary） */
export type SkillChangeSummary = Partial<
  Record<'fields' | 'assetsAdded' | 'assetsReplaced' | 'assetsChanged' | 'assetsRemoved', readonly string[]>
>

/** 変更点の行（「手順・使ってよいTool」「資料の追加：vendor-master」等） */
export const skillChangeLines = (summary: SkillChangeSummary | null | undefined, t: T): string[] => {
  if (!summary) return []
  const lines: string[] = []
  if (summary.fields?.length) lines.push(summary.fields.map((name) => skillFieldLabel(name, t)).join('・'))
  const assets: [keyof SkillChangeSummary, string][] = [
    ['assetsAdded', t('資料の追加')],
    ['assetsReplaced', t('資料の置換')],
    ['assetsChanged', t('資料の用途・説明')],
    ['assetsRemoved', t('資料の削除')],
  ]
  for (const [key, label] of assets) {
    if (summary[key]?.length) lines.push(t('{label}：{keys}', { label, keys: summary[key]!.join(', ') }))
  }
  return lines
}

/** 公開・切戻しのoperation key。ダイアログを開くたびに作り、同じ操作の再送で版を重ねて作らない */
export const newOperationKey = () => crypto.randomUUID()

/** 入出力のSchemaの入力欄の文字列 */
export const schemaToText = (value: unknown): string =>
  value === null || value === undefined ? '' : JSON.stringify(value, null, 2)

/**
 * 入力欄の文字列をSchemaとして送る値にする。空ならnull。JSONとして読めなければエラーの文を返す。
 * JSON Schemaとして正しいかはサーバーが確かめる
 */
export const parseSchemaText = (text: string, t: T): { value: unknown; error: string | null } => {
  if (!text.trim()) return { value: null, error: null }
  try {
    return { value: JSON.parse(text), error: null }
  } catch {
    return { value: undefined, error: t('JSONとして読めません') }
  }
}

/** Skillの持ち主（個人は本人、部門は部門名、全社は「全社」） */
export const skillOwnerLabel = (skill: Skill, t: T): string => {
  if (skill.scope === SkillScope.DEPARTMENT) return skill.departmentInfo?.name ?? ''
  if (skill.scope === SkillScope.PERSONAL) return skill.ownerInfo?.displayName || skill.ownerInfo?.email || ''
  return skill.scope === SkillScope.SYSTEM ? t('システム') : t('全社')
}

/**
 * 資料の差分の数の文（「1行追加・2行変更」「1枚変更」等）。asset_diffs の要素か、検証の結果の diff。
 * 中身が同じ（identical）なら「中身の変更なし」。数で表せない変更（PDF等の置換、スライドの並べ替え）は「中身が変わりました」
 */
export const assetDiffText = (
  counts: (Partial<AssetDiffCount> & { identical?: boolean; reordered?: boolean }) | null | undefined,
  t: T,
): string => {
  if (!counts) return ''
  if (counts.identical) return t('中身の変更なし')
  const { linesAdded: la, linesRemoved: lr, rowsAdded: ra, rowsRemoved: rr, rowsChanged: rc } = counts
  const { slidesAdded: sa, slidesRemoved: sr, slidesChanged: sc } = counts
  const texts = [
    la && t('{n}行追加', { n: la }),
    lr && t('{n}行削除', { n: lr }),
    ra && t('{n}行追加', { n: ra }),
    rr && t('{n}行削除', { n: rr }),
    rc && t('{n}行変更', { n: rc }),
    sa && t('{n}枚追加', { n: sa }),
    sr && t('{n}枚削除', { n: sr }),
    sc && t('{n}枚変更', { n: sc }),
    counts.reordered && t('スライドの順番を変更'),
  ].filter((text): text is string => Boolean(text))
  return texts.length ? texts.join('・') : t('中身が変わりました')
}

/** 置き換えた資料の差分の行（「vendor-master：1行追加・2行変更」） */
export const assetDiffLines = (items: readonly AssetDiffCount[] | null | undefined, t: T): string[] =>
  (items ?? []).map((item) => t('{key}：{diff}', { key: item.key, diff: assetDiffText(item, t) }))

/** 資料の検証の状態（誤り・注意・問題なし・未検証） */
export const assetCheckState = (validation: AssetValidation | null | undefined, t: T) => {
  if (!validation?.format) return { label: t('未検証'), color: 'neutral' as const, icon: 'i-lucide-circle-dashed' }
  if (validation.errors?.length)
    return {
      label: t('誤り{n}件', { n: validation.errors.length }),
      color: 'error' as const,
      icon: 'i-lucide-circle-x',
    }
  if (validation.warnings?.length)
    return {
      label: t('注意{n}件', { n: validation.warnings.length }),
      color: 'warning' as const,
      icon: 'i-lucide-triangle-alert',
    }
  return { label: t('検証済み'), color: 'success' as const, icon: 'i-lucide-circle-check' }
}

/** CSVの検証の条件の選択肢（resources.formats） */
export const csvEncodingItems = (t: T) => [
  { value: '', title: t('自動で判別') },
  { value: 'utf-8', title: 'UTF-8' },
  { value: 'utf-8-sig', title: t('UTF-8（BOM付き）') },
  { value: 'cp932', title: 'Shift_JIS（cp932）' },
]

export const csvDelimiterItems = (t: T) => [
  { value: '', title: t('自動で判別') },
  { value: ',', title: t('カンマ') },
  { value: '\t', title: t('タブ') },
  { value: ';', title: t('セミコロン') },
]

export const csvColumnTypeItems = (t: T) =>
  [
    { value: 'string', title: t('文字列') },
    { value: 'integer', title: t('整数') },
    { value: 'decimal', title: t('数値') },
    { value: 'date', title: t('日付') },
  ] satisfies { value: CsvColumnTypeEnum; title: string }[]

// skill.models.EvaluationStatus と同じ値
export const EvaluationStatus = {
  RUNNING: 1,
  FINISHED: 10,
  CANCELLED: 30,
} as const satisfies Record<string, EvaluationStatusEnum>

// skill.models.EvaluationVerdict と同じ値（公開中の版と比べたDraft）
export const EvaluationVerdict = {
  BETTER: 1,
  SAME: 2,
  WORSE: 3,
} as const satisfies Record<string, EvaluationVerdictEnum>

// 1回の評価で動かすケースの数の上限（skill.models.MAX_CASES_PER_EVALUATION）
export const MAX_CASES_PER_EVALUATION = 10

export const evaluationStatusColor = (status: EvaluationStatusEnum) =>
  status === EvaluationStatus.RUNNING ? 'primary' : status === EvaluationStatus.FINISHED ? 'success' : 'neutral'

export const evaluationVerdictColor = (verdict: EvaluationVerdictEnum | null | undefined) => {
  if (verdict === EvaluationVerdict.BETTER) return 'success' as const
  if (verdict === EvaluationVerdict.WORSE) return 'error' as const
  return 'neutral' as const
}

/** 評価の結果のまとめ（「2件中2件が通過」）。評価中は「評価中」 */
export const evaluationSummary = (evaluation: Pick<MinimumSkillEvaluation, 'status' | 'passed' | 'total'>, t: T) =>
  evaluation.status === EvaluationStatus.RUNNING
    ? t('評価中（{total}件）', { total: evaluation.total })
    : t('{total}件中{passed}件が通過', { total: evaluation.total, passed: evaluation.passed })

// skill.models.EvaluationResultState と同じ値
export const EvaluationResultState = {
  RUNNING: 1,
  JUDGING: 2,
  DONE: 10,
} as const
