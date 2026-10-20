# 開発の状況と次の作業

作業を引き継ぐための文書。何ができていて、何が決まっていて、次に何をするかをまとめる。作業の区切りごとに更新する。

- 最終更新：2026-10-20（LLMの管理画面。`feature/llm-manage-ui`、PR作成前）
- 何を作るかは [design.md](design.md)（実装の順番は §12）、どう書くかは各規約（[backend-conventions.md](backend-conventions.md)、[frontend-conventions.md](frontend-conventions.md)、[development.md](development.md)、[deployment.md](deployment.md)）を正とする。本書と食い違う場合は、それらを優先する。

---

## 1 できているもの

| PR | 内容 |
|---|---|
| （初期） | 設計書と規約、フロントエンドの雛形（Nuxt 4、Nuxt UI、i18n）、入力・ダイアログ・表の `D*` ラッパー（`/dev/inputs` で確認できる）、APIクライアント（`app/api/client.ts`、`resource.ts`） |
| #1 | `api/` の雛形（Django 6.1、DRF、drf-spectacular、django-allauth、Django Q2、psycopg 3の接続プール）、`utils/`、`user` アプリ、社内SSO（Microsoft 365・Google Workspace）、`app/v1/auth/session/`・`auth/providers/`・`my/`、フロントエンドのログイン画面・ログイン状態・権限の判定 |
| #2 | GitHub Actions（backend、frontend、api-types）、Dependabot |
| #4 | `[skip ci]` の使い方（development.md §4.1） |
| #5 | Node.js 26 |
| #6 | Dockerfile（api、frontend）、Compose一式（単一ホスト、アプリ、DB、Worker、Cloudflare Tunnel、エッジ）、nginxの設定、環境変数のexample、app-imagesのCI（イメージのビルドと起動後の疎通確認） |
| #8 | フロントエンドの土台の残り：`plugins/error-handler.ts`、`error.vue`、`layouts/manage.vue`（`useManageMenu()` のメニューを権限で出し分け、表示できる項目がなければ `/manage` は403）、Vitest（`test/nuxt/`。composables・APIクライアント・`D*` ラッパーの最初のテスト）とCIへの追加 |
| #9 | 開発時（DEBUG）の静的ファイルを `StaticFilesStorage` にし、Djangoの管理画面が500になる不具合を修正 |
| #12 | `llm` アプリ：LLMConnection（種別はOpenAI・Anthropic・Gemini・ローカルLLM。API keyは `EncryptedSecretField` で暗号化してDBに保存。種別とbase URLは作成後は変えられない）、ModelProfile（中身を変えると不変の版 ModelProfileVersion が自動で増える）、Provider Adapter（各社のAPIクライアントでストリーミング・Tool呼出し・利用量・エラーを共通化）、管理API（`v1/llm_connections`（`check` で接続の確認）、`v1/model_profiles`、版の履歴）、Secretの暗号化（`utils/crypto.py`、`utils.fields.EncryptedSecretField`、`common` の `rotate_secret_encryption`）。各社のAgent SDKは使わず、自前のハーネスで動かす決定（design.md §4.8） |
| （PR作成前） | LLMの管理画面：`/manage/llm_connections`（接続の登録・API keyの差し替え・接続の確認。種別の選択の近くにローカルLLMを推奨する案内）、`/manage/model_profiles`（モデル設定。中身を変えると新しい版、版の履歴、編集中の競合（409）からの読み直し、接続先のモデル名からの選択）。管理画面のメニューの最初の項目（permissionは `llm.change_llmconnection`・`llm.change_modelprofile`）。共通部品 `ListPageTitle`・`TableSearchForm`・`formatDatetime`。API側は接続とモデル設定の一覧に名前の検索（`search`）を追加 |

確認済みのこと：

- #12 のLLMのAPIは、テストで各社の応答を差し替えて確かめた。実際のプロバイダのAPI（API keyが要る）ではまだ試していない。

- LLMの管理画面は、開発サーバーでブラウザ（Playwright）から一通り操作して確かめた（追加・更新・接続の確認の失敗・版の履歴・409の競合・使用中の接続の削除の拒否）。実際のプロバイダでの接続の確認は、まだ試していない。

- 単一ホストの構成（`deploy/compose.yaml`）を手元とCIで起動し、ログイン画面の表示、API、静的ファイル、セキュリティのヘッダー、SSOの開始（リダイレクトURIが `https://`）まで動く。
- 実際のIdP（Entra ID・Google Workspace）でのログインは、まだ試していない（client IDの発行が要る）。

## 2 実装の中で決めたこと

設計書・規約に反映済み。経緯を知らずに変えないよう、要点だけを挙げる。

