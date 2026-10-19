# 開発の状況と次の作業

作業を引き継ぐための文書。何ができていて、何が決まっていて、次に何をするかをまとめる。作業の区切りごとに更新する。

- 最終更新：2026-10-20（#11）
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
| #11 | `llm` アプリ：LLMConnection（API keyは暗号化してDBに保存。種別とbase URLは作成後は変えられない）、ModelProfile・ModelProfileVersion（不変の版）、Provider Adapter（OpenAI、Anthropic、Gemini、ローカルLLM。ストリーミング、Tool呼出し、利用量、エラーの共通化）、管理API（`v1/llm_connections`、`v1/model_profiles`、接続の確認）、Secretの暗号化（`utils/crypto.py`、`rotate_secret_encryption`） |

確認済みのこと：

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
- 管理画面のメニューは、まだ項目が1つもない（`composables/manage.ts`）。最初の項目（LLMの接続）を足すまで、基盤管理者も `/manage` には入れない。
- CI：e2e、sandbox、本体のイメージのレジストリへのpush（使うレジストリが未決。design.md §13）。
- Compose：channel-listener（Channelの受信を実装するとき）。
- 管理コマンド：`drain_workers`・`resume_workers`（Workerの実行を実装するとき）。
- 業務のアプリ（`agent`、`llm`、`conversation`、`run`、`stream`、`skill`、`plugin`、`artifact`、`runtime`、`task`、`delivery`、`connector`、`usage` 等。backend-conventions.md §1.2）は、まだ1つもない。
- `sandbox/`（CONTRACT.md、イメージ、契約テスト）は、まだない。

## 4 次の作業

design.md §12の第1段階を、1つの目的ごとに1つのPull Requestにして進める。提案する順番：

1. **LLMの管理画面**：`/manage` でプロバイダ接続（種別、API key、base URL、上限）とモデル設定（版の作成と履歴）を登録・変更する画面。接続の確認（`check`）のボタン。`composables/manage.ts` にメニューの最初の項目を足す（permissionは `llm.change_llmconnection` 等。backend-conventions.md §6.3）。
2. **認証の方式の見直し**（design.md §10.0 を直してから実装する）：
   - SSOの設定（認証基盤の種別、client ID、client secret（暗号化）、テナント、許可するドメイン）を、環境変数ではなく管理画面で行う（django-allauthの `SocialApp` に合わせ、secretは復号して渡す）。
   - SSOを設定したら、社員はSSOだけでログインする。SSOがなければ、Yoriki自身のアカウントとパスワードでログインする（基盤管理者がアカウントを作り、招待のリンクから本人がパスワードを設定する。試行回数の制限）。
   - 基盤管理者のパスワードでのログインは、SSOの設定の誤りやIdPの停止に備えて残す。
3. **Agent（`agent`）**：Agent、AgentRevision（不変の版）、管理画面と、社員がAgentを選ぶ画面。
4. **会話と実行（`conversation`、`run`、`stream`）**：Conversation・ConversationMessage、Runの状態遷移（遷移表と行ロック）、claim / lease、`cancel`・`answer`、SSE（`stream/notifier.py` のLISTEN/NOTIFY）。まずSandboxなしでLLMと会話できるところまで。
5. **Sandbox（`sandbox/`、`runtime`）**：CONTRACT.md、docsのイメージ、Launcher（`network_mode: none`、コンテナからの脱出への対策の1〜4。design.md §7）、Runnerからのshell実行。gVisorの評価もこの段階で行う。
6. **成果物とファイル（`artifact`）**：Local・S3のStorage Backend、添付、成果物の取得（`/api/files/<token>/`、`X-Accel-Redirect`）。
7. **Skill（`skill`、`plugin`）**：Draft・Version・公開、会話からのSkill更新。
8. **Connector（`connector`）とタスク・配信（`task`、`delivery`）**：読取り専用のDB Connector、Webhook・Zapierの通知、Trigger。DB Connector（種別：PostgreSQL・MySQL・SQL Server、ホスト、DB名、ユーザー、パスワード等）とHTTP Connector（ホスト、ヘッダー、認証情報等）の設定は管理画面で登録し、資格情報は `utils/crypto.py` で暗号化してDBに保存する（backend-conventions.md §6.4）。
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

- 環境変数はリポジトリルートの `.envrc`（direnv）。テストやmypyは、`.envrc` を読み込んだ状態で実行する。
- 変更前に、AGENTS.md と、触る範囲の規約を読む。作業の進め方（ブランチ、コミット、Pull Request、CI、マージ）は development.md に従う。
- 作業の区切りでは、本書の §1・§3・§4 を更新する。
