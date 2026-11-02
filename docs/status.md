# 開発の状況と次の作業

作業を引き継ぐための文書。何ができていて、何が決まっていて、次に何をするかをまとめる。作業の区切りごとに更新する。

- 最終更新：2026-11-03（文書の整理の残り：design.md・harness.md・README・AGENTS.md・sandbox/README.md を今の実装と突き合わせて直した（このPR）。マージを確かめてから、§4 の「次の作業」の1から始める）
- 何を作るかは [design.md](design.md)（実装の順番は §12）、どう書くかは各規約（[backend-conventions.md](backend-conventions.md)、[frontend-conventions.md](frontend-conventions.md)、[development.md](development.md)、[deployment.md](deployment.md)）を正とする。本書と食い違う場合は、それらを優先する。

---

## 1 できているもの

| PR | 内容 |
|---|---|
| （初期） | 設計書と規約、フロントエンドの雛形（Nuxt 4、Nuxt UI、i18n、`D*` ラッパー、APIクライアント） |
| #1 | `api/` の雛形（Django、DRF、allauth、Django Q2）、`user` アプリ、社内SSO、ログインと権限の判定 |
| #2・#4・#5 | GitHub Actions・Dependabot、`[skip ci]`、Node.js 26 |
| #6 | Dockerfile、Compose一式（単一ホスト）、nginx、app-imagesのCI |
| #8・#9 | フロントエンドの土台の残り（エラー処理、`layouts/manage.vue`、Vitest）、開発時の静的ファイル |
| #12・#14 | `llm` アプリ（LLMの接続・モデル設定・Provider Adapter）とその管理画面 |
| #15〜#18 | 認証の見直し（design.md §10.0）：SSOの設定の管理画面、Yorikiのアカウント（招待・パスワード）、Secretを持つModelはDjango管理画面で見るだけ |
| #19〜#21 | 設計：標準のハーネス1つとSkill（design.md 0.19）、Webhookのpayloadのファイル化（0.20）、[harness.md](harness.md)（0.21） |
| #22・#24 | 会話と実行のバックエンド（`conversation`・`run`・`harness`・`stream`・`usage`）、qclusterでは接続プールを使わない |
| #25 | 会話の画面 |
| #26・#27 | Sandboxのイメージと `sandbox/CONTRACT.md`、Runtime Profile（`runtime`） |
| #30・#31 | LauncherとSandboxのTool、gVisorの既定化（design.md §7.2） |
| #32・#33 | `artifact` アプリ（Storage Backend、成果物とファイル）、`read` の画像 |
| #34・#35 | `skill` アプリ（Skill・Draft・版）とSkillの画面 |
| #36・#37 | 会話でSkillを使う（harness.md §10.1）、会話からのSkill更新（design.md §11.6、harness.md §10.9） |
| #40・#41 | 資料の形式ごとの検証と差分（design.md §3.9）、Draftの評価（§3.7.1） |
| #42・#44 | Connectorの土台（design.md §4.9.1・§4.9.2）、DB接続の分離と管理画面 |
| #45・#46 | 権限の設計（[authorization.md](authorization.md)）と実装：所属とpermissionの2軸 |
| #47・#48 | 会話でのConnector（`datasource_*`、`@`、機密のConnector）、Skillが宣言するConnectorと機密のSkill |
| #49〜#52 | タスクの定義と版・タスクのRun（design.md §4.1）、信頼しない内容の囲み（harness.md §6.3・§7.7）、Webhookの入口とファイルの取り込み（§4.2） |
| #55〜#57 | 配信（design.md §4.3〜§4.5）：出口・Delivery・再試行、出口へ渡す成果物と画面の通知、通知の書き方（#57の通知Skillは #58 で置き換えた） |
| #58・#59・#68 | タスクの画面（design.md §11.9）、出口・入口の管理画面、タスクのRunの画面と配信の状態・通知の再試行 |
| #69〜#71 | チャットからのタスクの起動、タスクの修正チャット（design.md §11.11、harness.md §10.10） |
| #72・#73 | Skillのスクリプトからの呼出し `yoriki_connector`（design.md §4.9.6、CONTRACT.md 契約2）と作業の記録での表示 |

各PRの中身は、Pull Requestの説明と設計書を見る。

まだ実際の環境で試していないもの：
- 実際のプロバイダのLLMのAPI（接続の確認、画像の入力、Skill・Connectorの選び方、`request_skill_edit` の使い分け、Draftの評価の判定の質）。
- 実際のIdP（Entra ID・Google Workspace）でのログイン（client IDの発行が要る）。
- 実際の社内DBへのConnector、HTTPSの出口と実際のZapier。
- Linuxのホスト（本番の構成のqcluster・userns-remap）でのgVisorの会話のRun、Composeの構成でのnginxの `X-Accel-Redirect`。

## 2 実装の中で決めたこと

設計書・規約に反映済み。経緯を知らずに変えないよう、要点だけを挙げる。

