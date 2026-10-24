// バックエンドの skill.models.SkillScope と同じ値
export const SkillScope = {
  PERSONAL: 1,
  DEPARTMENT: 2,
  COMPANY: 3,
} as const satisfies Record<string, SkillScopeEnum>

// 利用者がSkillに対してできること（Skill.access。skill.models.SkillAccess）。上は下を含む
export const SkillAccess = {
  NONE: 0,
  USE: 1,
  // Draftを変更する（説明・手順・Schema・資料）
  EDIT: 2,
  // 公開・切戻し・停止・削除・名前とToolの変更
  PUBLISH: 3,
} as const

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

// user.models.DepartmentRole と同じ値
export const DepartmentRole = {
  MEMBER: 1,
  EDITOR: 2,
  PUBLISHER: 3,
} as const satisfies Record<string, DepartmentRoleEnum>

type T = (key: string) => string

/** 公開範囲の選択肢（検索条件と作成）。一覧の表示はサーバーの scopeDisplay を使う */
export const skillScopeItems = (t: T) => [
  { value: SkillScope.PERSONAL, title: t('個人'), description: t('自分だけが使います') },
  { value: SkillScope.DEPARTMENT, title: t('部門'), description: t('部門に所属する人が使います') },
  { value: SkillScope.COMPANY, title: t('全社'), description: t('全社員が使います') },
]

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

/** 使ってよいToolの選択肢（バックエンドの harness.tools.ALL_TOOLS） */
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
  ] satisfies { value: ToolName; title: string; description: string }[]

export const departmentRoleItems = (t: T) => [
  { value: DepartmentRole.MEMBER, title: t('利用'), description: t('部門のSkillを使う') },
  { value: DepartmentRole.EDITOR, title: t('編集'), description: t('部門のSkillのDraftを変更する') },
  { value: DepartmentRole.PUBLISHER, title: t('公開'), description: t('部門のSkillを公開・切戻し・停止する') },
]

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
  return t('全社')
}
