# Yoriki

社員が業務用のAI Agentを、安全に・共通の社内Webサービスとして使うための基盤。

- 社員はチャットで業務を依頼し、エージェントは許可された範囲でファイル処理やコードの実行を行う。業務ごとの手順や資料はSkillにまとめ、社員が `/` で指定するか、エージェントが使えるものから選ぶ。社内DBはConnectorとして `@` で指定して読める。
- 決まった業務はタスク（プロンプトのテンプレート・使うSkillの版・入力の項目）として定義し、会話を介さずに実行する。
- 管理者はLLMの接続とモデル設定、Sandboxの実行環境、Connector、ユーザーと部門、SSO、利用量を一元管理する。
- 権限はLLMの判断ではなく、サーバーと実行環境（ネットワークのないSandbox。本番はgVisor）で強制する。

> pre-alpha。設計書の第1段階（[docs/design.md](docs/design.md) §12）を実装している途中。OSSとして公開する予定。

## できていること

| 分野 | 内容 |
|---|---|
| ログインとアカウント | 社内SSO（Microsoft 365・Google Workspace。設定は管理画面で行う）、SSOのない導入先向けのYorikiのアカウント（招待・パスワード）、部門と所属、permissionによる権限（[docs/authorization.md](docs/authorization.md)） |
| LLM | 接続（OpenAI・Anthropic・Gemini・ローカルLLM。API keyは暗号化して保存）、版を持つモデル設定、各社のAPIを共通化するProvider Adapter。各社のAgent SDKは使わず、自前のハーネスで動かす |
| 会話 | 依頼・応答の逐次表示（SSE）、作業の計画と記録、確認への回答、実行中の追加の指示、次の依頼、停止、モデルとeffortの切り替え、添付と成果物 |
| ハーネスとSandbox | Run・Attempt（lease・fencing）・RunEventを正本にした実行と障害からの再開、要約の圧縮。コマンドはネットワークのないSandboxのコンテナで動かす（Runtime Profileでイメージをdigestで固定、本番はgVisor）。文書処理用のイメージ（`sandbox/images/docs`） |
| Skill | 個人・部門・全社・システムの範囲、Draftと公開・不変の版・切戻し・緊急停止、資料（MD・CSV・PPTXの検証と差分）、評価ケースによるDraftの評価、会話からのSkillの作成・更新、Skillが使うConnectorの宣言、機密のSkill |
| Connector | 社内DB（PostgreSQL・MySQL・SQL Server）の読取り。公開する表・列と上限を版で持ち、会話では `datasource_describe`・`datasource_query` で使う |
| タスク | タスクの定義と版、入力の項目、手動でのタスクのRun（APIだけ。画面はまだない） |
| ファイル | 添付・成果物・Workspaceのsnapshotの保存先（Local・S3） |
| 配備 | Dockerfile、Docker Composeの単一ホスト構成と複数台構成、nginx、Cloudflare Tunnel（[docs/deployment.md](docs/deployment.md)） |

まだないもの（Webhookでの起動、通知・配信、Teams・Slack等のChannel、Pluginの仕組み、Pythonのパッケージの追加、クオータ等）と次の作業は [docs/status.md](docs/status.md) にまとめている。

## 文書

| 文書 | 内容 |
|---|---|
| [docs/design.md](docs/design.md) | 設計企画書（要件、データモデル、実行、Skill、Connector、LLM、認証、段階的な実装計画） |
| [docs/authorization.md](docs/authorization.md) | 権限の設計（範囲、利用・業務の管理・基盤の管理の3つの層、Runの実行許可） |
| [docs/harness.md](docs/harness.md) | エージェントのハーネス（Runner）の設計（Tool、文脈の組み立て、Sandboxでの実行、圧縮、再開） |
| [docs/realtime.md](docs/realtime.md) | リアルタイム通信（SSE）の方針 |
| [docs/backend-conventions.md](docs/backend-conventions.md) / [backend-examples.md](docs/backend-examples.md) | バックエンド（Django / DRF）の規約と実装例 |
| [docs/frontend-conventions.md](docs/frontend-conventions.md) / [frontend-examples.md](docs/frontend-examples.md) | フロントエンド（Nuxt / Nuxt UI）の規約と実装例 |
| [docs/deployment.md](docs/deployment.md) | 配備（Docker Compose、Nginx、イメージ、環境変数、更新手順、公開の方法） |
| [docs/development.md](docs/development.md) | 開発の進め方（ブランチ、コミット、lint・テスト、CI） |
| [docs/status.md](docs/status.md) | 開発の状況と次の作業 |
| [sandbox/README.md](sandbox/README.md) / [CONTRACT.md](sandbox/CONTRACT.md) | Sandboxのイメージのビルド・契約テストと、Runnerとの取り決め |
| [AGENTS.md](AGENTS.md) | コーディングエージェント向けの指示 |