- **権限**：[authorization.md](authorization.md) を正とする（0.33）。permissionの定義は `utils.permissions.AppPermission`（`Meta.permissions` で作る）、所属は `Department.objects.usable_by()`・`manageable_by()`・`joined_by()`、範囲を持つものは各アプリのQuerySetとModelの `can()`。使える部門・管理できる部門のIDは利用者のオブジェクトにキャッシュする（Djangoのpermissionのキャッシュと同じく、所属・Groupを変えた後は使わない）。初期のGroupはデータのマイグレーションで一度だけ作るので、テストの中でflushした後には残らない（テストはGroupに頼らず、`user.factories.grant()` でpermissionを付ける）。
- **パスワードでのログイン**：SSOが有効な間は基盤管理者だけ（`utils.backends.PasswordBackend`）。社員はSSOだけ（`SOCIALACCOUNT_ONLY`）。
- **SSOのアカウント確認**：許可したメールドメイン、Microsoftはテナントを固定、Googleは `hd` の一致が必須（未設定なら起動しない）。
- **テスト**：ローカルでもCIでもPostgreSQLで実行する（SQLiteは使わない）。PostgreSQLが起動していなければ、pytestは起動のコマンドを案内して止まる。NOTIFY/LISTENも実際のPostgreSQLで確かめる。
- **静的ファイル**：本番はnginxが `/api/static/` で返す（`static` サービスが共有ボリュームへコピー）。開発時はUvicornが返す。WhiteNoiseは使わない。
- **CSP**：NuxtのSPAがインラインのスクリプトを含むため、`script-src 'unsafe-inline'` を許可している（課題。§3）。
- **CI**：変更したディレクトリに関係するworkflowだけを動かす。Actionはコミットのsha、公式イメージはdigestで固定し、公開から2週間以内の版は使わない。
- **資料のアプリ名**：Pythonの標準ライブラリの `resource` と重なるので `resources` にした。
- **マージ**：squash mergeを基本にし、コミットを残す価値があるときだけ通常のmerge。`main` に入るコミットには、条件を満たせば `[skip ci]` を付ける（development.md §1、§4.1）。

## 3 まだないもの・課題

