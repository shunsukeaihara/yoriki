# 開発の進め方とCI

ブランチ運用、コミット、レビュー、lint・テスト、CIで必ず通すチェックを定める。コードの書き方は [backend-conventions.md](backend-conventions.md)・[frontend-conventions.md](frontend-conventions.md)、配備は [deployment.md](deployment.md) を参照する。

---

## 1 ブランチとPull Request

- `main` を常にリリースできる状態に保つ。コードの変更は作業ブランチからPull Requestで取り込む。文書だけの変更は内容による：`docs/status.md` の状況の更新や誤字の修正のように判断を含まないものは `main` へ直接コミットしてよい。design.md や規約の、方針や設計の判断を変える変更は、読んで判断してもらうためにPull Requestにする。迷ったら相談する。
- 作業ブランチの名前は `<種類>/<内容>` にする（例：`feature/run-cancel`、`fix/autocomplete-scroll`、`docs/deployment`）。
- Pull Requestは1つの目的に絞る。バックエンド・フロントエンド・Sandboxをまたぐ変更でも、1つの機能なら1つのPull Requestにしてよい（モノレポにした理由）。
- マージは、Pull Requestのコミットの中身を見て、squash mergeか通常のmerge（merge commit）かを選ぶ。rebase mergeは使わない。
  - **squash merge（既定）**：レビューへの対応、途中でやめた方針とその取り消し、作業途中のコミット（`wip`、`fix typo` 等）を含む場合。1つの機能として1コミットにまとめた方が、`main` の履歴が読みやすいとき。まとめたコミットのメッセージは、取り込む変更の最終的な内容に合わせて書き直す（途中でやめた方針は書かない）。
  - **通常のmerge**：どのコミットも独立した意味のある単位で、それぞれでテストが通り、メッセージも履歴に残す価値がある場合（例：依存の更新、リネーム、機能の追加を順に積み、後で個別に戻す可能性がある）。迷ったらsquash mergeにする。
- CIのチェック（§4）がすべて通り、レビューで承認されたものだけをマージする。コーディングエージェントはPull Requestをユーザーに依頼されてから作り、マージはユーザーが指示したときに限り行う（AGENTS.md）。

## 2 コミットとPull Requestの書き方

- コミットメッセージは英語で、`<種類>: <要約>` の形にする（Conventional Commits）。種類は `feat`、`fix`、`docs`、`refactor`、`test`、`chore`、`ci`。
  - 例：`feat: add cancel action for runs`
- CIを動かす必要のないコミットは、メッセージの本文の最後の行に `[skip ci]` を書く（GitHub Actionsは、pushとPull Requestの先頭のコミットのメッセージにこれがあると、workflowを動かさない）。使ってよい場合と使わない場合は §4.1 に従う。
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
| テスト | pytest＋pytest-django＋pytest-xdist＋factory_boy | `uv run pytest` |

- pylint（pylint_django）・autopep8・black・yapfは併用せず、ruffに一本化する。
- 日本語のコメントや文字列に全角の記号（「（」「：」等）を使うので、ruffの `RUF001`〜`RUF003` は無効にしている。
- mypyの設定は、`api/` の雛形を作るときに `api/pyproject.toml` に置く（`plugins = ["mypy_django_plugin.main", "mypy_drf_plugin.main"]`、`[tool.django-stubs] django_settings_module = "app.settings"`）。
- lintの警告は、`# noqa` で抑止せずにコードを直して解消する。抑止する場合は理由をコメントに書く（backend-conventions.md §10）。
- テストはSQLiteではなくPostgreSQLで実行する（`select_for_update`、LISTEN/NOTIFY、advisory lock等、PostgreSQLの機能に依存するため）。ローカルでは `deploy/compose.dev.yaml` のPostgreSQLを使う。
- PostgreSQLに接続できないときは、pytestは接続を長く待たずに、起動のコマンド（`docker compose -f deploy/compose.dev.yaml up -d`）を案内して終わる（`conftest.py`）。
- pytestは `app.settings_test` を使う。`.envrc` の `DJANGO_SETTINGS_MODULE` より優先させるため、`pyproject.toml` の `addopts` に `--ds` で指定している。テスト用DBは `--reuse-db` で使い回すので、マイグレーションを変えたら `uv run pytest --create-db` で作り直す。
- pytestは、pytest-xdistでCPUの数だけのプロセスに分けて並列に動かす（`addopts` の `-n auto --maxprocesses 8 --dist worksteal`。プロセスごとに接続プールを持つので、PostgreSQLの接続の上限を超えないように8までにする）。テスト用DBはプロセスごとに作る（`test_<DB名>_gw0` 等）。1つのテストを `--pdb` で調べるとき等、並列にしたくないときは `-n 0` を付ける。
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
- 単体テスト（Vitest）は導入済み（書き方は frontend-conventions.md §13）。E2E（Playwright）は、最初のテストを書くときに導入する。
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