- **権限**：「permissionを定義する → Groupに割り当てる → Groupをユーザーに割り当てる」で扱う。permissionは `utils.permissions.AppPermission` に1か所でまとめ（codenameは `can_<動詞>_<対象>`）、Modelの `Meta.permissions` で作る。役割（業務管理者・Plugin公開者）はGroupで、初期の中身は `user/roles.py`（`migrate` のたびに同期）。Userのフラグや役割の名前では判定しない。基盤管理者は `is_superuser`。フロントエンドも `allPermissions` だけで判定する（backend-conventions.md §6.3、frontend-conventions.md §6）。
- **パスワードでのログイン**：基盤管理者だけ（`utils.backends.PlatformAdminPasswordBackend`）。社員はSSOだけ（`SOCIALACCOUNT_ONLY`）。
- **SSOのアカウント確認**：許可したメールドメイン、Microsoftはテナントを固定、Googleは `hd` の一致が必須（未設定なら起動しない）。
- **テスト**：ローカルでもCIでもPostgreSQLで実行する（SQLiteは使わない）。PostgreSQLが起動していなければ、pytestは起動のコマンドを案内して止まる。NOTIFY/LISTENも実際のPostgreSQLで確かめる。
- **静的ファイル**：本番はnginxが `/api/static/` で返す（`static` サービスが共有ボリュームへコピー）。開発時はUvicornが返す。WhiteNoiseは使わない。
- **CSP**：NuxtのSPAがインラインのスクリプトを含むため、`script-src 'unsafe-inline'` を許可している（課題。§3）。
- **CI**：変更したディレクトリに関係するworkflowだけを動かす。Actionはコミットのsha、公式イメージはdigestで固定し、公開から2週間以内の版は使わない。
- **マージ**：squash mergeを基本にし、コミットを残す価値があるときだけ通常のmerge。`main` に入るコミットには、条件を満たせば `[skip ci]` を付ける（development.md §1、§4.1）。

## 3 まだないもの・課題

- CSPのインラインのスクリプトを、ハッシュかnonceで絞る（deployment.md §4.1）。
- フロントエンド：`app.config.ts`（テーマの色。primary / neutralを決める）、E2E（Playwright）。
- CI：e2e、sandbox、本体のイメージのレジストリへのpush（使うレジストリが未決。design.md §13）。
- Compose：channel-listener（Channelの受信を実装するとき）。
- 管理コマンド：`drain_workers`・`resume_workers`（Workerの実行を実装するとき）。
- 業務のアプリは `llm` だけ。`agent`、`conversation`、`run`、`stream`、`skill`、`plugin`、`artifact`、`runtime`、`task`、`delivery`、`connector`、`usage` 等（backend-conventions.md §1.2）は、まだない。
- Provider Adapterは、実際のプロバイダのAPIではまだ試していない（API keyが要る）。テストは各社の応答を差し替えて確かめている。
- 接続の同時実行数・1分あたりの要求数の上限は、項目があるだけで、まだ守らせていない（Runnerの段階）。
- `sandbox/`（CONTRACT.md、イメージ、契約テスト）は、まだない。

## 4 次の作業

design.md §12の第1段階を、1つの目的ごとに1つのPull Requestにして進める。提案する順番：

1. **認証の方式の見直し**（design.md §10.0 を直してから実装する）：
   - SSOの設定（認証基盤の種別、client ID、client secret（暗号化）、テナント、許可するドメイン）を、環境変数ではなく管理画面で行う（django-allauthの `SocialApp` に合わせ、secretは復号して渡す）。
   - SSOを設定したら、社員はSSOだけでログインする。SSOがなければ、Yoriki自身のアカウントとパスワードでログインする（基盤管理者がアカウントを作り、招待のリンクから本人がパスワードを設定する。試行回数の制限）。
   - 基盤管理者のパスワードでのログインは、SSOの設定の誤りやIdPの停止に備えて残す。
2. **Agent（`agent`）**：Agent、AgentRevision（不変の版）、管理画面と、社員がAgentを選ぶ画面。
3. **ハーネスの調査と設計（`docs/harness.md`）**：各社のAgent SDKやCLI製品は使わず、Provider Adapterの上にYoriki自身のRunnerを置く（design.md §4.8）。実装の前に、公開されているハーネスをリポジトリの外（例：`~/work/yoriki-reference/`。gitに入れない）にcloneして設計思想を読み、業務ツールとしてのハーネスをYoriki独自に設計する。読むもの：
   - **DeepSeek Harness**（`deepseek-ai/deepseek-harness`）：追記専用のセッションログから毎回モデルへの入力を組み立てる規約（RunEventを正本にするYorikiの形と同じ）、常駐ターミナル・バックグラウンドジョブ・長い出力の退避、fsとsubprocessのproviderをリモートのsandboxへ向ける構成、compaction・guard、deliverables（成果物）。developer previewで互換性が変わるので依存はしない。
   - **OpenCode**（`anomalyco/opencode`）：Toolの一式（`bash`、`read`、`edit`、`write`、`apply_patch`、`grep`、`glob`、`skill`、`question`）と、Toolごとの `allow` / `ask` / `deny` の権限、`plan`（読み取り専用）と `build` のエージェントの分け方。
   - **Codex CLI**：system promptの構成、Toolの説明文、`apply_patch` 形式、Sandbox前提のコマンド実行。
   - **Gemini CLI**：文脈の圧縮の手順、チェックポイント。
   - **OpenHands**：コンテナ内の実行サーバー（常駐bash、cwd・環境変数の保持）と、Action／Observationの型。Sandboxの契約（CONTRACT.md）の下敷き。
   - **SWE-agent / mini-swe-agent**：Tool（ACI）の設計原則と、最小のループ。
   - Claude Codeは非OSSなので、Agent SDKの文書と観察できる挙動（Toolの役割分担、Bashの説明文、質問の仕方）を参考にする。
   設計では、土台のコーディングエージェント（ファイルの読み書き・編集・検索、常駐シェル、バックグラウンド実行、出力の退避、圧縮、エラーからの立て直し）と、業務の層（Skill、Connector、成果物、確認待ち、予算、権限）を分けて書き、最初の2つのAgent（請求書の照合、契約書レビュー）でどのToolとSkillをどう使うかを書き下して確かめる。GNU toolsやPythonを含む大量のコマンドがSandboxの中で確実に動くことを要件に入れる。