- CSPのインラインのスクリプトを、ハッシュかnonceで絞る（deployment.md §4.1）。
- フロントエンド：`app.config.ts`（テーマの色。primary / neutralを決める）、E2E（Playwright）。
- CI：e2e、本体のイメージのレジストリへのpush（使うレジストリが未決。design.md §13）、Sandboxのイメージのmulti-arch（arm64）のビルドとDocker Hubへのpush・脆弱性スキャン（公開先のorgが未決）。
- Compose：channel-listener（Channelの受信を実装するとき）。
- 管理コマンド：`drain_workers`・`resume_workers`（まだない。更新の手順の2と6は飛ばす。deployment.md §6）、孤立したSandboxのコンテナと作業領域（`YORIKI_WORKSPACES_DIR/attempts/`）の定期の回収（今はSandboxを起動するときに残ったコンテナを消すだけ。design.md §8）。
- 業務のアプリは `llm`、`conversation`、`run`、`harness`、`stream`、`usage`、`runtime`（Runtime Profile、Launcher）、`artifact`、`skill`・`resources`、`connector`、`task`、`delivery` まで。`plugin`、`worker` 等（backend-conventions.md §1.2）は、まだない。
- 権限で後回しにしたもの：システムのSkillの詳細を管理画面の中に置く（今は `/manage/system_skills` の一覧から業務画面の `/skills/[id]` を開く）、個人Connector（会話でのConnectorの段階）、業務画面の `/connectors`、ユーザーの更新APIでの所属の書込み（今は部門の所属のAPIで付け外しする。防止策は同じ）、部門の「無効」（今は論理削除で、所属と履歴は残る）、部門のものを別の部門・全社へ移す操作（authorization.md §9）、管理画面の詳細の入力欄を見るだけの人には止める（今は保存のボタンを出さないだけ。ユーザーは止める）。
- Connectorで後回しにしたもの：Plugin Registry（`plugin` アプリ。今は種別からAdapterをコードで選ぶ。Channel Pluginの段階で、タスク・配信のPluginとあわせて作る）、個人Connector、業務画面の `/connectors`、`datasource_*` を並べて実行する（今は同時実行の枠と記録をDBで扱うので1つずつ）、実行中の会話で `@` を指定したメッセージを今の作業に取り込む（今は次の依頼）、会話のConnectorの結果の件数・合計の大きさの上限（今は保持期限の切れたものを置かないだけで、Attemptごとに残りをすべて置く。添付とあわせて決める）、Toolからの呼出しの1つのRunでの回数・頻度の上限（スクリプトからの呼出しには定数の上限がある。design.md §4.9.6）、SQL ServerのSNAPSHOT分離（DB側で有効なら使う。design.md §4.9.2）、MariaDB（MySQLの `MAX_EXECUTION_TIME` がない。試していない）、HTTP・SharePoint・Google Drive・MCPのConnector、書込みの操作。PostgreSQLの証明書の確認（`verify-full`）は、OSの証明書（`sslrootcert=system`。libpq 16以降）を使い、社内のCAの証明書を指定する項目はまだない。PyMySQLは1.2.1〜1.2.3の不具合（作ったばかりのユーザーがTLSなしで初めてログインすると失敗する）を避けて1.2.0に固定している（`api/pyproject.toml`）。直った版が出たら上げる。
- Skillのスクリプトからの呼出しで後回しにしたもの（④a・④b）：実行中の画面への知らせ（今はRunが動いている間の5秒ごとの差分の読み直しで、SSEでは送らない）、実際のモデルが案内どおりにスクリプトから呼ぶかの確認（偽のLLMだけで確かめた）、モデルの応答を待つ間の要求の処理（今はジョブを待つ間とステップの区切りだけ。バックグラウンドのジョブのスクリプトは待つ）、どのジョブからの要求かの記録（今はそのときに実行していたToolの呼出しのIDだけ）、回数・頻度の上限をConnectorやRuntime Profileの設定にする（今は定数）、スクリプトから呼べる操作をSkillの宣言でさらに絞る（今はToolと同じ）、HTTP・SharePoint等のConnectorの操作（社内DBの `query`・`describe` だけ）。
- Skillで後回しにしたもの：実行中のRunでSkillを最新の公開版へ切り替える依頼（design.md §3.2）、Resource Collection、MD・CSV・PPTX以外の形式（XLSX・DOCX・PDF等）の検証、PPTXの画像としてのプレビュー（LibreOfficeを使わない方法が未決。design.md §7.6）、本文（説明・手順・Schema）の版の差分の表示（今は変わった項目名だけ。資料の差分は #40 で表示）、公開せずに捨てたDraftの資料の原本の回収、共通Skillから個人用を派生させる操作、Draftを開いた後に別の版が公開されたときの取り込み（今は破棄して開き直す）、画面のToolの選択肢と説明（今はフロントエンドに `harness.tools.ALL_TOOLS` と同じ一覧を持つ。APIで返すようにする）。
- SkillのConnectorの宣言で後回しにしたもの：システムのConnectorを宣言したSkillの評価（今は断る。評価のRunからシステムのConnectorを使えないため。システムのタスクの段階で扱う）、個人Connectorの宣言、Draftだけが機密のSkillの印（一覧と詳細の「機密」は公開中の版で決める。Draftのフォームでは分かる）、Connectorの版で表の名前が変わったときに宣言を直す案内（今は公開時の検証の誤りで分かるだけ）、宣言を加えるときの操作の既定（今は両方を選ぶので、Toolに `datasource_describe` がないと公開時に止まる）。
- **未決の設計の課題：入力の変数は、タスクが先か入口（trigger）が先か**（2026-10-31、⑦bの議論。利用者が「権限モデルのコンセプトに無理がある」として持ち帰り、結論は出ていない）。今はタスクが入力の項目（名前・表示名・型・必須・長さの上限・選択肢）を決め、入口は「payloadのどこから取るか」だけを決める。利用者の指摘は、Zapierのように上流（入口）から作るのが自然で、入口にない変数をタスクで使えるのはおかしい、タスクの定義画面に入れさせる設定が多すぎる、というもの。今の形にした理由は、①手動実行は入口を通らない、②タスクは入口なしで作れる（⑤b）、③スケジュール・チャットからの起動（⑦d）が増えるので、依頼文が頼る変数は起動のしかたによらず同じである必要がある、④入口を連携の管理者、依頼文をタスクの管理者が持つ分け方（authorization.md §5.5）なので、入口が変数を決めると業務の変数を技術側が左右する、の4つ。結論を出すときは design.md §4.1・§4.2 と authorization.md §5.4・§5.5、⑤b（#51）・⑦a（#58）の作り直しになる。入口の画面でサンプルのpayloadから項目を提案する（決めるのはタスク側のまま）折衷案も出た。
- Webhookの入口で後回しにしたもの：URLの取得（許可した取得先と取得用の資格情報を入口に置く形。必要な送り元が出てきたら。design.md §4.2）、型がファイルの項目の形式・大きさの制限（今は入口の本文の上限と、手動実行では添付と同じ上限だけ）、HMAC署名の検証、スケジュールの入口、重複のキーの保持期限（今は消さずに持ち続ける）、受付の記録とpayloadのファイルの清掃（Runの会話とあわせて）、入口ごとの頻度の上限（今はnginxの `/api/hooks/` のrate limitだけ）。
- タスクで後回しにしたもの：タスクごとのConnectorの取得件数の上限とToolの引数の制限（authorization.md §5.4）、管理できる人へのRunの状態の通知（ユーザー宛てのSSEは起動した人だけ。タスクの画面の実行の履歴は、自分が手動実行したRunの状態の通知で読み直し、ほかの人・入口からのRunは「読み直す」ボタンで読む）、Skillの新しい版の知らせ（今はタスクの画面の「新しい版があります」だけ）、タスクのRunの会話の清掃、タスクのRunの再実行（今のタスクの定義で新しいRunを作り、入力を引き継ぐ。authorization.md §6。入口から受けたファイルの項目は、添付を新しいRunへ写す処理も要る。⑦cで利用者と決めて後回しにした。今は手動実行で入力し直す）、版の履歴の中身と差分の表示（今は版の番号・変更の理由・保存した人だけ）。チャットからの起動で後回しにしたもの（⑦d-1）：出口が画面の通知でないタスクで、起動した人へ画面の通知を出すこと（今は出口が画面の通知のときだけ起動した人に届き、ほかは会話のカードとユーザー宛ての `run.status` だけ）、候補の検索（今は200件まで読み、`/` の一覧の中で絞る）、再読み込みでの二重の起動（起動の要求がnginxからDjangoへ渡り終わり、Djangoがファイルの保存とRun・カードのトランザクションを処理している間に画面を読み込み直すと、起動のIDは画面のメモリにしかないので失われる。再読み込みした画面のメッセージの取得がコミットより先に返ってカードが出ず、利用者が同じタスクをもう一度起動すると、別のIDなので2つ目のRunができる。Djangoは切断で同期のビューのスレッドを止めないので、1つ目もコミットされる。処理はふつう1秒もかからない。利用者の判断で、まれな事象として直していない。直すなら、起動の前に、起動のIDつき・`task_run` が空の「受付中」のカード（`ConversationMessage`）を先に作る要求を分け、起動の処理が済んだらカードにRunを結ぶ形がよい（新しいモデルもRunの状態も要らない。受付中のまま一定の時間が過ぎたカードは「受け付けられませんでした」にする。Runを先に受付中で作る形は、固定するものが空のRunとWorker・一覧・見える範囲で扱う新しい状態が要るので避ける。どの形でも、再読み込みで画面の入力は消える）。手動実行・チャットから起動したタスクのRunの状態の通知（ログインユーザー宛ての `run.status`）で、サイドバーの会話の一覧が一覧に出さない会話を取りに行き、404で一覧から外す（表示には出ないが、要求が1回むだになる）。
- 修正チャットで後回しにしたもの（⑦d-2a・⑦d-2b）：修正チャットでのSkillの修正（ふつうの会話のSkillの編集で行う）、出口へ送る内容の全体のプレビュー（今は `prepare_notification` の文章と渡すファイルだけ。通知の書き方がなければ「送っていません」とだけ出す）、タスクの画面から自分の修正チャットの一覧を開く（今はサイドバーの会話の一覧から開く）。再読み込みでの二重の実行（要求のIDは画面のメモリに持つので、送った後に応答の前に再読み込みして送り直すと、別のRunになりうる。⑦d-1 の起動と同じ制約で、出口へは送らない）。修正チャットのタスクのRunも、固定したSkillが `todo_write` を許さなければ作業の計画を書けない（ほかのタスクのRunと同じ）。
- 会話からのSkill更新で後回しにしたもの：中身が前の版と同じDraftの公開を止めない（版が増える）、Draftに他の人の未公開の変更があっても一緒に公開する（`get_skill` の説明で、公開の前に利用者へ伝えさせるだけ）、編集のRunの対象は1つだけ（複数のSkillをまとめて直す依頼は分ける）、別のAttemptで再開すると `/workspace/master` のDraftの資料が置き直されない（`get_skill` を呼び直せば置く）。
- 資料の検証で後回しにしたもの：検証の結果に検証の仕組みの版を持たせていない（検証の規則を変えても、保存した結果は取り直すまで古いまま）、MDの差分は2MBまで、PPTXの「編集した候補」かどうかは置いた場所（`/workspace/input` か）だけで決める、CSVの行の差分はキーの列がなければ行の組合せの増減だけ、10MBを超えるCSVはキー・型・先頭の0・差分を調べない（全行を持たない方法が要る）。
- Draftの評価で後回しにしたもの：エージェントによる評価ケースの作成（今は画面だけ。design.md §3.7の「例示を公開先に適した内容へ一般化する」とあわせて決める）、評価の結果の画面での作業の記録（今は最後の応答・業務結果・失敗の理由だけ。評価のRunは依頼者の一覧に出さない会話にある）、評価のRunの会話・添付の清掃、評価のRunの同時実行の上限（今はQ2の順番待ちに任せる）、予算の上限（利用量の記録だけ）、停止中のSkillの評価（今は始めない）、編集のRunが評価を待つ間のRunの状態の表示（今は「確認待ち」で、画面の案内で「評価しています」と示す）、判定の指示文の調整（実際のモデルで試していない）。
- 公式のスキルの取り込みとNode.jsで後回しにしたもの（2026-11-02、利用者と決めた）：外部のSkill（`SKILL.md` と `scripts/`・`references/` 等のフォルダ）の取り込み（frontmatterと本文・資料をYorikiのSkillへどう写すか、Draft → 評価 → 公開を通すか、誰が取り込めるか）、Sandboxへの Node.js と npm のパッケージ（ネットワークがないので、イメージへの同梱か、PackageBundleのnpm版が要る。イメージを分けるか）。文書系の公式スキルは Node.js（docx-js、html2pptx）と LibreOffice に頼り、ライセンスで再配布できないものがある（記憶によるもの。作る前に現物で確かめる）。LibreOffice は大きすぎるので入れない。
- Sandboxで後回しにしたもの：`install_python_packages` と `/packages`（PackageBundle・EnvironmentRevision）、ステップごとのCheckpoint（今はAttemptを終えるときだけ。障害で落ちると、最後のCheckpointより後の変更は失われ、モデルに知らせる）、snapshotの保持期限と削除、非公開のレジストリからのpullの認証。
- 引き継いだ文脈が、それだけでモデルの文脈に収まらないほど大きいとき（前の依頼が文脈の上限で失敗した等）は、要約の要求も収まらず、次の依頼も失敗する。前の依頼の中で圧縮が働くので通常は起きない。
- 成果物とファイルで後回しにしたもの：Storage Backendの `validate_config`・`healthcheck`（セットアップ時の確認。design.md §7.8）、既存のファイルを新しい保存先へ移すコマンド（design.md §7.10。以前の保存先を読む設定 `YORIKI_STORAGE_PREVIOUS` はある）、WorkspaceSnapshotのModel（今はCheckpointが保存先・key・hashを持つ）、保持期限での清掃（`cleanup_artifacts`。期限 `expires` は記録している）と、DBに記録する前に失敗した参照のないobjectの回収、S3の期限付きURL（今はapiが読んで返す）、送らずに残った添付の削除、画像のプレビュー、エージェントの `present_files`（harness.md §10.5）。
- gVisorで後回しにしたもの：rootless Dockerとの組み合わせ（確かめていない。gVisorではuserns-remapを使う）、メモリの超過でSandboxごと止まるのを避ける方法（gVisorの中の上限をホストのcgroupより先に効かせる設定は、runscの外から指定できなかった）、プロセス数の換算（×2＋64）の見直し（試験の値から決めた）。
- `read` の画像で後回しにしたもの：上限（1回の要求で20枚、長辺1568px、`read` の1回で5ページ）をモデル設定の値にする（harness.md §12。上限の小さいローカルLLMで要るとき）、画面の作業の記録でTool結果の画像を見せる、Tool結果の画像のBlobObjectの清掃（Runを消すときや保持期限。今は残り続ける）。
- 補助コマンドが返すパス（symlinkを辿った先）と、モデルが指定したパスが違うと、読んだ版の記録が見つからず `edit` が「まだ読んでいない」になる（symlinkを使うときだけ）。
- 会話の画面で後回しにしたもの：次の依頼の開始前の編集・取消（design.md §11.2。APIもまだない）、会話のタイトルの変更、実行履歴の画面（`/runs`）。
- Provider Adapterは、実際のプロバイダのAPIではまだ試していない（API keyが要る）。テストは各社の応答を差し替えて確かめている。
- LLMの接続の同時実行数・1分あたりの要求数の上限は、項目があるだけで、まだ守らせていない（DB接続の同時実行数は守らせている）。
- 契約テストの代表的なコマンドは111件。harness.md §8.5の目標（数百）へ、Skillで使われたコマンドから増やす。
- 認証プロキシの裏に置く場合は、前段のnginxが利用者のIPアドレスを `CF-Connecting-IP` 等で解決して `X-Forwarded-For` を上書きしているかを、導入先で確かめる（deployment.md §8.6）。解決していないと、ログインの試行回数の制限で、多くの利用者が同じIPアドレスとして数えられる。
- ハーネスで後回しにしたもの：予算の上限の強制（今は利用量を記録するだけ。クオータの設定とあわせて作る）、圧縮の2段（古いTool結果の置き換え。1段の大きいTool結果の退避はある。harness.md §9.1）、再開したRunを保存したsystem promptから組み立て直す（今は毎ステップ今のコードから作り、`request.header` にhashを記録するだけ。harness.md §6.1）、知らない種類のRunEventで止める（今は読み飛ばす。§5.2）、引数の形の明らかな誤りを直して実行する（今はエラーで返す。§7.6）、SandboxのないRunの大きいTool結果の退避（今は切るだけ。§7.7）、要約の後に読み込んだSkillの本文を読み込み直す（§9.2）、プロバイダの再試行のばらつきと `Retry-After`（今は1・2・4・8・16秒。§10.8）、知らないToolの呼出しの繰返しの検出（§11.2）、operation keyを持つ操作の障害の後の呼び直し（今は `unknown` で閉じる。§11.1）、`tool_choice` の名前の指定（§12）、モデルの切り替えを言葉で頼む（今は画面だけ。design.md §11.3）。
- フロントエンドのSPAを静的なファイルにして `yoriki-api` のイメージに入れ、`frontend` のコンテナをなくす（`ssr: false` なのでNode.jsのサーバーは要らない。アプリのイメージが1つになる）。
- 配信で後回しにしたもの：保存先の設定の誤りの検出（apiとWorkerを別のサーバーにしたのにLocal保存のままだと、Workerが書いた成果物をapiが読めず、取得が失敗するだけ。起動時の確認はまだない。deployment.md §8はS3を必須にしている）、出口へ送る内容に結果を確かめる画面へのリンクを入れること（画面の通知からはタスクのRunの画面へ移れる（⑦c））、通知の一覧の画面（今はサイドバーの最近の20件だけ）と通知の保持期限・清掃、ファイルの取得の記録（FileLink・FileFetch）の保持期限と清掃、渡すファイルの数・合計の大きさの上限（今は回収の上限（`MAX_OUTPUT_FILES`）だけ）、汎用のWebhookへファイルを本文に載せて送る方法（外へ開く口を増やしたくない導入先が出てきたら）、実際のZapierでのファイルの取得の確認、Cloudflareの裏での `/api/files/` のrate limitの送り元のIPアドレス（nginxの `real_ip`。`/api/hooks/` も同じ）、出口の接続の確認（試しに送る操作）、Plugin Registry（今は出口の種類からAdapterをコードで選ぶ）、送る直前の確認と送信の間の停止（確かめた後に停止しても、始めた1回の送信は止めない。出口の行をHTTPの間ロックし続けないため）、配信と配信の試行の保持期限と清掃、egressのproxy（`HTTPS_PROXY`。確かめたアドレスへ直接つなぐので使わない。proxyが要る導入先が出たら、proxyの側で宛先を絞る形を考える）、通知の内容の `result` と `status_and_result` の違い（今は同じ。結果を確かめる画面へのリンクを足すときに `status` 側に入れる）、`run.waiting_user`（対話の連携の段階）。
- 通知の書き方（以前の通知Skill）で後回しにしたもの：1つのタスクに複数の通知（例：失敗は画面の通知、結果はZapier。NotificationBindingを別のModelにし、配信キーにその版を加える）、通知の項目のSchema（許可項目。Zapierで項目ごとに使いたい導入先が出てきたら）、会話からの `request_delivery`、通知Pluginに同梱する既定の通知の書き方の雛形（Plugin Registryとあわせて）、通知の候補を作ったかどうかの画面での表示（今は作業の記録のTool呼出しで分かるだけ）、実際のモデルでの確認（偽のLLMのテストだけ）、権限を失った人の画面に取得済みの通知の文章が残る（開いたままの通知欄は、次に読み直すまで消えない。会話・Runの画面と同じで、権限の変更を画面へ知らせる仕組みはまだない）。
- 招待のリンクはYorikiからメールで送らない（画面でコピーして渡す）。メールの送信は必要になった時点で追加する（design.md §10.0）。