## 構成

| ディレクトリ | 内容 |
|---|---|
| `api/` | Django / DRF / Django Q2（API、Runner、Worker）。アプリは `user`、`llm`、`conversation`、`run`、`harness`、`stream`、`usage`、`runtime`、`artifact`、`skill`・`resources`、`connector`、`task` |
| `frontend/` | Nuxt 4 / Nuxt UI 4（社員向けの画面と、`/manage` の管理画面） |
| `sandbox/` | エージェントがコマンドを動かすSandboxのイメージ、補助コマンド `yoriki-sandbox`、契約テスト |
| `deploy/` | Docker Compose、nginxの設定、本番の環境変数のexample |
| `.github/workflows/` | CI（backend、frontend、api-types、app-images、sandbox） |
| `.vscode/`、`ruff.toml`、`.editorconfig` | エディタと整形・lintの共通設定 |
| `docs/` | 設計と規約 |

## 開発環境のセットアップ

### 必要なもの

- [mise](https://mise.jdx.dev/)（Node.js、pnpm、Python、uvのバージョンを `mise.toml` で揃える）
- [direnv](https://direnv.net/)（ローカル開発の環境変数）
- Docker（PostgreSQLとSandboxの実行）

### エディタ

VS Codeを標準とする。開くと `.vscode/extensions.json` の拡張（Ruff、Prettier、ESLint、Vue、direnv等）のインストールを勧められるので入れる。保存時の整形とlintの規則はリポジトリの設定で固定している（[docs/development.md](docs/development.md) §3.0）。

### 手順

```bash
# ツールを揃える
mise trust && mise install

# 環境変数を用意する（値を埋めてから allow する）
cp .envrc.example .envrc
#   YORIKI_SECRET_ENCRYPTION_KEYS に、.envrc.example のコメントのコマンドで作った鍵を設定する
#   （ないと、LLMのAPI key等のSecretを保存できない）
direnv allow

# PostgreSQL（ローカル開発用）
docker compose -f deploy/compose.dev.yaml up -d

# バックエンド（http://localhost:8000）
cd api
uv sync
uv run python src/manage.py migrate
uv run python src/manage.py createsuperuser   # 最初の基盤管理者
uv run uvicorn app.asgi:application --reload --app-dir src

# Worker（別のターミナルで。会話・タスクのRunを動かす）
cd api
uv run python src/manage.py qcluster

# フロントエンド（別のターミナルで）
cd frontend
pnpm install
pnpm dev            # http://localhost:3000 （/api は localhost:8000 の Django へ転送される）
```

- 基盤管理者は `http://localhost:3000/login?method=password` でログインする。社内SSOを使うには、管理画面の「SSOの設定」（`/manage/sso_providers`）に認証基盤のアプリの値を登録する。
- 会話を始めるには、管理画面でLLMの接続・モデル設定と、Sandboxの実行環境（Runtime Profile）を登録する。
- Sandboxのイメージは `docker build -f sandbox/images/docs/Dockerfile -t yoriki-sandbox-docs:dev sandbox` で手元でビルドできる。Docker DesktopにはgVisorがないので、手元では `YORIKI_SANDBOX_RUNTIME=runc` にする（`.envrc.example` に設定済み）。手元のイメージをRuntime Profileとして登録する方法は [docs/status.md](docs/status.md) §5 にある。
- `/dev/inputs` で、フロントエンドの入力コンポーネントの一覧を確認できる（開発時のみ）。

テストと静的解析は [docs/development.md](docs/development.md) §3、本番の配備（Docker Compose、nginx）は [docs/deployment.md](docs/deployment.md) を参照。

## ライセンス

[MIT License](LICENSE)
