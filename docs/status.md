# 開発の状況と次の作業

作業を引き継ぐための文書。何ができていて、何が決まっていて、次に何をするかをまとめる。作業の区切りごとに更新する。

- 最終更新：2026-10-22（#30）
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
| #14 | LLMの管理画面：`/manage/llm_connections`（接続の登録・API keyの差し替え・接続の確認。種別の選択の近くにローカルLLMを推奨する案内）、`/manage/model_profiles`（モデル設定。中身を変えると新しい版、版の履歴、編集中の競合（409）からの読み直し、接続先のモデル名からの選択）。管理画面のメニューの最初の項目（permissionは `llm.change_llmconnection`・`llm.change_modelprofile`）。共通部品 `ListPageTitle`・`TableSearchForm`・`formatDatetime`。API側は接続とモデル設定の一覧に名前の検索（`search`）を追加 |
| #15 | 認証の方式の見直しの設計（design.md 0.18 §10.0）：SSOの設定を管理画面へ、SSOのない導入先のYorikiのアカウント（招待・パスワード・試行回数の制限）、SSOへの切り替え時のメールアドレスでの紐付け |
| #16 | SSOの設定の管理画面：`user.SSOProvider`（client secretは暗号化）、`v1/sso_providers`、`/manage/sso_providers`（リダイレクトURIの表示）。allauthへは `list_apps()` で保存しない `SocialApp` を渡す。SSOの初回ログインで同じメールアドレスのユーザーに紐付ける。環境変数 `YORIKI_SSO_*` を廃止 |
| #17 | Secretを持つModel・版を作るModel（LLMの接続、モデル設定、SSOの設定）を、Django管理画面では見るだけにする（`utils.admin.ReadOnlyAdmin`） |
| #18 | Yorikiのアカウント：`/manage/users`（`v1/users`、`v1/groups`。作成・役割・無効化・招待／再設定のリンクの発行）、`/invitation#<token>`（`app/v1/auth/invitations/verify`・`accept`）、パスワードでのログイン（`POST app/v1/auth/session/`、`utils.backends.PasswordBackend`。SSOが有効なら基盤管理者だけ。`/login?method=password`）、django-axesによる試行回数の制限（アカウントごと・送信元IPごと、10回で15分）。未ログインのPOSTでもCSRFを確かめる `utils.permissions.CsrfProtected`。`app/v1/auth/providers/` は `{providers, password_login}` を返す |
| #19 | 設計（design.md 0.19）：「エージェント」を標準のハーネス1つだけにし、業務ごとのAgentとSkillを束ねる業務のPluginをやめた。業務の単位はSkill（Tool・資料・Connector・実行環境・必要な能力を版で持ち、公開範囲で誰が使えるかが決まる）。SkillとConnectorは `/`・`@` で明示するか、エージェントが利用者の権限で使えるものから探す。モデルは会話・タスクで直接選び、自動で変えるのはeffortだけ。障害時は会話なら利用者に切り替えを求め、タスクならエラーを通知する |
| #20 | 設計（design.md 0.20）：Webhookのpayloadはそのままファイルとして渡し、読み方はSkillで吸収する。受付時に必要な項目だけをBindingの表で取り出せる（JSONのpath・必須・型、サンプルで試せる）。重複のキーは送り元のイベントIDか `Idempotency-Key` だけ。Webhookのタスクは失敗の通知先が必須。処理の流れを組む画面は作らない |
| #21 | ハーネス（Runner）の設計（[harness.md](harness.md)、design.md 0.21）：RunEventを正本にしてモデルへの入力を組み立てる、Toolの定義・呼び出し方・見せ方、コマンドは呼出しごとの `docker exec` とバックグラウンドのジョブ（常駐のシェルは持たない）、形式ごとの `read`・文字列の置換の `edit`、出力の上限と退避、段階的な圧縮、確認待ち、障害からの再開（Workspaceとの突き合わせ、外部への書込みの重複のキー）、確認を必須にするConnectorの書込み、Provider Adapterへの追加（メッセージの部品、OpenAIのResponses API）。Web検索・取得はMVPの後にConnectorとして加え、プロバイダ組込みの検索は基盤管理者の許可制 |
| #22 | 会話と実行のバックエンド：`conversation`（会話・メッセージ・実行中の依頼）、`run`（Run・Attempt（lease・fencing）・RunEvent・確認・ToolInvocation・Outbox・Reconciler）、`harness`（Runner、`ask_user`・`todo_write`、再試行とモデルの切り替えの確認、繰返しの検出、ステップ数の上限、要約の圧縮）、`usage`（利用量）、`stream`（LISTEN/NOTIFYとSSE）、Provider Adapterの拡張（メッセージの部品、OpenAIのResponses API、effort、推論の途中経過、`tool_choice`）、モデル設定の入力の種類・effort・既定。Q2のクラスタを1つにし、qclusterごとに同時に動かすRunの数を制限 |
| #24 | qclusterでは接続プールを使わない（`settings.USE_DATABASE_POOL`）。Q2がworkerをforkで作り、プールの接続をforkした先と共有して、問い合わせの結果やtransactionが混ざっていた（#22 からの不具合） |
| #25 | 会話の画面：会話を始めるウィザード（個人情報・機密情報を扱うならローカルLLMのモデルだけ）、サイドバーの会話の一覧（`app/v1/stream/` の通知で更新）、Runごとの作業の表示（状態、作業の計画、作業の記録、応答の逐次表示）、確認への回答（単一・複数の選択、自由記述、補足。モデルの切り替えの確認も）、実行中の追加の指示と次の依頼、停止、モデルとeffortの切り替え。`useEventStream`・`useUserEvents`（再接続時の取得し直し、古い接続・応答の破棄）、`DCheckboxGroup`、`MarkdownContent`（`markdown-it`。生のHTMLと画像は出さない。frontend-conventions.md §7.5）。API側はRunの一覧の会話での絞り込みと `todos` の型 |
| #26 | Sandboxのイメージ：`sandbox/CONTRACT.md`（契約番号1。イメージの配置、起動の制限、コマンドの環境、補助コマンドの入出力）、docsのイメージ（Python 3.13、GNUのツール、日本語のフォント、文書処理のライブラリをlockからイメージのPythonへ）、補助コマンド `yoriki-sandbox`（PID 1のinit、exec・kill（グループかPID 1以外のすべて）・形式ごとのread・write・edit（一時ファイルから置き換え、改行は行ごとに保つ）・glob・grep・stat・init-env）、契約テスト（制限の確認、サブコマンド、代表的なコマンド111件）、CI（`sandbox.yml`）。design.md 0.22：`/workspace/env` はイメージのPythonの上の薄い環境（`--system-site-packages`）にした |
| #27 | Runtime Profile：`runtime` アプリ（`RuntimeProfile`・`RuntimeProfileVersion`。中身を変えると版が増える）、レジストリのAPIでのイメージの確認（digest、platform、契約番号のラベル。許可したレジストリだけ、HTTPSと公開のアドレスだけ）、`v1/runtime_profiles`、Runを始めるときに既定の版を固定（`Run.runtime_version`。既定がなければRunを始めない）、`/manage/runtime_profiles`。design.md 0.23：Runtime ProfileはSkillごとでなくRunの単位で決める。モデル設定の「既定にする」が同時の要求で500になる問題も直した |
| #30 | LauncherとSandboxのTool：`runtime/launcher.py`（Docker SDK（docker-py）。CONTRACT.md §2の制限で起動し、起動後にinspectで確かめ直す。Workerのplatformのdigestを選んでAttemptに記録。補助コマンド、コマンドのexec、tarでのWorkspaceの取り出し・戻し、`docker rm -f`。終わったAttemptのコンテナの掃除。userns-remap・rootlessでなければ警告）、Workspaceのsnapshotの保存先の抽象（`runtime/storage.py`。design.md §7.8の契約。初期はWorkerのローカルディスク）、`run.Checkpoint`（Attemptを終えるときに保存。新しいAttemptと同じ会話の次の依頼は、最後のCheckpointから戻す。戻せないWorkerでは空で続けて知らせる。design.md 0.24）、RunnerのTool（`shell`・`job_output`・`job_kill`・`read`・`write`・`edit`・`glob`・`grep`。読んでから書く版の確認、出力の上限と退避、読取りのToolの並列の実行、バックグラウンドのジョブとその終了の知らせ、障害の後の知らせ、Sandboxの喪失での別のAttemptでのやり直し）、画面の作業の記録のToolの表示名、CIの `sandbox-runner`（Sandboxのイメージをビルドして実際のDockerでテスト）、deployment.md §7.1（ホストの更新、userns-remap・rootless Docker） |