## 4 次の作業

**今の状態（2026-11-03）**：design.md §12の第1段階のうち、Skill（①〜⑤）と、Connector・タスク・配信（①〜⑦、Connectorの④a・④b）は終わった（#72・#73まで）。今は、業務（下の「第1段階の順番」の3）を作る前の整理を、利用者と決めた次の順で進めている。1（文書の整理）は #74 と、その残り（design.md・harness.md の突き合わせ）のPRで行った。#74 では、status.md の終わった段階の「決めたこと・判断した点」を設計書と突き合わせ、設計書になかった今の動きに関わるものを design.md・authorization.md・harness.md・frontend-conventions.md・sandbox/CONTRACT.md へ移してから消した（経過・レビュー・確かめたことは消した。各PRの説明に残っている）。§1 はPRごとの短い一覧にした。

**次の作業**：
1. **テストの高速化**（PR）：バックエンドのテスト全体（1104件）は手元で約4分半かかる（テスト本体216秒、準備48秒、後片付け8秒。一番遅い1件でも4秒で、時間は全体に広がっている）。`pytest-xdist` で並列にする（プロセスごとのテスト用DB、Sandboxのテストのコンテナ名・作業領域が重ならないか、競合のテスト（`TestMembershipRace` 等。ロックを待って1件2秒ほど）を並列でも安定させる、待つ時間を短くする）。CIの `backend` もあわせて速くする。フロントエンドの、手元のmacOSでcomposablesのテストが「Hook timed out」で止まる問題（§5）も調べる。
2. **確認用の道具をリポジトリに入れる**（PR）：偽のLLM（OpenAI互換。応答の台本を指定できる）と、確認用のデータ（ユーザー、モデル設定、Runtime Profile、DB接続・Connectorと確認用のスキーマ等）を入れる管理コマンド。今は毎回 `/tmp` にスクリプトを書き直していて、開発用のDBには削除済みの確認用のデータが溜まっている。入れたら §5 の確認の手順を短くする。
3. **未決の設計の課題に結論を出す**（§3の「入力の変数は、タスクが先か入口が先か」）：コードより先に利用者と話して決める。
4. **最初の2つの業務**（下の3）：何をSkill・タスク・Connector・出口で用意するか、足りない機能、PRの分け方を利用者と決めてから作る。
- 後回しにしたもの（利用者と決めた）：公式のスキル（`SKILL.md` の形のフォルダ）の取り込みと、Sandboxへの Node.js（npm）のエコシステムの追加。文書系の公式スキルは Node.js と LibreOffice に頼り、ライセンスで同梱できないものがある（確かめてから決める）。LibreOffice は大きすぎるので入れない（design.md §7.6 の方針のまま）。