- イメージのビルドと契約テスト（`sandbox/tests/`）、`ruff check`・`ruff format` を通す（`docker build -f sandbox/images/docs/Dockerfile -t yoriki-sandbox-docs:dev sandbox`、`cd sandbox && uv run pytest`。sandbox/README.md）。
- 契約（`sandbox/CONTRACT.md`）を変えたら、イメージのラベル `org.yoriki.sandbox-contract` の番号と、Runnerの対応番号を同じPull Requestで上げる。

## 4 CI

GitHub Actionsで、Pull Requestと `main` へのpushごとに実行する。変更されたディレクトリに関係するjobだけを動かす（workflowごとに `paths` で絞る）。workflowは `.github/workflows/` に置く。

- ツールの版は、ローカルと同じく `mise.toml` から入れる（`jdx/mise-action`）。
- 使うActionはタグではなくコミットのSHAで固定し、横にタグをコメントで書く。公開から日の浅い版（目安として2週間以内）は使わない。
- workflowの権限は `permissions: contents: read` を基本にし、必要なjobだけに広げる。

| job | 対象の変更 | 内容 |
|---|---|---|
| backend（`backend.yml`） | `api/**` | ruff check、ruff format --check、mypy、pytest（PostgreSQLのserviceコンテナ）、`makemigrations --check --dry-run`、`spectacular --validate --fail-on-warn` |
| frontend（`frontend.yml`） | `frontend/**` | lint、format:check、typecheck、vitest、`nuxt build` |
| api-types（`api-types.yml`） | `api/**`、`frontend/**` | スキーマとフロントエンドの型を生成し、コミット済みのものと差分がないこと（`git diff --exit-code`） |
| app-images（`app-images.yml`） | Dockerfile、依存のlock、`deploy/**` | 本体のイメージをビルドし、`deploy/compose.yaml` を起動して疎通を確認する（`/api/ping/`、画面、API、静的ファイル、セキュリティのヘッダー）。レジストリへのpushは、使うレジストリを決めてから追加する。コードだけの変更では動かさない |
| e2e（未） | `api/**`、`frontend/**` | Composeでバックエンドとフロントエンドを起動し、Playwrightを実行（`main` へのpushと、ラベルを付けたPull Requestだけ） |
| sandbox | `sandbox/**`、毎週の定期実行 | lint、イメージのビルド、契約テスト。脆弱性スキャンと、`main` でのDocker Hubへの公開（deployment.md §2.1）は、公開先のorgを決めてから加える |

- 依存の更新はDependabot（`.github/dependabot.yml`。GitHub Actions、uv、npm、Dockerfileのベースイメージ、Composeの公式イメージ）でPull Requestにし、CIを通してからマージする。公開から日の浅い版を避けるため、Dependabotは `cooldown` を14日にし、npmはpnpmの `minimumReleaseAge` も維持する。
- CIのSecret（Docker Hubのtoken等）は、GitHubのEnvironmentに置き、`main` のjobだけが使えるようにする。

### 4.1 `[skip ci]` の使い方