確認済みのこと：

- #12 のLLMのAPIは、テストで各社の応答を差し替えて確かめた。実際のプロバイダのAPI（API keyが要る）ではまだ試していない。

- LLMの管理画面は、開発サーバーでブラウザ（Playwright）から一通り操作して確かめた（追加・更新・接続の確認の失敗・版の履歴・409の競合・使用中の接続の削除の拒否）。実際のプロバイダでの接続の確認は、まだ試していない。

- 単一ホストの構成（`deploy/compose.yaml`）を手元とCIで起動し、ログイン画面の表示、API、静的ファイル、セキュリティのヘッダー、SSOの開始（リダイレクトURIが `https://`）まで動く。
- 実際のIdP（Entra ID・Google Workspace）でのログインは、まだ試していない（client IDの発行が要る）。

- 会話の画面は、偽のLLMサーバーを使ってブラウザ（Playwright）から一通り操作して確かめた（開始、逐次表示、質問への回答、追加の指示、次の依頼、停止、effortの切り替え、再試行の後のモデルの切り替え、狭い画面、XSSを含む応答）。実際のプロバイダでは、まだ試していない。
- #30 のSandboxは、手元のqcluster（fork）・手元のレジストリのRuntime Profile（platformのdigest）・Toolを呼ぶ偽のLLMで、shell → read → 確認待ち（Checkpointを保存してSandboxを消す）→ 回答 → 戻したWorkspaceで edit → 完了、まで確かめた。画面での表示は、ブラウザではまだ確かめていない。Linuxのホスト（本番の構成のqcluster・userns-remap）では、まだ試していない。

