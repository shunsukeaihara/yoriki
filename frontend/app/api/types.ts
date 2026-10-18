import type { CamelCasedPropertiesDeep } from 'type-fest'
import type { components } from './schema'

// API の型は schema.yaml（drf-spectacular）から生成する（docs/frontend-conventions.md §3.3）
type Schemas = components['schemas']
type Api<K extends keyof Schemas> = CamelCasedPropertiesDeep<Schemas[K]>

export type MyUser = Api<'MyUser'>
export type Session = Api<'Session'>
export type AuthProvider = Api<'AuthProvider'>
