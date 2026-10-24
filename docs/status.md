# 開発の状況と次の作業

作業を引き継ぐための文書。何ができていて、何が決まっていて、次に何をするかをまとめる。作業の区切りごとに更新する。

- 最終更新：2026-10-25（会話からのSkill更新）
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
| #31 | gVisorの評価と既定化（design.md 0.25 §7.2）：設定 `YORIKI_SANDBOX_RUNTIME`（既定 `runsc`。Dockerに登録されていなければSandboxを起動しない、起動後のinspectで実行環境も確かめる）、gVisorではRuntime Profileのプロセス数の上限をホストのpidsへ換算（×2＋64）、メモリの超過でSandboxごと止まったときの知らせ（Attemptの失敗の理由に残し、次のAttemptでモデルに上限と減らし方を伝え、繰り返したら利用者にも理由を示す）、契約テストとLauncherのテストの実行環境の切替え（`YORIKI_SANDBOX_TEST_RUNTIME`）、所要時間の比較（`sandbox/benchmarks/docs.py`）、CI（`.github/actions/install-gvisor`、`sandbox-gvisor`（amd64・arm64、userns-remap）、`sandbox-runner` をgVisor＋userns-remapで）、deployment.md §7.1（gVisorの入れ方） |
| #32 | `artifact` アプリ：Storage Backend（`artifact/storage.py`。Local・S3（boto3。上書きしないことを `If-None-Match: *` で守る）。`YORIKI_STORAGE_BACKEND` で選び、保存先のIDとkeyで記録する）、BlobObject・BlobLocation・Artifact、添付のアップロード（`app/v1/conversations/{id}/artifacts/`、multipart、`YORIKI_UPLOAD_MAX_MB`）とメッセージへの添付（`attachments`。送ったときに `/workspace/input` の中の名前を決め、同じ名前は「名前 (2)」）、ダウンロード（セッションの `download` と期限付きtokenの `/api/files/<token>/`。いつも添付として返し、`nosniff`・CSPの `sandbox`。Localはnginxの `X-Accel-Redirect`、それ以外はapiが非同期のiteratorで返す）。Sandbox：会話の添付すべてをAttemptごとのディレクトリに置き、`/workspace/input` に読取り専用でbind mount（起動後のinspectでも確かめる。実行中の追加指示の添付も足す）、Attemptを終えるたびに `/workspace/output` を成果物として回収（変わっていないファイル・隠しファイル・symlinkは回収しない）、Workspaceのsnapshotも同じ保存先へ（`runtime/storage.py` を移した。S3なら別のWorkerで再開できる）。モデルには添付の一覧と、新しい依頼ではそれまでの添付の一覧を渡す。同じ会話の次の依頼のRunは、前の依頼のRunの文脈を先頭に引き継ぐ（harness.md §5.6。結果のないTool呼出しは補い、圧縮では1つのまとまりにする）。画面：入力欄のクリップとドロップでの添付（会話を始めるフォームも）、メッセージの添付、「添付と成果物」の欄（広い画面は右の欄、狭い画面はドロワー）。nginx（アップロードの上限、`/_protected/` のヘッダー）、Compose（`/var/lib/yoriki/objects`）、deployment.md §7.2。design.md 0.26：同じ会話の中では依頼が変わっても文脈とWorkspaceを引き継ぐ（タスクはトリガーで起動してから終わるまで） |
| #33 | `read` の画像：モデル設定が画像入力に対応するときだけ、補助コマンドに画像を求め、スキャンのPDFのページ（1回に5枚まで）と画像ファイルをTool結果の画像としてモデルに渡す（対応しないモデルには、画像に対応するモデルが要ることを伝える文）。画像のbytesは保存先にBlobObjectとして置き、RunEventには参照だけを残す（Artifactにはしない。Workerは読んだ画像をプロセスに残す）。送る前に今のモデルに合わせる（途中で画像に対応しないモデルに切り替えたら断りの文に、1回の要求で新しい側の20枚まで）。圧縮の見積もりで画像1枚を1,600 tokenと数える。繰返しの注意で画像が落ちていたのを直した。補助コマンド：PNGで1MBを超える画像はJPEGにする（CONTRACT.md §4.3。契約番号は変えない） |
| #34 | `skill` アプリ：Skill（公開範囲は個人・部門・全社で作成時に固定、公開中の版、緊急停止）、SkillDraft（Skillごとに1つ、revisionで競合を検出、公開で閉じる）、不変のSkillVersion・SkillAssetBinding（説明・手順・Tool・必要な能力・入出力のSchema・資料の版）、SkillChange（公開・切戻し・停止の記録、operation keyで再送を重ねない）。`resources` アプリ：Resource・ResourceVersion（資料の原本の版。中身はBlobObject）。`user` アプリ：部門（Department）と所属（部門の中の役割：利用・編集・公開）と管理API（`v1/departments`）、`app/v1/my/` の所属する部門。permission `skill.can_edit_company_skills`・`can_publish_company_skills` と役割「Skill公開者」（「Plugin公開者」を改名）。API：`app/v1/skills`（作成・公開・切戻し・停止・再開・削除）と、その下の `versions`・`changes`・`drafts`・`drafts/{id}/assets`（資料のアップロード・置換）・`files`（原本の取得） |
| #35 | Skillの画面：社員向けの `/skills`（一覧・作成。作れる範囲だけを出す）と `/skills/[skillId]`（公開中の版の中身、Draftの編集（説明・手順・Tool（公開の役割だけ）・必要な能力・入出力のSchema）、資料の追加・変更・置換・外す、公開、Draftの破棄、版の履歴（中身・この版から編集・この版に戻す）、変更の記録、名前の変更・停止・再開・削除）。Draftのrevisionでの競合（409）からの読み直し、資料の操作の後も入力中の本文を残す、公開・切戻しのoperation key。部門と所属の管理画面 `/manage/departments`（基盤管理者）。`composables/skill.ts`。Skillの画面は `/manage` ではなく社員向けに置く（frontend-conventions.md §8.1）。最初の公開の変更点に空の項目が載っていたのを直した |
| #36 | 会話でSkillを使う（design.md 0.28、harness.md §10.1）：エージェントに見せるSkillの一覧（使えて、モデルが必要な能力を持つもの。文脈の2%まで、同じ名前は範囲を添える）をRun開始時にManifestへ固定し、`<runtime_context>` の `<skills>` と `skill` の引数のenumにする。`skill` Tool（その時点の公開中の版へ解決し、権限・停止・能力・要るToolを確かめてManifestへ追記、`skill.loaded`。同じRunでは同じ版）。入力欄の `/` での指定（メッセージの `skills`。3つまで。Run開始時に版を解決して最初の発言に本文を入れ、Toolはその `tools` に限る。実行中なら次の依頼）。資料はWorkerのディレクトリを `/workspace/master` に読取り専用でbind mountして `skills/<Skill>/<key>/<ファイル名>` に置く（起動の前と、動いているSandboxへの途中の読込み）。`submit_result`（`/` で指定したSkillの業務結果のSchemaを引数にし、`Run.result` に保存。呼ばずに終えようとしたら1回促し、なければ `no_result`）。Runが使うSkillが停止・削除・権限の失効をしたら次の区切りで止める（`skill`）。モデルの切り替えはSkillの必要な能力を持つモデルに限る。業務結果のSchemaを全プロバイダで通る範囲に限る検証。system promptのSkillの節。画面：入力欄のSkillのボタンと `/` の一覧、メッセージのSkill、Runの使ったSkillと版、業務結果。APIは `Run.skills`・`result`、メッセージの `skills`・`skillsInfo` |
| （作業中） | 会話からのSkill更新（design.md 0.29 §11.6、harness.md §10.9）：Skillの編集のRun（`Run.kind`。対象のSkillを1つだけManifestの `fixed.skill_edit` に固定し、新しいSkillは `create_skill` で作って追記する）。始め方は、入力欄の `/` の「編集」「新しいSkillを作る」（メッセージの `edit_skill`・`create_skill`。実行中なら次の依頼）と、業務のRunの `request_skill_edit`（対象を利用者が編集できるSkillから名前で決め、確認（種類「Skillの編集」）で承認されたら要約を本文とする編集の依頼を次の依頼として受け付ける。自由記述の回答は承認にしない）。Skill管理のTool（`get_skill`（公開中の版とDraftの資料を `/workspace/master/skills`・`drafts` に置く）、`create_skill`、`update_skill_draft`、`stage_skill_asset`（Sandboxのファイルを原本のbytesのままDraftの資料に追加・置換。添付の置換も兼ねる）、`remove_skill_asset`、`publish_skill_draft`（公開の役割がなければ変更案として保存）、`rollback_skill`）は、管理APIと同じSerializer・Modelのメソッドで検証と権限を確かめ、公開・切戻しはToolInvocationのoperation keyで重ねない。SkillChangeに編集のRunを記録。画面：`/` の一覧の「使う」「編集する」「新しいSkillを作る」、編集の依頼のメッセージ、提案の確認、Runの「Skillの編集」と変えたSkill（状態・版・変更点・Skillの画面へのリンク）。APIは `Run.kind`・`skillChanges`、確認の `target`、メッセージの `skillEdit` |