## 2 実装の中で決めたこと

設計書・規約に反映済み。経緯を知らずに変えないよう、要点だけを挙げる。

- **権限**：「permissionを定義する → Groupに割り当てる → Groupをユーザーに割り当てる」で扱う。permissionは `utils.permissions.AppPermission` に1か所でまとめ（codenameは `can_<動詞>_<対象>`）、Modelの `Meta.permissions` で作る。役割（業務管理者・Skill公開者）はGroupで、初期の中身は `user/roles.py`（`migrate` のたびに同期）。Userのフラグや役割の名前では判定しない。基盤管理者は `is_superuser`。フロントエンドも `allPermissions` だけで判定する（backend-conventions.md §6.3、frontend-conventions.md §6）。
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
- CI：e2e、本体のイメージのレジストリへのpush（使うレジストリが未決。design.md §13）、Sandboxのイメージのmulti-arch（arm64）のビルドとDocker Hubへのpush・脆弱性スキャン（公開先のorgが未決）。
- Compose：channel-listener（Channelの受信を実装するとき）。
- 管理コマンド：`drain_workers`・`resume_workers`（Workerの実行を実装するとき）。
- 業務のアプリは `llm`、`conversation`、`run`、`harness`、`stream`、`usage`、`runtime`（Runtime Profileまで。Launcherはまだ）まで。`skill`、`plugin`、`artifact`、`task`、`delivery`、`connector` 等（backend-conventions.md §1.2）は、まだない。
- Sandboxで後回しにしたもの：`/workspace/input`・`/workspace/master` のmount（Artifact・Skillの段階）、`install_python_packages` と `/packages`（PackageBundle・EnvironmentRevision）、`read` の画像（PDFのページ・画像。RunEventに画像を保存するArtifactの段階）、ステップごとのCheckpoint（今はAttemptを終えるときだけ。障害で落ちると、最後のCheckpointより後の変更は失われ、モデルに知らせる）、snapshotの保持期限と削除、別のWorkerから読めるsnapshotの保存先（S3。成果物の段階のStorage Backend）、非公開のレジストリからのpullの認証、gVisor（§4の2）。
- 補助コマンドが返すパス（symlinkを辿った先）と、モデルが指定したパスが違うと、読んだ版の記録が見つからず `edit` が「まだ読んでいない」になる（symlinkを使うときだけ）。
- 会話の画面で後回しにしたもの：添付と成果物の一覧（Artifactの段階）、入力欄の `/`・`@` での指定（Skill・Connectorの段階）、次の依頼の開始前の編集・取消（design.md §11.2。APIもまだない）、会話のタイトルの変更、実行履歴の画面（`/runs`）。
- Provider Adapterは、実際のプロバイダのAPIではまだ試していない（API keyが要る）。テストは各社の応答を差し替えて確かめている。
- 接続の同時実行数・1分あたりの要求数の上限は、項目があるだけで、まだ守らせていない（Runnerの段階）。
- 契約テストの代表的なコマンドは111件。harness.md §8.5の目標（数百）へ、Skillで使われたコマンドから増やす。
- 認証プロキシの裏に置く場合は、前段のnginxが利用者のIPアドレスを `CF-Connecting-IP` 等で解決して `X-Forwarded-For` を上書きしているかを、導入先で確かめる（deployment.md §8.6）。解決していないと、ログインの試行回数の制限で、多くの利用者が同じIPアドレスとして数えられる。
- ハーネスで後回しにしたもの：予算の上限の強制（今は利用量を記録するだけ。クオータの設定とあわせて作る）、Toolの並列の実行（今は順に実行する。Sandboxの読取りのToolを加えるときに作る）、圧縮の1・2段（大きいTool結果の退避と置き換え。harness.md §9.1）、Checkpoint・Workspace snapshot（Sandboxの段階）、添付ファイル（Artifactの段階）。
- フロントエンドのSPAを静的なファイルにして `yoriki-api` のイメージに入れ、`frontend` のコンテナをなくす（`ssr: false` なのでNode.jsのサーバーは要らない。アプリのイメージが1つになる）。
- 招待のリンクはYorikiからメールで送らない（画面でコピーして渡す）。メールの送信は必要になった時点で追加する（design.md §10.0）。