CIは、変更されたディレクトリに関係するworkflowだけが動く（`paths`）。それでも同じ確認が重なる場合は、コミットメッセージに `[skip ci]` を書いて飛ばす。

| 場面 | `[skip ci]` |
|---|---|
| Pull Requestの途中のpushで、すぐ後に続けてpushする（作業途中、細かい修正をまとめて送る前） | 付けてよい |
| レビューを依頼する前・マージする前の最後のpush | 付けない。Pull Requestの最後のコミットで、必ずCIを通す |
| squash merge・mergeで `main` に入るコミット。Pull Requestの最後のコミットでCIが通っていて、その後 `main` に他の変更が入っていない（ブランチが `main` の最新を含んでいる） | 付ける（同じ内容の確認を `main` で繰り返さない） |
| 同上で、Pull RequestのCIの後に `main` が進んでいる | 付けない（`main` との組み合わせを `main` で確認する） |
| `main` へ直接入れるコミット（原則しない） | 付けない |
| Dependabotの更新 | 付けない |

- `docs/` だけの変更など、どのworkflowの `paths` にも当たらない変更では、CIはもともと動かないので付けなくてよい。
- `[skip ci]` で飛ばしたコミットが最後に残ったままマージしない。必要なら空のコミット（`git commit --allow-empty`）を足して、CIを動かす。

## 5 マイグレーション

- `makemigrations` で生成したファイルは、内容を読んで確認してからコミットする。
- 1つのPull Requestで作るマイグレーションは、なるべく1つのアプリにつき1ファイルにまとめる（作業中に何度も生成した場合は、マージ前に作り直す）。
- 既存データを消すマイグレーションは、ユーザー（プロジェクトの担当者）の了承を得てから作る。列の削除は、コードからの参照をなくすリリースの次のリリースで行う（deployment.md §6）。

## 6 確認用の道具（`api/src/devtools/`）

画面やRunを手元で確かめるための管理コマンド。`YORIKI_DEBUG=true` のときだけ動く（本番の設定では止まる）。テストでは使わない（テストはfactoryと `harness.tests.fakes.FakeAdapter` を使う）。

| コマンド | 内容 |
|---|---|
| `seed_dev` | 確認用のデータを入れる（§6.1） |
| `fake_llm` | 確認用の偽のLLM（OpenAI互換）を起動する（§6.2） |
| `dev_session <ユーザー>` | ログイン済みのセッションを作り、`sessionid=…` の形でCookieを表示する（Playwright等で画面を確かめるとき） |

手元で会話のRunを一通り動かす手順：

```bash
cd api
uv run python src/manage.py seed_dev --sandbox-image localhost:5001/yoriki-sandbox-docs@sha256:<digest>
uv run python src/manage.py fake_llm                                 # 別の端末で
(cd src && uv run uvicorn app.asgi:application --port 8000)          # 別の端末で
uv run python src/manage.py qcluster                                 # 別の端末で
```

Sandboxのイメージは、手元のレジストリにpushして使う（Runtime Profileには「名前@sha256:digest」で登録する。digestは `docker push` の最後に表示される）。`seed_dev` とapiには `YORIKI_SANDBOX_INSECURE_REGISTRIES=localhost:5001` が要る。

```bash
docker run -d --name yoriki-dev-registry -p 127.0.0.1:5001:5000 registry:3   # 初めてのときだけ
docker build -f sandbox/images/docs/Dockerfile -t yoriki-sandbox-docs:dev sandbox
docker tag yoriki-sandbox-docs:dev localhost:5001/yoriki-sandbox-docs:dev
docker push localhost:5001/yoriki-sandbox-docs:dev
```

`check-member`（パスワード `yoriki-check`）でログインし、会話で「[shell] 作業領域を見て」のように送る。

### 6.1 確認用のデータ（`seed_dev`）