確認済みのこと：

- #12 のLLMのAPIは、テストで各社の応答を差し替えて確かめた。実際のプロバイダのAPI（API keyが要る）ではまだ試していない。

- LLMの管理画面は、開発サーバーでブラウザ（Playwright）から一通り操作して確かめた（追加・更新・接続の確認の失敗・版の履歴・409の競合・使用中の接続の削除の拒否）。実際のプロバイダでの接続の確認は、まだ試していない。

- 単一ホストの構成（`deploy/compose.yaml`）を手元とCIで起動し、ログイン画面の表示、API、静的ファイル、セキュリティのヘッダー、SSOの開始（リダイレクトURIが `https://`）まで動く。
- 実際のIdP（Entra ID・Google Workspace）でのログインは、まだ試していない（client IDの発行が要る）。

- 会話の画面は、偽のLLMサーバーを使ってブラウザ（Playwright）から一通り操作して確かめた（開始、逐次表示、質問への回答、追加の指示、次の依頼、停止、effortの切り替え、再試行の後のモデルの切り替え、狭い画面、XSSを含む応答）。実際のプロバイダでは、まだ試していない。
- Skillの画面は、開発サーバーでブラウザ（Playwright）から、基盤管理者と部門の「編集」の役割の社員で一通り操作して確かめた（部門と所属の追加、部門のSkillの作成、Draftの編集・保存、資料の追加と置換、公開、別のタブからの変更による409と読み直し、版の中身、切戻し、停止と再開、変更の記録、編集の役割ではToolを変えられず公開・名前の変更・停止がないこと、狭い画面）。
- `read` の画像は、偽のSandboxのテストと、手元の実際のSandbox（runc）でスキャンのPDFを読むテストで確かめた。実際のプロバイダのモデル（画像の入力）では、まだ試していない。
- gVisor：契約テストは手元のarm64（Docker Desktopの中のdind）とCIのamd64・arm64（userns-remap）で、LauncherとSandboxのToolのテストは手元のdind（userns-remap）とCIのamd64（userns-remap、AppArmor）で、すべて通った。gVisorでの会話のRun（qclusterから）は、Linuxのホストではまだ試していない。
- 成果物とファイルは、手元のqcluster・偽のLLM（shellで `/workspace/input` を読み、`/workspace/output` に書く）・ブラウザ（Playwright）で、会話を始めるフォームでの添付 → Run → 成果物の欄 → ダウンロード（HTMLも添付として返る）→ 同じ名前の添付の2回目の依頼（「(2)」に分かれ、変わったファイルだけが新しい成果物になる）→ 狭い画面のドロワー、まで確かめた。S3はmotoで確かめただけで、実際のS3・S3互換のストレージでは試していない。nginxの `X-Accel-Redirect` は、Composeの構成ではまだ試していない。
- #36 の会話でのSkillは、手元のqcluster・手元のレジストリのRuntime Profile（runc）・偽のLLM・ブラウザ（Playwright）で、`/` の一覧から部門のSkillを選んで送る → 最初の発言にSkillの本文 → 実際のSandboxで `/workspace/master` の資料を `read` → 業務結果のSchemaがあるので `submit_result` を促して `no_result`、と、次の依頼で指定せずに送る → `<skills>` からエージェントが `skill` で読み込む → 完了、まで確かめた。実際のプロバイダのモデルがSkillを選ぶかは、まだ試していない。
- 会話からのSkill更新は、手元のqcluster・手元のレジストリのRuntime Profile（runc）・偽のLLM・ブラウザ（Playwright）で、基盤管理者が `/` の「編集」から部門のSkillを編集 → `get_skill` → `update_skill_draft` → `publish_skill_draft` で第4版を公開（Runに「第4版を公開・次の依頼から反映」）、業務の会話で「Skillも直しておいて」→ `request_skill_edit` の確認 →「Skillを編集する」→ 業務のRunの完了後に編集のRunが始まって第5版を公開、部門の「編集」の役割の社員では公開されず「変更案を保存（未公開）」、狭い画面、まで確かめた。実際のプロバイダのモデルが「今回だけ」と「今後も」を区別して `request_skill_edit` を呼ぶかは、まだ試していない。
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
- **Skillの権限**：個人は本人、部門は所属の役割（利用・編集・公開）、全社はpermission（`skill.can_edit_company_skills`・`can_publish_company_skills`）で決める。部門は役割（Group）と別のDepartmentで表す。Draftの変更は編集以上、公開・切戻し・停止・削除・名前とToolの変更は公開の役割。判定は `Skill.access_of()` と `Skill.objects.visible_to()`（design.md §3.6）。
- **資料のアプリ名**：Pythonの標準ライブラリの `resource` と重なるので `resources` にした。
- **マージ**：squash mergeを基本にし、コミットを残す価値があるときだけ通常のmerge。`main` に入るコミットには、条件を満たせば `[skip ci]` を付ける（development.md §1、§4.1）。

