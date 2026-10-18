const toArray = <T>(value: T | T[] | undefined): T[] =>
  value === undefined ? [] : Array.isArray(value) ? value : [value]

// definePageMeta の permission・role を確認する。配列はいずれか1つを持っていればよい
export default defineNuxtRouteMiddleware((to) => {
  const permissions = toArray(to.meta.permission)
  const roles = toArray(to.meta.role)
  if (permissions.length === 0 && roles.length === 0) return

  const { hasAny, hasAnyRole } = usePermission()
  const allowed = (permissions.length === 0 || hasAny(permissions)) && (roles.length === 0 || hasAnyRole(roles))
  if (!allowed) return abortNavigation(createError({ statusCode: 403, statusMessage: 'Forbidden' }))
})