`seed_dev` が自分で作ったものだけを扱い、何度流しても同じ状態になる（名前で探して、なければ作り、あれば揃える）。確認の途中で変えたもの（無効にした、版を変えた、permission・所属を足した等）も元に戻す。自分で作ったものかは、作成者（`check-admin`）と、ロールの印（コメント `yoriki seed_dev`）で見分ける。同じ名前で自分で作ったものでないものがあれば、書き換えずに止まる（名前には「（seed_dev）」を付け、ほかのデータと重ならないようにしている）。`--dry-run` は、同じ処理を最後に取り消して、何をするかだけを表示する。

| 種類 | 名前 | 内容 |
|---|---|---|
| 部門 | 確認用の部門（seed_dev） | 下の3人が所属する（3人の所属は、この部門だけにする） |
| ユーザー | `check-member` | 確認用の社員。permissionなし |
| | `check-lead` | 確認用の部門の担当。部門のSkillの編集・公開、部門のタスクの管理 |
| | `check-admin` | 確認用の全社の担当。すべての部門の管理、全社のSkill・タスク、Connector・接続先、入口・出口の管理。確認用のデータの作成者 |
| LLMの接続 | 偽LLM（seed_dev） | ローカルLLM。`--llm-url`（既定 `http://127.0.0.1:8765/v1`。`fake_llm` の既定）。接続先は作った後に変えないので、`--llm-url` を変えると前の接続を消して新しい接続を作る |
| | つながらないLLM（seed_dev） | `http://127.0.0.1:9/v1`。再試行の後のモデルの切り替えの確認に使う |
| モデル設定 | 偽モデル（seed_dev） | `fake`。Tool呼出し・画像入力。既定のモデル設定がなければ既定にする |
| | 偽モデル・推論（seed_dev） | `fake-reasoning`。effortは low・medium・high |
| | つながらないモデル（seed_dev） | つながらない接続を使う |
| Runtime Profile | 文書処理（seed_dev） | `--sandbox-image` を指定したときだけ。管理画面と同じくレジストリで確かめる（手元のレジストリは `YORIKI_SANDBOX_INSECURE_REGISTRIES=localhost:5001` が要る）。資源の上限は、管理画面で変えた値を残す。既定のRuntime Profileがなければ既定にする |
| スキーマ | `devtools_check` | YorikiのDBの中に作る。表 `vendors`（取引先3件）・`invoices`（請求3件）は毎回作り直す。2つの表だけを読むロール `devtools_check_ro` を作る（PostgreSQLのクラスタで共有。DBの利用者にロールを作る権限が要る。`deploy/compose.dev.yaml` のものは持っている） |
| Connector | 確認用DB（seed_dev） | 全社。上のスキーマの2つの表を公開する。接続先は「確認用DBの接続（seed_dev）」。`--skip-database` でスキーマと一緒に飛ばす |

- パスワード（`--password`。既定 `yoriki-check`）は、SSOの設定がない手元では、パスワードでのログインにそのまま使える。違うときだけ設定し直す（作ったセッションが切れないように）。
- API key・DBのパスワードは、そのときの `YORIKI_SECRET_ENCRYPTION_KEYS` で暗号化して保存する。apiと同じ `.envrc` を読み込んで流す。
- 基盤管理者（`createsuperuser` で作る）は作らない。

### 6.2 偽のLLM（`fake_llm`）

OpenAI互換の `POST /v1/chat/completions`（ストリーミングだけ）と `GET /v1/models` を返す。どう応答するかは、台本（JSON）のルールで決める。既定の台本は `api/src/devtools/scenarios/default.json`。`--scenario` で自分の台本を、`--dump <ディレクトリ>` で受けた要求の書き出し（system promptやToolの定義を確かめるとき）を指定する。台本は要求ごとに読み直す（更新日時が変わったときだけ）ので、起動したまま書き換えられる。受けた要求ごとに、どのルールに合ったかを1行表示する。

既定の台本は、発言に次の語があると、その動きをする（ないときは、発言の1行目を返すだけ）。