## 4 次の作業

design.md §12の第1段階を、1つの目的ごとに1つのPull Requestにして進める。提案する順番：

1. **gVisorの評価**：契約テストと、文書処理の所要時間で確かめる（design.md §7.2の5）。
2. **成果物とファイル（`artifact`）**：Local・S3のStorage Backend、添付（`/workspace/input` へのmount）、成果物の取得（`/api/files/<token>/`、`X-Accel-Redirect`）。Workspaceのsnapshotの保存先（`runtime/storage.py` の `SnapshotStorage`）にS3の実装を足し、複数のWorkerで再開できるようにする。`read` の画像もここで。
3. **Skill（`skill`、`resource`）**：Draft・Version・公開範囲と公開、Skillの資料（マスター・テンプレート）、会話からのSkill更新、入力欄の `/` での指定、エージェントに見せる一覧（利用者の権限で使えるものだけ）。役割「Plugin公開者」（`user/roles.py`）を「Skill公開者」に改める（design.md 0.19）。
4. **Connector（`connector`）とタスク・配信（`task`、`delivery`）**：読取り専用のDB Connector、Connectorを使える利用者・役割と操作（ConnectorPermission。design.md §4.9.1）、入力欄の `@` での指定、Task Definition（使うSkill・モデル・許可する操作）、Webhook・Zapierの通知、Trigger。DB Connector（種別：PostgreSQL・MySQL・SQL Server、ホスト、DB名、ユーザー、パスワード等）とHTTP Connector（ホスト、ヘッダー、認証情報等）の設定は管理画面で登録し、資格情報は `utils/crypto.py` で暗号化してDBに保存する（backend-conventions.md §6.4）。個人情報を主な中身とする接続先には機密のフラグを付けられるようにし、フラグの付いたConnectorはローカルLLMのモデルを使うタスク・会話にだけ割り当てる（design.md §4冒頭、§4.9.1）。
5. **最初の2つの業務**（design.md §13。Skillとタスクとして作る）：請求書PDF → 社内DBと照合 → 取込用のCSV、契約書レビュー → Zapierへ結果。
6. **Channel Plugin**（Teams・Slack・Discord。`plugin` のRegistry）。

並行してよいもの：実際のIdPでのログインの確認（client IDが用意でき次第）、CSPの改善。

## 5 作業を始めるときの手順

