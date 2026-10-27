import type { CamelCasedPropertiesDeep } from 'type-fest'
import type { ListParams } from './resource'
import type { components } from './schema'

// API の型は schema.yaml（drf-spectacular）から生成する（docs/frontend-conventions.md §3.3）
type Schemas = components['schemas']
type Api<K extends keyof Schemas> = CamelCasedPropertiesDeep<Schemas[K]>

export type MyUser = Api<'MyUser'>
export type Session = Api<'Session'>
export type AuthProvider = Api<'AuthProvider'>

export type ProviderKindEnum = Schemas['ProviderKindEnum']
export type LLMConnection = Api<'LLMConnection'>
export type LLMConnectionWrite = Api<'LLMConnectionRequest'>
export type LLMConnectionUpdate = Api<'LLMConnectionUpdateRequest'>
export type LLMConnectionCheck = Api<'LLMConnectionCheck'>
export type MinimumLLMConnection = Api<'MinimumLLMConnection'>
export type ModelProfile = Api<'ModelProfile'>
export type ModelProfileWrite = Api<'ModelProfileRequest'>
export type ModelProfileVersion = Api<'ModelProfileVersion'>
export type RuntimeProfile = Api<'RuntimeProfile'>
export type RuntimeProfileWrite = Api<'RuntimeProfileRequest'>
export type RuntimeProfileVersion = Api<'RuntimeProfileVersion'>

// 一覧の検索条件（バックエンドの FilterSet と合わせる）
export type LLMConnectionListParams = ListParams & { name?: string; kind?: ProviderKindEnum; isActive?: boolean }
export type ModelProfileListParams = ListParams & { name?: string; connection?: string; isActive?: boolean }
export type RuntimeProfileListParams = ListParams & { name?: string; isActive?: boolean }

export type ConnectorKindEnum = Schemas['ConnectorKindEnum']
export type TlsEnum = Schemas['TlsEnum']
export type DatabaseConnection = Api<'DatabaseConnection'>
export type DatabaseConnectionWrite = Api<'DatabaseConnectionRequest'>
export type DatabaseConnectionUpdate = Api<'DatabaseConnectionUpdateRequest'>
export type MinimumDatabaseConnection = Api<'MinimumDatabaseConnection'>
export type Connector = Api<'Connector'>
export type ConnectorWrite = Api<'ConnectorRequest'>
export type ConnectorUpdate = Api<'ConnectorUpdateRequest'>
export type ConnectorTableEntry = Api<'ConnectorTableEntry'>
export type ConnectorCheck = Api<'ConnectorCheck'>
export type ConnectorTable = Api<'ConnectorTable'>
export type ConnectorTables = Api<'ConnectorTables'>
export type ConnectorQuery = Api<'ConnectorQueryRequest'>
export type ConnectorQueryResult = Api<'ConnectorQueryResult'>
export type ConnectorVersion = Api<'ConnectorVersion'>
// 会話で使えるConnector（入力欄の `@` の候補）
export type AppConnector = Api<'AppConnector'>
// 使えるConnectorの詳細（Skillで使う表を宣言するときの候補）
export type AppConnectorDetail = Api<'AppConnectorDetail'>
export type DatabaseConnectionListParams = ListParams & { name?: string; kind?: ConnectorKindEnum; isActive?: boolean }
export type ConnectorScopeEnum = Schemas['ConnectorScopeEnum']
export type ConnectorListParams = ListParams & {
  name?: string
  connection?: string
  kind?: ConnectorKindEnum
  isActive?: boolean
  isSensitive?: boolean
  scope?: ConnectorScopeEnum
  department?: string
}

export type SSOProviderKindEnum = Schemas['SSOProviderKindEnum']
export type SSOProvider = Api<'SSOProvider'>
export type SSOProviderWrite = Api<'SSOProviderRequest'>
export type SSOProviderUpdate = Api<'SSOProviderUpdateRequest'>

export type AuthMethods = Api<'AuthMethods'>
export type Login = Api<'LoginRequest'>
export type InvitationToken = Api<'InvitationToken'>
export type InvitationAccept = Api<'InvitationAcceptRequest'>
export type InvitationLink = Api<'InvitationLink'>
export type ManageUser = Api<'ManageUser'>
export type ManageUserCreated = Api<'ManageUserCreate'>
export type ManageUserWrite = Api<'ManageUserCreateRequest'>
export type ManageUserUpdate = Api<'ManageUserUpdateRequest'>
export type Group = Api<'Group'>

export type ManageUserListParams = ListParams & { isActive?: boolean; group?: string }

export type MyDepartment = Api<'MyDepartment'>
export type Department = Api<'Department'>
export type MinimumDepartment = Api<'MinimumDepartment'>
export type DepartmentWrite = Api<'DepartmentRequest'>
export type DepartmentMembership = Api<'DepartmentMembership'>
export type DepartmentMembershipWrite = Api<'DepartmentMembershipRequest'>
export type DepartmentMembershipListParams = ListParams & { q?: string }