## 3 まだないもの・課題

- CSPのインラインのスクリプトを、ハッシュかnonceで絞る（deployment.md §4.1）。
- フロントエンド：`app.config.ts`（テーマの色。primary / neutralを決める）、E2E（Playwright）。
- CI：e2e、本体のイメージのレジストリへのpush（使うレジストリが未決。design.md §13）、Sandboxのイメージのmulti-arch（arm64）のビルドとDocker Hubへのpush・脆弱性スキャン（公開先のorgが未決）。
- Compose：channel-listener（Channelの受信を実装するとき）。
- 管理コマンド：`drain_workers`・`resume_workers`（Workerの実行を実装するとき）。
- 業務のアプリは `llm`、`conversation`、`run`、`harness`、`stream`、`usage`、`runtime`（Runtime Profile、Launcher）、`artifact`、`skill`・`resources`（土台だけ）まで。`plugin`、`task`、`delivery`、`connector` 等（backend-conventions.md §1.2）は、まだない。
- Skillで後回しにしたもの：実行中のRunでSkillを最新の公開版へ切り替える依頼（design.md §3.2）、機密の制限によるSkillの絞り込み（Connectorの段階）、Draftの評価（SkillEvaluation、`test_skill_draft`）、Resource Collection、Connectorの宣言、資料の形式ごとの検証（MD・CSV・PPTX。今は大きさだけを確かめる）、版の差分の表示（今はSkillChangeに変わった項目と資料のkeyだけを記録）、公開せずに捨てたDraftの資料の原本の回収、共通Skillから個人用を派生させる操作、Draftを開いた後に別の版が公開されたときの取り込み（今は破棄して開き直す）、画面のToolの選択肢と説明（今はフロントエンドに `harness.tools.ALL_TOOLS` と同じ一覧を持つ。APIで返すようにする）。
- 会話からのSkill更新で後回しにしたもの：中身が前の版と同じDraftの公開を止めない（版が増える）、Draftに他の人の未公開の変更があっても一緒に公開する（`get_skill` の説明で、公開の前に利用者へ伝えさせるだけ）、編集のRunの対象は1つだけ（複数のSkillをまとめて直す依頼は分ける）、別のAttemptで再開すると `/workspace/master` のDraftの資料が置き直されない（`get_skill` を呼び直せば置く）、資料の形式ごとの検証（⑤）。
- Sandboxで後回しにしたもの：`/workspace/master` のmount（Skillの段階）、`install_python_packages` と `/packages`（PackageBundle・EnvironmentRevision）、ステップごとのCheckpoint（今はAttemptを終えるときだけ。障害で落ちると、最後のCheckpointより後の変更は失われ、モデルに知らせる）、snapshotの保持期限と削除、非公開のレジストリからのpullの認証。
- 引き継いだ文脈が、それだけでモデルの文脈に収まらないほど大きいとき（前の依頼が文脈の上限で失敗した等）は、要約の要求も収まらず、次の依頼も失敗する。前の依頼の中で圧縮が働くので通常は起きない。
- 成果物とファイルで後回しにしたもの：Storage Backendの `validate_config`・`healthcheck`（セットアップ時の確認。design.md §7.8）、既存のファイルを新しい保存先へ移すコマンド（design.md §7.10。以前の保存先を読む設定 `YORIKI_STORAGE_PREVIOUS` はある）、WorkspaceSnapshotのModel（今はCheckpointが保存先・key・hashを持つ）、保持期限での清掃（`cleanup_artifacts`。期限 `expires` は記録している）と、DBに記録する前に失敗した参照のないobjectの回収、S3の期限付きURL（今はapiが読んで返す）、送らずに残った添付の削除、画像のプレビュー、エージェントの `present_files`（harness.md §10.5）。
- gVisorで後回しにしたもの：rootless Dockerとの組み合わせ（確かめていない。gVisorではuserns-remapを使う）、メモリの超過でSandboxごと止まるのを避ける方法（gVisorの中の上限をホストのcgroupより先に効かせる設定は、runscの外から指定できなかった）、プロセス数の換算（×2＋64）の見直し（試験の値から決めた）。
- `read` の画像で後回しにしたもの：上限（1回の要求で20枚、長辺1568px、`read` の1回で5ページ）をモデル設定の値にする（harness.md §12。上限の小さいローカルLLMで要るとき）、画面の作業の記録でTool結果の画像を見せる、Tool結果の画像のBlobObjectの清掃（Runを消すときや保持期限。今は残り続ける）。
- 補助コマンドが返すパス（symlinkを辿った先）と、モデルが指定したパスが違うと、読んだ版の記録が見つからず `edit` が「まだ読んでいない」になる（symlinkを使うときだけ）。
- 会話の画面で後回しにしたもの：添付と成果物の一覧（Artifactの段階）、入力欄の `/`・`@` での指定（Skill・Connectorの段階）、次の依頼の開始前の編集・取消（design.md §11.2。APIもまだない）、会話のタイトルの変更、実行履歴の画面（`/runs`）。
- Provider Adapterは、実際のプロバイダのAPIではまだ試していない（API keyが要る）。テストは各社の応答を差し替えて確かめている。
- 接続の同時実行数・1分あたりの要求数の上限は、項目があるだけで、まだ守らせていない（Runnerの段階）。
- 契約テストの代表的なコマンドは111件。harness.md §8.5の目標（数百）へ、Skillで使われたコマンドから増やす。
- 認証プロキシの裏に置く場合は、前段のnginxが利用者のIPアドレスを `CF-Connecting-IP` 等で解決して `X-Forwarded-For` を上書きしているかを、導入先で確かめる（deployment.md §8.6）。解決していないと、ログインの試行回数の制限で、多くの利用者が同じIPアドレスとして数えられる。
- ハーネスで後回しにしたもの：予算の上限の強制（今は利用量を記録するだけ。クオータの設定とあわせて作る）、圧縮の1・2段（大きいTool結果の退避と置き換え。harness.md §9.1）、添付ファイル（Artifactの段階）。
- フロントエンドのSPAを静的なファイルにして `yoriki-api` のイメージに入れ、`frontend` のコンテナをなくす（`ssr: false` なのでNode.jsのサーバーは要らない。アプリのイメージが1つになる）。
- 招待のリンクはYorikiからメールで送らない（画面でコピーして渡す）。メールの送信は必要になった時点で追加する（design.md §10.0）。