```bash
mise install
docker compose -f deploy/compose.dev.yaml up -d   # PostgreSQL
cd api && uv sync && uv run python src/manage.py migrate
cd frontend && pnpm install
```

- `.envrc` に `YORIKI_SECRET_ENCRYPTION_KEYS` を設定する（`.envrc.example` の生成コマンドで作る）。設定がなくても起動はするが、LLMの接続のAPI keyを保存するときに入力エラーになる。
- Runを手元で動かすときは、api（`uvicorn app.asgi:application --port 8000`）と `python src/manage.py qcluster` を起動する。macOSでは、qclusterに `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` が要る（`.envrc.example`。ないとforkしたworkerがHTTPの接続で落ちる）。
- LLMのAPI keyがなくても、OpenAI互換の応答を返す小さな偽のサーバーを立て、ローカルLLMの接続として登録すれば、API → Q2 → Runner → SSEを一通り確かめられる（#22で確かめた方法）。開発用のDBには、そのときの確認用のユーザー（`e2e-check@example.co.jp`）と、接続「確認用の偽LLM」・モデル設定「確認用の偽モデル」「確認用の偽モデル（推論）」（effortは low・medium・high）が残っている。モデル設定「社内Qwen」の接続先はつながらないので、再試行の後のモデルの切り替えの確認に使える。
- 基盤管理者は `cd api && uv run python src/manage.py createsuperuser` で作る。開発時はDjangoの管理画面（`/api/admin/`）のパスワードログインでセッションを作れる。
- Sandboxのイメージは `docker build -f sandbox/images/docs/Dockerfile -t yoriki-sandbox-docs:dev sandbox` でビルドし、`cd sandbox && uv sync && uv run pytest` で契約テストを動かす（sandbox/README.md）。apiのLauncher・SandboxのToolのテスト（`pytest -m sandbox`）も、このイメージ（`YORIKI_SANDBOX_TEST_IMAGE`）で動く。イメージがなければ飛ばす。
- Runを手元で動かすと、Workerの作業領域（ジョブの出力、snapshot）を `YORIKI_WORKSPACES_DIR`（`.envrc.example` ではリポジトリの `.workspaces/`）に作る。
- Runtime Profileを手元で登録するときは、手元のレジストリにイメージをpushする：`docker run -d --name yoriki-dev-registry -p 127.0.0.1:5001:5000 registry:3`、`docker tag yoriki-sandbox-docs:dev localhost:5001/yoriki-sandbox-docs:dev && docker push localhost:5001/yoriki-sandbox-docs:dev`。apiは `YORIKI_SANDBOX_INSECURE_REGISTRIES=localhost:5001` を付けて起動し、管理画面で `localhost:5001/yoriki-sandbox-docs@<pushで表示されたdigest>` を登録する。開発用のDBには、この方法で登録した既定のRuntime Profile「文書処理」（linux/arm64）がある。既定のRuntime Profileがないと、会話のRunは始まらない。

- 環境変数はリポジトリルートの `.envrc`（direnv）。テストやmypyは、`.envrc` を読み込んだ状態で実行する。
- 変更前に、AGENTS.md と、触る範囲の規約を読む。作業の進め方（ブランチ、コミット、Pull Request、CI、マージ）は development.md に従う。
- 作業の区切りでは、本書の §1・§3・§4 を更新する。Pull Requestで進めた作業は、マージの前にそのPull Requestの中で更新する（マージの後に `main` へ別にコミットしない）。
- Pull Requestの進め方（AGENTS.md）：コードの変更は手元のブランチで作り、PRはユーザーに依頼されてから作る。マージはユーザーが指示したときに限り行う。本書の状況の更新や誤字の修正のように判断を含まない文書の変更は、PRを作らずに `main` へ直接コミットする。design.md や規約の判断を変える変更はPRにする（development.md §1）。PRごとにCodexのレビューを受け、指摘をコードで確かめてから直す。CIが通ったら報告し、マージの指示を待つ（squash merge。`[skip ci]` は development.md §4.1）。作業の中で見つけた小さな問題は、別のPRにせず、今のPRで直す。PRのページは消せないので、試しのPRや取り消しのPRは作らない。
- 設計の判断を変える話が出たら、コードを書く前に design.md と本書を直し、合意してから実装する。