**第1段階の順番**（design.md §12。1つの目的ごとに1つのPull Requestにして、上から進める）：

1. **Skill**：済み（#34〜#41）。後回しにしたものは §3。
2. **Connector・タスク・配信**：済み（#42〜#73）。範囲の外：HTTP・SharePoint・MCPのConnector、Teams・Slack・DiscordのInteraction Binding（下の4）。
   - design.md §12 の第1段階のうち、ほかに残るもの（§3）：Plugin Registry（下の4とあわせて）、クオータと予算の強制、清掃（`cleanup_artifacts` 等）、`install_python_packages`・PackageBundle・EnvironmentRevision、Sandboxのイメージの Docker Hub への配布、実行履歴の画面（`/runs`）。順番は、最初の2つの業務で要るものから利用者と決める。
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
- LLMのAPI keyがなくても、OpenAI互換の応答を返す小さな偽のサーバーを立て、ローカルLLMの接続として登録すれば、API → Q2 → Runner → SSEを一通り確かめられる（#22で確かめた方法）。開発用のDBには、そのときの確認用のユーザー（`e2e-check@example.co.jp`）と、接続「確認用の偽LLM」・モデル設定「確認用の偽モデル」「確認用の偽モデル（推論）」（effortは low・medium・high）が残っている。モデル設定「社内Qwen」の接続先はつながらないので、再試行の後のモデルの切り替えの確認に使える。偽のサーバーはリポジトリに置いていない。#36 では、`http.server` で `POST /v1/chat/completions` にSSE（`chat.completion.chunk` の並び、最後にusageと `[DONE]`）を返し、要求の `tools` を見て「`skill` があれば一覧の最初を読み込む → 発言・Tool結果に `/workspace/master/...` のパスがあれば `read` → 文で答える」と決める50行ほどのスクリプトを `127.0.0.1:8765` で動かした。開発用のDBの部門のSkill「請求書の照合」（版3）は、資料 `vendor-master.csv` と業務結果のSchema（`{"ok": boolean}`）を持つので、`/` で指定すると `submit_result` が要る。会話からのSkill更新では、同じ偽のサーバーを「要求の `tools` に `get_skill` があれば `get_skill` → `update_skill_draft`（結果の `<draft revision=` を使う）→ `publish_skill_draft`（結果の `now revision` を使う）→ 文で答える。業務のRunで利用者の発言に「Skillも直して」があれば `request_skill_edit`」と決める形にした。部門の「編集」の役割の社員（今は所属とGroup「部門のSkill編集者」） `skill-member@example.co.jp` で確かめたときのDraftが、Skill「請求書の照合」（版5）に残っている。
- 基盤管理者は `cd api && uv run python src/manage.py createsuperuser` で作る。開発時はDjangoの管理画面（`/api/admin/`）のパスワードログインでセッションを作れる。
- Sandboxのイメージは `docker build -f sandbox/images/docs/Dockerfile -t yoriki-sandbox-docs:dev sandbox` でビルドし、`cd sandbox && uv sync && uv run pytest` で契約テストを動かす（sandbox/README.md）。apiのLauncher・SandboxのToolのテスト（`pytest -m sandbox`）も、このイメージ（`YORIKI_SANDBOX_TEST_IMAGE`）で動く。イメージがなければ飛ばす。
- Sandboxは既定でgVisor（runsc）で動かすが、Docker DesktopにはgVisorがないので、手元の `.envrc` には `YORIKI_SANDBOX_RUNTIME=runc` を設定する（`.envrc.example`）。gVisorでの動きはCIで確かめる（PRでは `sandbox-gvisor`・`sandbox-runner` が動く。PRの前に手元のブランチで動かすなら、pushして `gh workflow run sandbox.yml --ref <ブランチ>`・`gh workflow run backend.yml --ref <ブランチ>`）。手元で試すときは sandbox/README.md のdindの方法を使う。
- Runを手元で動かすと、Workerの作業領域（ジョブの出力、Sandboxに見せる添付）を `YORIKI_WORKSPACES_DIR`（`.envrc.example` ではリポジトリの `.workspaces/`）に作る。添付・成果物・snapshotは `YORIKI_STORAGE_LOCAL_ROOT`（`.envrc.example` ではリポジトリの `.objects/`）に置く。既存の `.envrc` にはこの行を足す（ないと既定の `/var/lib/yoriki/objects` に書けず、アップロードが失敗する）。
- Runtime Profileを手元で登録するときは、手元のレジストリにイメージをpushする：`docker run -d --name yoriki-dev-registry -p 127.0.0.1:5001:5000 registry:3`、`docker tag yoriki-sandbox-docs:dev localhost:5001/yoriki-sandbox-docs:dev && docker push localhost:5001/yoriki-sandbox-docs:dev`。apiは `YORIKI_SANDBOX_INSECURE_REGISTRIES=localhost:5001` を付けて起動し、管理画面で `localhost:5001/yoriki-sandbox-docs@<pushで表示されたdigest>` を登録する。開発用のDBには、この方法で登録した既定のRuntime Profile「文書処理」（linux/arm64）がある。既定のRuntime Profileがないと、会話のRunは始まらない。