4. **会話と実行（`conversation`、`run`、`stream`）**：Conversation・ConversationMessage、Runの状態遷移（遷移表と行ロック）、claim / lease、`cancel`・`answer`、SSE（`stream/notifier.py` のLISTEN/NOTIFY）。まずSandboxなしでLLMと会話できるところまで。会話を始めるときのウィザードで、個人情報や機密情報を扱うかを確かめ、扱う場合はローカルLLMのモデルだけに絞る（design.md §4冒頭、§11.3）。
5. **Sandbox（`sandbox/`、`runtime`）**：CONTRACT.md、docsのイメージ、Launcher（`network_mode: none`、コンテナからの脱出への対策の1〜4。design.md §7）、Runnerからのshell実行。gVisorの評価もこの段階で行う。
6. **成果物とファイル（`artifact`）**：Local・S3のStorage Backend、添付、成果物の取得（`/api/files/<token>/`、`X-Accel-Redirect`）。
7. **Skill（`skill`、`plugin`）**：Draft・Version・公開、会話からのSkill更新。
8. **Connector（`connector`）とタスク・配信（`task`、`delivery`）**：読取り専用のDB Connector、Webhook・Zapierの通知、Trigger。DB Connector（種別：PostgreSQL・MySQL・SQL Server、ホスト、DB名、ユーザー、パスワード等）とHTTP Connector（ホスト、ヘッダー、認証情報等）の設定は管理画面で登録し、資格情報は `utils/crypto.py` で暗号化してDBに保存する（backend-conventions.md §6.4）。個人情報を主な中身とする接続先には機密のフラグを付けられるようにし、フラグの付いたConnectorはローカルLLMのモデルを使うタスク・会話にだけ割り当てる（design.md §4冒頭、§4.9.1）。
9. **最初の2つのAgent**（design.md §13）：請求書PDF → 社内DBと照合 → 取込用のCSV、契約書レビュー → Zapierへ結果。
10. **Channel Plugin**（Teams・Slack・Discord）。

並行してよいもの：実際のIdPでのログインの確認（client IDが用意でき次第）、CSPの改善。

## 5 作業を始めるときの手順

```bash
mise install
docker compose -f deploy/compose.dev.yaml up -d   # PostgreSQL
cd api && uv sync && uv run python src/manage.py migrate
cd frontend && pnpm install
```

- `.envrc` に `YORIKI_SECRET_ENCRYPTION_KEYS` を設定する（`.envrc.example` の生成コマンドで作る）。設定がなくても起動はするが、LLMの接続のAPI keyを保存するときに入力エラーになる。
- 基盤管理者は `cd api && uv run python src/manage.py createsuperuser` で作る。開発時はDjangoの管理画面（`/api/admin/`）のパスワードログインでセッションを作れる。

- 環境変数はリポジトリルートの `.envrc`（direnv）。テストやmypyは、`.envrc` を読み込んだ状態で実行する。
- 変更前に、AGENTS.md と、触る範囲の規約を読む。作業の進め方（ブランチ、コミット、Pull Request、CI、マージ）は development.md に従う。
- 作業の区切りでは、本書の §1・§3・§4 を更新する。
- Pull Requestの進め方（AGENTS.md）：コードの変更は手元のブランチで作り、PRはユーザーに依頼されてから作る。マージはユーザーが行う。本書の状況の更新や誤字の修正のように判断を含まない文書の変更は、PRを作らずに `main` へ直接コミットする。design.md や規約の判断を変える変更はPRにする（development.md §1）。CIが通ったら `/codex:adversarial-review`（ユーザーが実行する）の指摘を直して報告し、マージはユーザーに任せる（squash merge。`[skip ci]` は development.md §4.1）。PRのページは消せないので、試しのPRや取り消しのPRは作らない。
- 設計の判断を変える話が出たら、コードを書く前に design.md と本書を直し、合意してから実装する。
