// definePageMeta の permission を確認する。配列はいずれか1つを持っていればよい
export default defineNuxtRouteMiddleware((to) => {
  const required = to.meta.permission
  if (required === undefined) return
  const codes = Array.isArray(required) ? required : [required]
  if (codes.length === 0) return
  if (!usePermission().hasAny(codes)) {
    return abortNavigation(createError({ statusCode: 403, statusMessage: 'Forbidden' }))
  }
})