| 語 | 動き |
|---|---|
| `[skill]` | 一覧の最初のSkillを読み込み、資料があれば最初の1つを `read` してから答える |
| `[skill-edit]` | `request_skill_edit` で一覧の最初のSkillの編集を提案する。承認した後の編集のRunでは、`get_skill` → `update_skill_draft`（説明を直す）→ `publish_skill_draft` |
| `[shell]` | `shell` で作業領域を見て、結果を答える |
| `[file]` | `write` で `/workspace/output/result.md` を書く |
| `[ask]` | `ask_user` で対象月を尋ね、回答を受けて答える |
| `[todo]` | `todo_write` で作業の計画を書く |
| `[query]` | `datasource_query` で、使えるConnectorの最初の1つの `vendors` を照会する（会話では `@` で「確認用DB（seed_dev）」を指定する） |
| `[script]` | `shell` のスクリプトから `yoriki_connector` で「確認用DB（seed_dev）」を照会する（会話では `@` で指定する） |
| `[notify]` | `prepare_notification` で通知を用意し、結果を答える（タスクの依頼文に書く）。業務結果のSchemaのあるタスクでは先に `submit_result` が要り、引数がSchemaで決まるので、台本を書く |
| `[slow]` | 2秒待ってから、少しずつ答える |
| `[error]`・`[429]` | 同じ要求に、最初の2回は500、1回は429を返す（Runnerの再試行の確認） |

台本の形：

```json
{
  "models": ["fake", "fake-reasoning"],
  "rules": [
    {
      "name": "資料を読む",
      "when": {"prompt": "[skill]", "called": "skill", "match": "(?P<file>/workspace/master/[^\\n]+?) \\("},
      "reply": {"text": "資料を読みます。", "tool_calls": [{"name": "read", "arguments": {"path": "${file}"}}]}
    }
  ],
  "default": {"text": "（偽のLLM）「${prompt}」を受け取りました。"}
}
```

- ルールは上から順に照らし、`when` の条件がすべて合った最初のルールの `reply` を返す。どれにも合わなければ `default`。
- 「今の依頼」は、Toolを呼ばずに終わった最後のassistantの発言（前の依頼への答え）より後の部分。Runnerが利用者の発言の形で足す文脈（`<runtime_context>`、ジョブの終了の知らせ等）も、今の依頼の発言に含める。前の依頼が答えずに終わった（失敗・取消）ときは、その依頼も今の依頼に含まれる。
- `when`：`prompt`（今の依頼の利用者の発言に、すべての語を含む）、`step`（今の依頼の中のassistantの発言の数。0が最初。配列ならどれか）、`tools`（すべてのToolが使える）、`called`（直前の発言が呼んだToolの名前。配列ならどれか。呼んでいなければ `""`）、`result`（最後のToolの結果に、すべての語を含む）、`system`（system promptに、すべての語を含む）、`model`（モデル名。配列ならどれか）、`match`（今の依頼のすべての文に対する正規表現。名前付きのグループは変数になる）。語は文字列か配列。
- `reply`：`text`、`tool_calls`（`{"name", "arguments"}` の配列）、`finish`（`stop`・`length`・`tool_calls`。省くと、Toolを呼ぶなら `tool_calls`、ほかは `stop`）、`delay`（最初に待つ秒数）、`chunk_delay`（20文字ずつに分けて送るときの間の秒数）、`status`・`error`（HTTPのエラーの状態と文）。
- ルールの `times`：同じ要求（モデルと発言の並びが同じ）に、最初の何回だけ合うか。Runnerの再試行の確認に使う。台本を読み直すと数え直す。
- `${名前}` は文字列の中で置き換える。`prompt`（今の依頼の最初の利用者の発言の1行目。100文字まで）、`step`、`called`、`result`（最後のToolの結果。1000文字まで）、`model`、`enum:<Tool>.<引数>`（Toolの引数のenumの最初の値。例：`${enum:skill.name}`、`${enum:datasource_query.connector}`）と、`match` のグループ。知らない名前はそのまま残す（シェルの `${HOME}` 等を書ける）。
- 推論の途中経過は返さない。`tool_choice` で指定されたToolも自動では呼ばないので、必要ならルールに書く（要求の `tool_choice` は `--dump` で確かめる）。