- 手元のDocker Desktopの仮想ディスク（37GB）が満杯になると、PostgreSQLが `No space left on device` で止まり、テストが大量にエラーになる。#36 ではビルドキャッシュ（`docker builder prune -f`）を消して空けた。`docker system df` で確かめる。
- 環境変数はリポジトリルートの `.envrc`（direnv）。テストやmypyは、`.envrc` を読み込んだ状態で実行する。エージェントが `.envrc` を読めない設定のときは、`.envrc.example` の `export` の行と、新しく生成した `YORIKI_SECRET_ENCRYPTION_KEYS`（Fernet）・`YORIKI_SANDBOX_RUNTIME=runc` を一時ファイルに書き、リポジトリのルートでコマンドごとに `source` する（`$PWD` を使う行があるため）。`api/` で `source` すると保存先が `api/.objects` になり、既存の資料・添付の原本が見つからない（Sandboxの起動が「cannot prepare ... not found」で失敗する）。
- Sandboxの補助コマンド（`sandbox/helper/`）・ライブラリ（`sandbox/python/`）を変えたら、イメージをビルドし直す（#33で `read` の画像の符号化を変えた）。④aでSandboxの契約を2に上げた。手元の開発用のDBのRuntime Profile「文書処理」は、④bで契約2のイメージ（第2版）に更新した。手元のレジストリのRuntime Profileで会話を動かすときは、pushし直して管理画面で新しいdigestに更新する。`pytest -m sandbox` は `YORIKI_SANDBOX_TEST_IMAGE=yoriki-sandbox-docs:dev` を付けて動かす。
- 会話でのConnectorを手元で確かめるとき（#47 で行った方法）：開発用のDB（`yoriki-dev-postgres-1`）の中に確認用のスキーマ（例 `connector_check`）と読取り用のロールを作り（`GRANT USAGE ON SCHEMA`・`GRANT SELECT`）、管理画面か `manage.py shell` でそのロールのPostgreSQLの接続と、範囲が全社のConnector（既定のスキーマを確認用のスキーマに）を登録する。偽のLLMは、要求の `tools` に `datasource_query` があれば `datasource_describe` → `datasource_query`（`params` は文字列）→ 結果の `file:` の行のパスを `read` → 文で答える、と決める70行ほどのOpenAI互換のスクリプトを `127.0.0.1:8765` で動かした（リポジトリには置いていない）。接続のパスワードは今の暗号化の鍵で暗号化されるので、`.envrc` と違う鍵で作った接続は確かめた後に消す（`rotate_secret_encryption` が読めない行で止まらないように）。機密のConnectorを試すときは、会話を機密で始めるか、会話の設定の盾のボタンで機密にする（ローカルLLMのモデルが要る。開発用のDBの「確認用の偽モデル」はローカルLLM）。今の開発用のDBにConnectorと接続は残っていない（どれも削除済み）。 ④bで、確認用のスキーマ `connector_check`（表 `vendors`）・読取り用のロール `connector_check_ro`・DB接続「確認用（④b）」・全社のConnector「確認用DB」を作り直した（それまでのものは削除済みだった）。
- ConnectorのAdapterのテスト（`connector/tests/test_adapters.py`）は、PostgreSQLの分はいつも動き、MySQL・SQL Serverの分は管理者の接続先（`YORIKI_CONNECTOR_TEST_MYSQL`・`YORIKI_CONNECTOR_TEST_SQLSERVER`。`.envrc.example`）を渡したときだけ動く。#42 では一時のコンテナ（`docker run -d --rm --name yoriki-connector-mysql -p 127.0.0.1:3307:3306 -e MYSQL_ROOT_PASSWORD=... mysql:8.0`、`docker run -d --rm --platform linux/amd64 --name yoriki-connector-mssql -p 127.0.0.1:1434:1433 -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD=... mcr.microsoft.com/mssql/server:2022-latest`）で試した。手元のmacOSのODBC Driver 18（18.7.1.1まで）は `/opt/homebrew/opt/openssl/lib/` のOpenSSL 3以前を探すので、Homebrewの `openssl` が4系だと読み込めない。`export DYLD_LIBRARY_PATH=/opt/homebrew/opt/openssl@3/lib` を付け、`uv run` ではなく `.venv/bin/python -m pytest ...` で動かす（`uv run` を通すと `DYLD_LIBRARY_PATH` がPythonに届かない）。本体のイメージ（`docker build -t yoriki-api:dev api`）の中で、リポジトリを読取り専用でmountして動かしてもよい（`--user root` で `pip install uv`・`UV_PROJECT_ENVIRONMENT=/tmp/venv uv sync --locked`・`uv run --no-sync pytest ...`。DBのホストは `host.docker.internal`）。手元の `mysql@8.4`（Homebrew）は利用者のものなので使わない。
- 画面をブラウザで確かめるときは、api（上記）と `cd frontend && pnpm dev` を起動し、Playwright（Python）で操作する。手元のブラウザのキャッシュ（chromium-1208）に合う版は `uv run --no-project --with playwright==1.58.0 python <script>`。基盤管理者は `/login?method=password` でログインし、`storage_state` を保存して使い回す。SSOの社員はパスワードでログインできないので、`manage.py shell` でその社員のセッションを作り（`BACKEND_SESSION_KEY` は `utils.backends.PasswordBackend`）、Cookie `sessionid` に入れる。開発用のDBには、#35 で使った基盤管理者 `skill-admin@example.co.jp`（パスワード `check-pass-123`）、社員 `skill-member@example.co.jp`（部門「経理部」の所属とGroup「部門のSkill編集者」。以前の「編集」の役割の代わり）、委任したユーザー管理者 `user-admin@example.co.jp`（Group「ユーザー管理者」。パスワードはないので、セッションを作って使う）、システムのSkill「部門横断の照合（確認用）」（未公開）、部門のSkill「請求書の照合」（版3まで）が残っている。
- 手元のmacOS（Node.js 26.12.1）では、フロントエンドの単体テストのうちコンポーネントをマウントしないファイル（`test/nuxt/composables/`、`api/client.test.ts` 等）が、Nuxtの環境の準備で止まって「Hook timed out」になる（`main` でも同じ。CIでは通る。原因は未調査）。コンポーネントのテストのファイルと一緒に `pnpm vitest run <ファイル>` で動かすと、同じ実行の中では通ることがある。
- 変更前に、AGENTS.md と、触る範囲の規約を読む。作業の進め方（ブランチ、コミット、Pull Request、CI、マージ）は development.md に従う。
- 作業の区切りでは、本書の §1・§3・§4 を更新する。Pull Requestで進めた作業は、マージの前にそのPull Requestの中で更新する（マージの後に `main` へ別にコミットしない）。マージは人間が指示して行うので、「次の作業」には「マージする」とは書かず、マージされる前提で、マージの後に始める作業を書く。
- セッションを始めるときは、「次の作業」の前提のPRが実際にマージされたことを確かめてから進める（`gh pr view <番号>` か git の履歴）。マージされていなければ、進めずに利用者に伝える。
- Codexのレビュー：手元で `codex review --base main -c 'developer_instructions="<指示>"'` を実行し（数分かかる。出力の `[P1]`・`[P2]` の行と末尾を読む）、指摘をコードで確かめて直し（テストも足す）、コミットして、指摘がなくなるまで繰り返す。PRの前に回してよい。`--base` と引数の指示文は一緒に使えないので、見る対象は `developer_instructions` で指定する。変更の種類で見る対象を変える：
  - 文書だけの変更（`docs/` 等）：文書を対象にする。指示は「レビューの指摘とまとめは、すべて日本語で書いてください。この変更は文書だけです。変えた文書と、関係する docs/ の文書（design.md・authorization.md・harness.md・各規約・status.md）の間の食い違い、古くなった記述、今の実装（api/・frontend/）と合わない記述を見てください。」
  - コードの変更：コードと文書を対象にする。指示は「レビューの指摘とまとめは、すべて日本語で書いてください。コードの不具合に加えて、変更が docs/ の設計書・規約（design.md・authorization.md・harness.md・各規約）と食い違っていないか、文書の直し漏れがないか（status.md を含む）も見てください。」
