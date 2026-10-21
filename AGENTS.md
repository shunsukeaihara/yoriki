# AGENTS.md

このリポジトリで作業するコーディングエージェント向けの指示。

Yoriki（よりき）は、社員が業務用AI Agentを安全に使うための社内Webサービス（OSSとして公開予定）。
モノレポで、Nuxt（`frontend/`）、Django / DRF / Django Q2（`api/`）、Sandboxイメージ（`sandbox/`。公開Docker Hubへprovenanceと契約番号ラベル付きで配布）、配備設定（`deploy/`。Docker Compose・Nginx）を管理する。DBはPostgreSQL。

## 参照する文書

- 開発の状況と次の作業（作業を始める前に読む）：[docs/status.md](docs/status.md)
- 製品の設計・要件・データモデル・状態遷移：[docs/design.md](docs/design.md)（大きいため、必要な章だけを読む）
- バックエンドの実装規約：[docs/backend-conventions.md](docs/backend-conventions.md)
- Model / Serializer / ViewSetの実装例：[docs/backend-examples.md](docs/backend-examples.md)
- フロントエンドの実装規約：[docs/frontend-conventions.md](docs/frontend-conventions.md)
- フロントエンドの実装例：[docs/frontend-examples.md](docs/frontend-examples.md)
- リアルタイム通信（SSE）の方針：[docs/realtime.md](docs/realtime.md)
- エージェントのハーネス（Runner、Tool、文脈の組立、Sandboxでの実行）：[docs/harness.md](docs/harness.md)
- 配備・Compose・Nginx・イメージの方針：[docs/deployment.md](docs/deployment.md)
- 開発の進め方（ブランチ、コミット、lint・テスト、CI、VS Code）：[docs/development.md](docs/development.md)

バックエンド（`api/`）を変更する前に docs/backend-conventions.md を、フロントエンド（`frontend/`）を変更する前に docs/frontend-conventions.md を読むこと。

手元に `AGENTS.local.md`（gitに入れない）があれば、あわせて読む。リポジトリの外の参考情報はそこに書く。

## 開発環境

- ツールのバージョンはリポジトリルートの `mise.toml` で固定する（Node.js、pnpm、Python、uv）。`mise install` で揃える。
- 環境変数は、ローカル開発ではdirenvの `.envrc`（`.envrc.example` をコピーして作る。gitに入れない）、本番では実行環境から渡す。アプリは環境変数を読むだけにし、値をコードやsettingsに直接書かない。新しい環境変数は `.envrc.example` に追記する。
- フロントエンドのパッケージ管理はpnpm、バックエンドはuvを使う。
- 整形・lintの規則は `ruff.toml`、`frontend/.prettierrc`、`frontend/eslint.config.mjs`、`.editorconfig` にあり、VS Code（`.vscode/settings.json`）とCIが同じものを使う。行の長さは120。
- バックエンドを変更したら `uv run ruff check`、`uv run ruff format`、`uv run mypy src`、`uv run pytest` を通す。
- フロントエンドを変更したら `pnpm lint`、`pnpm format`、`pnpm typecheck` を通す。入力ラッパーを変更したら、開発サーバーの `/dev/inputs` で表示を確認する。

## 守ること

### API・バックエンド

- endpointは可能な限りViewSetで書き、DRFの標準の流れに合わせる。CRUD以外の操作は `@action` で追加する。
- Swaggerですべてのendpointの入出力の型が分かるようにする。本文のある入出力は名前付きのSerializerで定義し、本文がなければ `None` と明示する。汎用や既存のSerializerで形が合えばそれを使う。`request.data` を直接読まない。
- 検証、正規化、Modelインスタンスの作成・更新はSerializerの責務にする。Viewは取得、権限、transactionの範囲だけを担う。
- `django-extensions` と `drf-spectacular` は必須の依存とする。DjangoはASGIとしてUvicornで起動する。

### フロントエンド

- 社員向けと管理向けを1つのNuxtアプリで作る。UIはNuxt UIを使い、入力・ダイアログ・表は `components/common` の `D*` ラッパーを通す。
- APIの型はOpenAPIスキーマから生成する。Serializerを変更したら型も生成し直す。
- 多言語化は最初から行う。画面の文言は日本語の文をそのままキーにして `$t('保存しました')` で出し、Djangoのメッセージも日本語を元に `_()` で包む。他の言語の翻訳ファイルはまだ用意しない。
- サーバーからの逐次配信はSSEで受け取り、送信はREST APIで行う。WebSocketやgRPCは使わない。

### 全体

- 社員の認証はYoriki自身がバックエンドで行い（django-allauthによるOIDCの社内SSO）、DjangoのセッションCookieで保つ。前段に認証プロキシ（oauth2-proxy等）があっても、それはバリアとして置くだけにする（backend-conventions.md §6.2）。フロントエンドにトークンを持たせず、APIは画面と同じオリジンの `/api/` で呼ぶ。
- 権限は必ずサーバー側（permission、QuerySetの絞り込み、Sandboxの制限）で強制する。LLMの判断やプロンプトの指示に頼らない。
- 版のあるオブジェクト（`*Version`、`*Revision`）と履歴・監査記録は不変にする。変更は新しい版を作って行う。
- Q2のtaskにはIDだけを渡す。実行状態の正本はPostgreSQLに置き、二重実行はclaim / leaseで防ぐ。
- LLMのAPI keyなどのSecretは、Control Planeの外（Runner、Sandbox、ログ、画面）へ出さない。
- 既存データを消すマイグレーションやコマンドは、ユーザーの了承なしに実行しない。
- 設計の判断がdesign.mdと食い違う場合は、実装する前にユーザーに確認する。

### Pull Requestとマージ

- Pull Requestは、ユーザーに依頼されてから作る。作業はそれまで手元のブランチに置く。CIが通った・レビューの指摘を直した、といった区切りでは「PRを作ってよいか」を報告して待つ。
- マージは、ユーザーがそのPRのマージを指示したときに限り行う。指示がなければマージしない（CIが通った・レビューを直した、といった区切りでも、自分の判断で `gh pr merge` を実行しない）。
- 指示を受けてマージするときは、CIが最後のコミットですべて通り、ブランチが `main` の最新を含み、衝突がないことを確かめてから、development.md §1の基準でsquash mergeか通常のmergeを選び、§4.1の条件で `[skip ci]` を付ける。squash mergeのメッセージは、取り込む変更の最終的な内容に合わせて書き直す。マージした後は、手元とGitHubの作業ブランチを消す。
- PRのページとマージは消せない記録なので、試しのPR・取り消しのPR・一時的なブランチのpushは作らない。