## 4 次の作業

design.md §12の第1段階を、1つの目的ごとに1つのPull Requestにして進める。上から順に着手する（利用者から別の指示がなければ、1から始める）。提案する順番：

1. **Skill（`skill`、`resources`）**：利用者と合意したPRの分け方は「① 土台（backend。済み） → ② 管理画面（frontend） → ③ 会話での利用 → ④ 会話からのSkill更新 → ⑤ 評価とCSV・PPTXの検証・編集」。④まで済み。次は⑤：Draftの評価（SkillEvaluation、`test_skill_draft`。評価のRunにはSkill公開のToolを出さない）と、資料の形式ごとの検証・編集（MD・CSV・PPTX。design.md §3.7・§3.9・§12）。
   - 読む章：design.md §3.7（差分と競合と評価）、§3.8・§3.9（参照ファイルと形式ごとの検証）、§11.8、§12（受入条件）、harness.md §7.5（評価のRunのTool）・§10.9（Skillの編集のRun）。
2. **Connector（`connector`）とタスク・配信（`task`、`delivery`）**：読取り専用のDB Connector、Connectorを使える利用者・役割と操作（ConnectorPermission。design.md §4.9.1）、入力欄の `@` での指定、Task Definition（使うSkill・モデル・許可する操作）、Webhook・Zapierの通知、Trigger。DB Connector（種別：PostgreSQL・MySQL・SQL Server、ホスト、DB名、ユーザー、パスワード等）とHTTP Connector（ホスト、ヘッダー、認証情報等）の設定は管理画面で登録し、資格情報は `utils/crypto.py` で暗号化してDBに保存する（backend-conventions.md §6.4）。個人情報を主な中身とする接続先には機密のフラグを付けられるようにし、フラグの付いたConnectorはローカルLLMのモデルを使うタスク・会話にだけ割り当てる（design.md §4冒頭、§4.9.1）。
3. **最初の2つの業務**（design.md §13。Skillとタスクとして作る）：請求書PDF → 社内DBと照合 → 取込用のCSV、契約書レビュー → Zapierへ結果。
4. **Channel Plugin**（Teams・Slack・Discord。`plugin` のRegistry）。

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
- LLMのAPI keyがなくても、OpenAI互換の応答を返す小さな偽のサーバーを立て、ローカルLLMの接続として登録すれば、API → Q2 → Runner → SSEを一通り確かめられる（#22で確かめた方法）。開発用のDBには、そのときの確認用のユーザー（`e2e-check@example.co.jp`）と、接続「確認用の偽LLM」・モデル設定「確認用の偽モデル」「確認用の偽モデル（推論）」（effortは low・medium・high）が残っている。モデル設定「社内Qwen」の接続先はつながらないので、再試行の後のモデルの切り替えの確認に使える。偽のサーバーはリポジトリに置いていない。#36 では、`http.server` で `POST /v1/chat/completions` にSSE（`chat.completion.chunk` の並び、最後にusageと `[DONE]`）を返し、要求の `tools` を見て「`skill` があれば一覧の最初を読み込む → 発言・Tool結果に `/workspace/master/...` のパスがあれば `read` → 文で答える」と決める50行ほどのスクリプトを `127.0.0.1:8765` で動かした。開発用のDBの部門のSkill「請求書の照合」（版3）は、資料 `vendor-master.csv` と業務結果のSchema（`{"ok": boolean}`）を持つので、`/` で指定すると `submit_result` が要る。会話からのSkill更新では、同じ偽のサーバーを「要求の `tools` に `get_skill` があれば `get_skill` → `update_skill_draft`（結果の `<draft revision=` を使う）→ `publish_skill_draft`（結果の `now revision` を使う）→ 文で答える。業務のRunで利用者の発言に「Skillも直して」があれば `request_skill_edit`」と決める形にした。部門の「編集」の役割の社員 `skill-member@example.co.jp` で確かめたときのDraftが、Skill「請求書の照合」（版5）に残っている。
- 基盤管理者は `cd api && uv run python src/manage.py createsuperuser` で作る。開発時はDjangoの管理画面（`/api/admin/`）のパスワードログインでセッションを作れる。
- Sandboxのイメージは `docker build -f sandbox/images/docs/Dockerfile -t yoriki-sandbox-docs:dev sandbox` でビルドし、`cd sandbox && uv sync && uv run pytest` で契約テストを動かす（sandbox/README.md）。apiのLauncher・SandboxのToolのテスト（`pytest -m sandbox`）も、このイメージ（`YORIKI_SANDBOX_TEST_IMAGE`）で動く。イメージがなければ飛ばす。
- Sandboxは既定でgVisor（runsc）で動かすが、Docker DesktopにはgVisorがないので、手元の `.envrc` には `YORIKI_SANDBOX_RUNTIME=runc` を設定する（`.envrc.example`）。gVisorでの動きはCIで確かめる（PRでは `sandbox-gvisor`・`sandbox-runner` が動く。PRの前に手元のブランチで動かすなら、pushして `gh workflow run sandbox.yml --ref <ブランチ>`・`gh workflow run backend.yml --ref <ブランチ>`）。手元で試すときは sandbox/README.md のdindの方法を使う。
- Runを手元で動かすと、Workerの作業領域（ジョブの出力、Sandboxに見せる添付）を `YORIKI_WORKSPACES_DIR`（`.envrc.example` ではリポジトリの `.workspaces/`）に作る。添付・成果物・snapshotは `YORIKI_STORAGE_LOCAL_ROOT`（`.envrc.example` ではリポジトリの `.objects/`）に置く。既存の `.envrc` にはこの行を足す（ないと既定の `/var/lib/yoriki/objects` に書けず、アップロードが失敗する）。
- Runtime Profileを手元で登録するときは、手元のレジストリにイメージをpushする：`docker run -d --name yoriki-dev-registry -p 127.0.0.1:5001:5000 registry:3`、`docker tag yoriki-sandbox-docs:dev localhost:5001/yoriki-sandbox-docs:dev && docker push localhost:5001/yoriki-sandbox-docs:dev`。apiは `YORIKI_SANDBOX_INSECURE_REGISTRIES=localhost:5001` を付けて起動し、管理画面で `localhost:5001/yoriki-sandbox-docs@<pushで表示されたdigest>` を登録する。開発用のDBには、この方法で登録した既定のRuntime Profile「文書処理」（linux/arm64）がある。既定のRuntime Profileがないと、会話のRunは始まらない。