export type SkillScopeEnum = Schemas['SkillScopeEnum']
export type SkillAssetRoleEnum = Schemas['SkillAssetRoleEnum']
export type SkillRequirementEnum = Schemas['SkillRequirementEnum']
export type SkillChangeKindEnum = Schemas['SkillChangeKindEnum']
export type ToolName = Schemas['ToolsEnum']
export type Skill = Api<'Skill'>
export type SkillCreate = Api<'SkillCreateRequest'>
export type SkillWrite = Api<'SkillRequest'>
export type SkillVersion = Api<'SkillVersion'>
export type MinimumSkillVersion = Api<'MinimumSkillVersion'>
export type SkillAsset = Api<'SkillAsset'>
export type SkillChange = Api<'SkillChange'>
export type SkillPublish = Api<'SkillPublishRequest'>
export type SkillRollback = Api<'SkillRollbackRequest'>
export type SkillSuspend = Api<'SkillSuspendRequest'>
export type SkillDraft = Api<'SkillDraft'>
// Skillが宣言するConnectorと操作・表（design.md §4.9.1）
export type ConnectorDeclaration = Api<'ConnectorDeclaration'>
export type DeclaredConnector = Api<'DeclaredConnector'>
export type SkillDraftWrite = Api<'SkillDraftRequest'>
export type SkillDraftOpen = Api<'SkillDraftOpenRequest'>
export type SkillDraftAsset = Api<'SkillDraftAsset'>
export type ResourceVersion = Api<'ResourceVersion'>
export type AssetChecks = Api<'AssetChecks'>
export type AssetValidation = Api<'AssetValidation'>
export type AssetDiff = Api<'AssetDiff'>
export type AssetDiffCount = Api<'AssetDiffCount'>
export type SkillDraftAssetChecks = Api<'SkillDraftAssetChecksRequest'>
export type CsvEncodingEnum = Schemas['CsvEncodingEnum']
export type CsvDelimiterEnum = Schemas['CsvDelimiterEnum']
export type CsvColumnTypeEnum = Schemas['CsvColumnTypeEnum']
// 資料は multipart で送るので、file は File にする（生成した型は binary の string）
export type SkillDraftAssetWrite = Omit<Api<'SkillDraftAssetRequest'>, 'file'> & { file: File }
export type SkillDraftAssetUpdate = Omit<Api<'SkillDraftAssetUpdateRequest'>, 'file'> & { file?: File | null }
export type EvaluationStatusEnum = Schemas['EvaluationStatusEnum']
export type EvaluationVerdictEnum = Schemas['EvaluationVerdictEnum']
export type SkillEvaluationCase = Api<'SkillEvaluationCase'>
// 評価ケースは multipart で送る（添付のファイル）。expectedResult はJSONの値
export type SkillEvaluationCaseWrite = Omit<Api<'SkillEvaluationCaseRequest'>, 'addFiles' | 'expectedResult'> & {
  expectedResult?: unknown
  addFiles?: File[]
}
export type SkillEvaluation = Api<'SkillEvaluation'>
export type MinimumSkillEvaluation = Api<'MinimumSkillEvaluation'>
export type SkillEvaluationResult = Api<'SkillEvaluationResult'>
export type SkillEvaluationCreate = Api<'SkillEvaluationCreateRequest'>
export type EvaluationCheck = Api<'EvaluationCheck'>
export type SkillListParams = ListParams & {
  name?: string
  scope?: SkillScopeEnum
  department?: string
  suspended?: boolean
  published?: boolean
  editable?: boolean
}

export type AppModelProfile = Api<'AppModelProfile'>
export type Conversation = Api<'Conversation'>
export type ConversationWrite = Api<'ConversationRequest'>
export type Message = Api<'Message'>
export type MessageWrite = Api<'MessageRequest'>
export type MessageRoleEnum = Schemas['MessageRoleEnum']
export type InstructionKindEnum = Schemas['InstructionKindEnum']
export type InstructionStateEnum = Schemas['InstructionStateEnum']
export type Artifact = Api<'Artifact'>
export type ArtifactKindEnum = Schemas['ArtifactKindEnum']

export type RunStatusEnum = Schemas['RunStatusEnum']
export type RunEventKindEnum = Schemas['RunEventKindEnum']
export type Run = Api<'Run'>
export type RunKindEnum = Schemas['RunKindEnum']
export type RunSkillChange = Api<'RunSkillChange'>
export type Todo = Api<'Todo'>
export type RunCancel = Api<'RunCancelRequest'>
// モデル名が ClarificationRequest なので、これは出力用の型（入力用は ClarificationAnswerRequest）
export type Clarification = Api<'ClarificationRequest'>
export type ClarificationKindEnum = Schemas['ClarificationKindEnum']
export type ClarificationStatusEnum = Schemas['ClarificationStatusEnum']
export type ClarificationAnswer = Api<'ClarificationAnswerRequest'>
export type ClarificationAnswerItem = Api<'ClarificationAnswerItemRequest'>

// SSE のイベント（realtime.md §3）。type で判別できる union 型
export type RunStreamEvent = Api<'RunStreamEvent'>
export type RunMessageEvent = Api<'RunMessageEvent'>
export type UserStreamEvent = Api<'UserStreamEvent'>

export type ConversationListParams = ListParams
export type ArtifactListParams = ListParams & { kind?: ArtifactKindEnum }
export type RunListParams = ListParams & { status?: RunStatusEnum; conversation?: string }
export type ClarificationListParams = ListParams & { status?: ClarificationStatusEnum }
export type ToolCallInfo = Api<'ToolCallInfo'>
export type ToolResultInfo = Api<'ToolResultInfo'>
