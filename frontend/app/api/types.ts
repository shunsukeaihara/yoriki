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