- 手元のDocker Desktopの仮想ディスク（37GB）が満杯になると、PostgreSQLが `No space left on device` で止まり、テストが大量にエラーになる。#36 ではビルドキャッシュ（`docker builder prune -f`）を消して空けた。`docker system df` で確かめる。
- 環境変数はリポジトリルートの `.envrc`（direnv）。テストやmypyは、`.envrc` を読み込んだ状態で実行する。エージェントが `.envrc` を読めない設定のときは、`.envrc.example` の `export` の行と、新しく生成した `YORIKI_SECRET_ENCRYPTION_KEYS`（Fernet）・`YORIKI_SANDBOX_RUNTIME=runc` を一時ファイルに書き、リポジトリのルートでコマンドごとに `source` する（`$PWD` を使う行があるため）。`api/` で `source` すると保存先が `api/.objects` になり、既存の資料・添付の原本が見つからない（Sandboxの起動が「cannot prepare ... not found」で失敗する）。
- Sandboxの補助コマンド（`sandbox/helper/`）を変えたら、イメージをビルドし直す（#33で `read` の画像の符号化を変えた）。手元のレジストリのRuntime Profileで会話を動かすときは、pushし直して管理画面で新しいdigestに更新する。`pytest -m sandbox` は `YORIKI_SANDBOX_TEST_IMAGE=yoriki-sandbox-docs:dev` を付けて動かす。
- 画面をブラウザで確かめるときは、api（上記）と `cd frontend && pnpm dev` を起動し、Playwright（Python）で操作する。手元のブラウザのキャッシュ（chromium-1208）に合う版は `uv run --no-project --with playwright==1.58.0 python <script>`。基盤管理者は `/login?method=password` でログインし、`storage_state` を保存して使い回す。SSOの社員はパスワードでログインできないので、`manage.py shell` でその社員のセッションを作り（`BACKEND_SESSION_KEY` は `utils.backends.PasswordBackend`）、Cookie `sessionid` に入れる。開発用のDBには、#35 で使った基盤管理者 `skill-admin@example.co.jp`（パスワード `check-pass-123`）、社員 `skill-member@example.co.jp`（部門「経理部」の編集の役割）、部門のSkill「請求書の照合」（版3まで）が残っている。
- 変更前に、AGENTS.md と、触る範囲の規約を読む。作業の進め方（ブランチ、コミット、Pull Request、CI、マージ）は development.md に従う。
- 作業の区切りでは、本書の §1・§3・§4 を更新する。Pull Requestで進めた作業は、マージの前にそのPull Requestの中で更新する（マージの後に `main` へ別にコミットしない）。
- Codexのレビュー：手元で `codex review --base main -c 'developer_instructions="レビューの指摘とまとめは、すべて日本語で書いてください。"'` を実行し（数分かかる。出力の `[P1]`・`[P2]` の行と末尾を読む）、指摘をコードで確かめて直し（テストも足す）、コミットして、指摘がなくなるまで繰り返す。PRの前に回してよい。
- ブランチの名前は `<種類>/<内容>`（development.md §1。例：`feature/skill-models`）。
- Pull Requestの進め方（AGENTS.md）：コードの変更は手元のブランチで作り、PRはユーザーに依頼されてから作る。マージはユーザーが指示したときに限り行う。本書の状況の更新や誤字の修正のように判断を含まない文書の変更は、PRを作らずに `main` へ直接コミットする。design.md や規約の判断を変える変更はPRにする（development.md §1）。PRごとにCodexのレビューを受け、指摘をコードで確かめてから直す。CIが通ったら報告し、マージの指示を待つ（squash merge。`[skip ci]` は development.md §4.1）。作業の中で見つけた小さな問題は、別のPRにせず、今のPRで直す。PRのページは消せないので、試しのPRや取り消しのPRは作らない。
- 設計の判断を変える話が出たら、コードを書く前に design.md と本書を直し、合意してから実装する。