- 敵対的なレビュー：普通のレビューと並行して、`node ~/.claude/plugins/cache/openai-codex/codex/<版>/scripts/codex-companion.mjs adversarial-review --wait --base main "<日本語で答えること、見る対象、重点、議論し直さなくてよい合意済みの方針>"` も回す（権限や設計の変更で特に効く）。見る対象は普通のレビューと同じく、文書だけの変更なら文書、コードの変更ならコードと文書（設計書・規約との食い違いと直し漏れ）にする。毎回何かを出す性質なので、指摘が細部の詰めに移ったら、利用者と相談して区切る（#45 では15回で区切った）。
- ブランチの名前は `<種類>/<内容>`（development.md §1。例：`feature/skill-models`）。
- Pull Requestの進め方（AGENTS.md）：コードの変更は手元のブランチで作り、PRはユーザーに依頼されてから作る。マージはユーザーが指示したときに限り行う。本書の状況の更新や誤字の修正のように判断を含まない文書の変更は、PRを作らずに `main` へ直接コミットする。design.md や規約の判断を変える変更はPRにする（development.md §1）。PRごとにCodexのレビューを受け、指摘をコードで確かめてから直す。CIが通ったら報告し、マージの指示を待つ（squash merge。`[skip ci]` は development.md §4.1）。作業の中で見つけた小さな問題は、別のPRにせず、今のPRで直す。PRのページは消せないので、試しのPRや取り消しのPRは作らない。
- 設計の判断を変える話が出たら、コードを書く前に design.md と本書を直し、合意してから実装する。
