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

// 一覧の検索条件（バックエンドの FilterSet と合わせる）
export type LLMConnectionListParams = ListParams & { name?: string; kind?: ProviderKindEnum; isActive?: boolean }
export type ModelProfileListParams = ListParams & { name?: string; connection?: string; isActive?: boolean }

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

export type ManageUserListParams = ListParams & { isActive?: boolean; isSuperuser?: boolean; group?: string }

export type AppModelProfile = Api<'AppModelProfile'>
export type Conversation = Api<'Conversation'>
export type ConversationWrite = Api<'ConversationRequest'>
export type Message = Api<'Message'>
export type MessageWrite = Api<'MessageRequest'>
export type MessageRoleEnum = Schemas['MessageRoleEnum']
export type InstructionKindEnum = Schemas['InstructionKindEnum']
export type InstructionStateEnum = Schemas['InstructionStateEnum']

export type RunStatusEnum = Schemas['RunStatusEnum']
export type RunEventKindEnum = Schemas['RunEventKindEnum']
export type Run = Api<'Run'>
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
export type RunListParams = ListParams & { status?: RunStatusEnum; conversation?: string }
export type ClarificationListParams = ListParams & { status?: ClarificationStatusEnum }
export type ToolCallInfo = Api<'ToolCallInfo'>
export type ToolResultInfo = Api<'ToolResultInfo'>
