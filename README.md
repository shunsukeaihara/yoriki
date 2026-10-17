# Yoriki

社員が業務用のAI Agentを、安全に・共通の社内Webサービスとして使うための基盤。

- 社員はチャットで業務を依頼し、Agentは許可された範囲でファイル処理やコードの実行を行う。
- 管理者はAgent、Skill（手順・判断基準・参照ファイル）、Plugin、外部接続、権限、利用量を一元管理する。
- 権限はLLMの判断ではなく、サーバーと実行環境（ネットワークのないSandbox）で強制する。

> 現在は設計段階から実装を始めるところ（pre-alpha）。OSSとして公開する予定。

## 文書

| 文書 | 内容 |
|---|---|
| [docs/design.md](docs/design.md) | 設計企画書（要件、データモデル、実行、Plugin、Connector、LLM、認証、段階的な実装計画） |
| [docs/backend-conventions.md](docs/backend-conventions.md) / [backend-examples.md](docs/backend-examples.md) | バックエンド（Django / DRF）の規約と実装例 |
| [docs/frontend-conventions.md](docs/frontend-conventions.md) / [frontend-examples.md](docs/frontend-examples.md) | フロントエンド（Nuxt / Nuxt UI）の規約と実装例 |
| [docs/realtime.md](docs/realtime.md) | リアルタイム通信（SSE）の方針 |
| [docs/deployment.md](docs/deployment.md) | 配備（Docker Compose、Nginx、イメージ、環境変数、更新手順） |
| [docs/development.md](docs/development.md) | 開発の進め方（ブランチ、コミット、lint・テスト、CI） |
| [AGENTS.md](AGENTS.md) | コーディングエージェント向けの指示 |

## 構成

| ディレクトリ | 内容 |
|---|---|
| `api/` | Django / DRF / Django Q2（API、Runner、Worker）※雛形はこれから作る |
| `frontend/` | Nuxt 4 / Nuxt UI 4（社員向け画面と管理画面） |
| `sandbox/` | Agentがコードを実行するSandboxのイメージ（Docker Hubで公開）※これから作る |
| `deploy/` | Docker Compose、Nginxの設定 ※これから作る |
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
direnv allow

# フロントエンド
cd frontend
pnpm install
pnpm dev            # http://localhost:3000 （/api は localhost:8000 の Django へ転送される）
```

`/dev/inputs` で、フロントエンドの入力コンポーネントの一覧を確認できる（開発時のみ）。

バックエンド（`api/`）とローカル用のComposeファイル（`deploy/compose.dev.yaml`）の起動手順は、雛形を作るときにここへ追記する（予定している手順は [docs/deployment.md](docs/deployment.md) §3.3）。

## ライセンス

未定（OSSとして公開する前に決める）。
