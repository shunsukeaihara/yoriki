# 開発の進め方とCI

ブランチ運用、コミット、レビュー、lint・テスト、CIで必ず通すチェックを定める。コードの書き方は [backend-conventions.md](backend-conventions.md)・[frontend-conventions.md](frontend-conventions.md)、配備は [deployment.md](deployment.md) を参照する。

---

## 1 ブランチとPull Request

- `main` を常にリリースできる状態に保つ。直接pushせず、作業ブランチからPull Requestで取り込む。
- 作業ブランチの名前は `<種類>/<内容>` にする（例：`feature/run-cancel`、`fix/autocomplete-scroll`、`docs/deployment`）。
- Pull Requestは1つの目的に絞る。バックエンド・フロントエンド・Sandboxをまたぐ変更でも、1つの機能なら1つのPull Requestにしてよい（モノレポにした理由）。
- マージはsquash mergeにする。
- CIのチェック（§4）がすべて通り、レビューで承認されたものだけをマージする。

## 2 コミットとPull Requestの書き方

- コミットメッセージは英語で、`<種類>: <要約>` の形にする（Conventional Commits）。種類は `feat`、`fix`、`docs`、`refactor`、`test`、`chore`、`ci`。
  - 例：`feat: add cancel action for runs`
- Pull Requestの説明は日本語で書く。何を・なぜ変えたか、確認した方法、画面の変更があればスクリーンショットを書く。
- 設計（design.md）や規約（docs/*.md）と食い違う変更をする場合は、同じPull Requestで文書も直す。

## 3 lint・整形・型チェック・テスト

### 3.0 エディタ（VS Code）

VS Codeを標準のエディタとし、リポジトリの `.vscode/settings.json` で整形・lintの設定を固定する。人が手で編集しても、エージェントが編集しても、CIと同じ結果になるようにするため。

- 規則の正本は設定ファイルに置き、VS CodeとCIの両方がそれを読む。

| 対象 | 規則の正本 | VS Codeの拡張 |
|---|---|---|
| Python | `ruff.toml`（リポジトリルート） | Ruff（保存時に整形とimportの並べ替え、自動修正） |
| Pythonの型 | `api/pyproject.toml` の `[tool.mypy]` | Mypy Type Checker |
| TypeScript / Vue | `frontend/.prettierrc`、`frontend/eslint.config.mjs` | Prettier（保存時に整形）、ESLint（保存時に自動修正） |
| 全ファイル共通 | `.editorconfig` | EditorConfig |

- 行の長さは、Python・TypeScript・エディタの縦線とも **120** にそろえる。
- 入れるべき拡張は `.vscode/extensions.json` の `recommendations` にある。autopep8・black・pylint・flake8・isortの拡張は、ruffと役割が重なり整形や警告が二重になるので、このワークスペースでは無効にする（`unwantedRecommendations`）。
- Pylanceの型検査は切り、型の検査はmypyに一本化する（Pylanceは補完と定義ジャンプに使う）。
- 環境変数はdirenvの拡張（`mkhl.direnv`）で `.envrc` をVS Codeにも読み込ませる。
- 個人のユーザー設定で、ワークスペースの設定（整形ツール、行の長さ等）を上書きしない。

### 3.1 バックエンド（`api/`）

| 種類 | ツール | コマンド |
|---|---|---|
| lint | ruff（設定は `ruff.toml`。`DJ`（Django）、`B`、`I`（import順）、`PL`（pylint相当）等） | `uv run ruff check` |
| 整形 | ruff format | `uv run ruff format` |
| 型チェック | mypy＋django-stubs＋djangorestframework-stubs | `uv run mypy src` |
| テスト | pytest＋pytest-django＋factory_boy | `uv run pytest` |

- pylint（pylint_django）・autopep8・black・yapfは併用せず、ruffに一本化する。
- 日本語のコメントや文字列に全角の記号（「（」「：」等）を使うので、ruffの `RUF001`〜`RUF003` は無効にしている。
- mypyの設定は、`api/` の雛形を作るときに `api/pyproject.toml` に置く（`plugins = ["mypy_django_plugin.main", "mypy_drf_plugin.main"]`、`[tool.django-stubs] django_settings_module = "app.settings"`）。
- lintの警告は、`# noqa` で抑止せずにコードを直して解消する。抑止する場合は理由をコメントに書く（backend-conventions.md §10）。
- ローカルのテストはSQLite（メモリ上）で実行し、PostgreSQLを起動しなくてよい。PostgreSQLの機能（`select_for_update`、LISTEN/NOTIFY、advisory lock等）に依存するテストは `@pytest.mark.postgres` を付け、SQLiteでは飛ばす。CIの結合テストでは `YORIKI_TEST_DATABASE=postgres` を設定し、PostgreSQLで全件を実行する（手元で確かめるときは `deploy/compose.dev.yaml` のPostgreSQLを使う）。
- pytestは `app.settings_test` を使う。`.envrc` の `DJANGO_SETTINGS_MODULE` より優先させるため、`pyproject.toml` の `addopts` に `--ds` で指定している。PostgreSQLで実行するときはテスト用DBを `--reuse-db` で使い回すので、マイグレーションを変えたら `--create-db` を付けて作り直す。
- settingsの読み込みに環境変数（`YORIKI_SECRET_KEY` 等）が要るので、mypy（django-stubs）とpytestは `.envrc` を読み込んだ状態で実行する。

### 3.2 フロントエンド（`frontend/`）

| 種類 | ツール | コマンド |
|---|---|---|
| lint | ESLint（`@nuxt/eslint`＋`eslint-config-prettier`） | `pnpm lint` |
| 整形 | Prettier（`semi: false`、`singleQuote: true`、`printWidth: 120`） | `pnpm format`（確認だけなら `pnpm format:check`） |
| 型チェック | vue-tsc | `pnpm typecheck` |
| 単体テスト | Vitest（`@nuxt/test-utils`） | `pnpm test` |
| E2E | Playwright | `pnpm test:e2e` |

- ESLintは不具合につながる書き方の検出だけを担当し、見た目の整形はPrettierに任せる（`eslint-config-prettier` で整形系の規則を外している）。
- `any` は使わない。どうしても必要な場合は、理由をコメントに書いて `// eslint-disable-next-line` で1行だけ外す。
- 単体テスト（Vitest）とE2E（Playwright）は、最初のテストを書くときに導入する。
- 単体テストは、`composables/`（`createSubmit`、`getValidationError`、日付の変換等）と `D*` ラッパーの値の受け渡しを対象にする。
- E2Eは、ログイン、会話の送信、Runの停止・回答など、主要な流れだけに絞る。APIはテスト用のバックエンドを起動して使い、モックで置き換えない。
- `D*` ラッパーを変更したら、`/dev/inputs` で通常の表示とエラーの表示も目で確認する（frontend-conventions.md §7.3）。

### 3.3 APIの型の同期

Serializerを変更したら、OpenAPIスキーマとフロントエンドの型を生成し直し、同じPull Requestに含める。

```bash
cd api && uv run python src/manage.py spectacular --file ../frontend/schema.yaml --validate --fail-on-warn
cd frontend && pnpm gen:api
```

### 3.4 Sandbox（`sandbox/`）

- イメージのビルドと契約テスト（`sandbox/tests/`）を通す。
- 契約（`sandbox/CONTRACT.md`）を変えたら、イメージのラベル `org.yoriki.sandbox-contract` の番号と、Runnerの対応番号を同じPull Requestで上げる。

## 4 CI

GitHub Actionsで、Pull Requestと `main` へのpushごとに実行する。変更されたディレクトリに関係するjobだけを動かす。

| job | 対象の変更 | 内容 |
|---|---|---|
| backend | `api/**` | ruff check、ruff format --check、mypy、pytest（PostgreSQLのserviceコンテナ、`YORIKI_TEST_DATABASE=postgres`）、`makemigrations --check --dry-run`、`spectacular --validate --fail-on-warn` |
| frontend | `frontend/**` | lint、format:check、typecheck、vitest、`nuxt build` |
| api-types | `api/**`、`frontend/**` | スキーマとフロントエンドの型を生成し、コミット済みのものと差分がないこと（`git diff --exit-code`） |
| e2e | `api/**`、`frontend/**` | Composeでバックエンドとフロントエンドを起動し、Playwrightを実行（`main` へのpushと、ラベルを付けたPull Requestだけ） |
| sandbox | `sandbox/**`、毎週の定期実行 | イメージのビルド、契約テスト、脆弱性スキャン。`main` ではDocker Hubへ公開（deployment.md §2.1） |
| app-images | `main` へのpush | `yoriki-api`、`yoriki-frontend` のイメージをビルドしてレジストリへpush |

- 依存の更新はDependabot（またはRenovate）でPull Requestにし、CIを通してからマージする。npmは公開から日の浅い版を避ける設定（pnpmの `minimumReleaseAge`）を維持する。
- CIのSecret（Docker Hubのtoken等）は、GitHubのEnvironmentに置き、`main` のjobだけが使えるようにする。

## 5 マイグレーション

- `makemigrations` で生成したファイルは、内容を読んで確認してからコミットする。
- 1つのPull Requestで作るマイグレーションは、なるべく1つのアプリにつき1ファイルにまとめる（作業中に何度も生成した場合は、マージ前に作り直す）。
- 既存データを消すマイグレーションは、ユーザー（プロジェクトの担当者）の了承を得てから作る。列の削除は、コードからの参照をなくすリリースの次のリリースで行う（deployment.md §6）。
