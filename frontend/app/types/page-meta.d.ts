import type { PermissionCode } from '~/composables/permission'

declare module '#app' {
  interface PageMeta {
    /** false ならログインしていなくても表示する（ログイン画面、開発用ページ） */
    auth?: boolean
    /** ページを見るのに必要な権限コード。配列はいずれか1つ */
    permission?: PermissionCode | PermissionCode[]
  }
}

export {}
