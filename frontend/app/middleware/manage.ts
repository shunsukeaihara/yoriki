// /manage 以下は、管理画面のメニューに表示できる項目が1つ以上ある利用者だけが入れる（composables/manage.ts）。
// 各ページの権限は、それぞれの definePageMeta の permission で確認する（middleware/permission.global.ts）
export default defineNuxtRouteMiddleware(() => {
  if (useManageMenu().links.value.length === 0) {
    return abortNavigation(createError({ statusCode: 403, statusMessage: 'Forbidden' }))
  }
})
