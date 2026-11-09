# 開発の状況と次の作業

作業を引き継ぐための文書。何ができていて、何が決まっていて、次に何をするかをまとめる。作業の区切りごとに更新する。

- 最終更新：2026-11-09（agentセッションの実装の④サブエージェントの画面（#92）まで。次は⑤ワークスペースの保存期限の設定、アーカイブと論理削除、添付・成果物の期限をなくす）
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
| #74・#75 | 文書の整理（status.md の決めたことを設計書へ移し、設計書・README を今の実装と突き合わせた） |
| #76 | テストの並列化（pytest-xdist。development.md §3.1）、残ったSandboxの掃除を同じDBのものに限る（ラベル `org.yoriki.database`） |
| #77 | 確認用の道具（`devtools`：`seed_dev`・`fake_llm`・`dev_session`。development.md §6） |
| #78・#79 | Webhookの入口をタスクを管理できる人が作る形（設計：design.md 0.45、authorization.md §5.5・§13。実装：入口のAPIと画面をタスクの下へ移し、1つのタスクに複数の入口、鍵を作った人の記録と警告、`/manage/trigger_bindings` は見るだけの一覧） |
| #80 | 新案：agentセッション（design.md 0.47 §5・§7・§8・§11.11、harness.md §5.6・§8・§10.11 へ反映した）、Sandboxのイメージ（design.md 0.46 §7.6 へ反映した）。実装は §4 の順に進める |
| #84 | design.md §4 の構成図を実装に合わせた（0.48。LLMとConnectorの呼出しはqclusterのRunner。Control Planeの定義） |
| #85 | ワークスペースの保存基準（答える人がいるか。design.md 0.49 §7.9）、snapshotを確定した2世代にする、修正チャットをふつうの会話と同じ扱いにする |
| #86 | Runの区切りと `WAITING_BACKGROUND`（design.md 0.50 §5.3、harness.md §4.3・§4.4・§8.3）：親が話し終えても確認待ちでもジョブを止めず、同じAttemptで見張って親を起こす。Attemptの55分の上限 |
| #91 | サブエージェントのバックエンド（harness.md §10.11、design.md §5.3・§7.9）：`subagent`・`subagent_kill`、子のRunner（親のワークスペースのコピーの上で別のAttempt・別のSandbox）、返すテキストとファイル（`/workspace/input/subagents/<子のID>/`）、子だけを待つ間はWorkerを手放す `WAITING_BACKGROUND`、停止・Workerの喪失・実行時間の上限・機密への切替え。画面は親の呼出しと `subagent.finished` だけを出す |
| #92 | サブエージェントの画面（harness.md §10.11、realtime.md §3）：作業の記録・SSE・照会のAPIで子の記録も返し（`subagent` に子のID）、子の状態のAPI（`/runs/{id}/subagents/`）を足した。作業の記録で `subagent` の呼出しの下に子の状態と記録を入れ子で出し、動いている子の数を出す。子の途中経過は流さない。偽のLLMの台本に `[subagent]` |
| #87 | 発言の渡し方（design.md 0.51 §5.5・§11.4、harness.md §7.5・§10.1・§10.2・§10.4）：発言の種類の選択をなくし、`/`・`@` を指定した発言も親へ渡す。Skill・Connectorの一覧を発言ごとに作り直し、`skill`・`connector` の引数を文字列にする。Runがアクセスできるものを権限モデルだけで決め、`/`・`@`、タスクの定義、Skillの宣言と `tools` は使用の指示にする（タスク・評価のRunも絞らない） |

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
- Connectorで後回しにしたもの：Plugin Registry（`plugin` アプリ。今は種別からAdapterをコードで選ぶ。Channel Pluginの段階で、タスク・配信のPluginとあわせて作る）、個人Connector、業務画面の `/connectors`、`datasource_*` を並べて実行する（今は同時実行の枠と記録をDBで扱うので1つずつ）、会話のConnectorの結果の件数・合計の大きさの上限（今は保持期限の切れたものを置かないだけで、Attemptごとに残りをすべて置く。添付とあわせて決める）、Toolからの呼出しの1つのRunでの回数・頻度の上限（スクリプトからの呼出しには定数の上限がある。design.md §4.9.6）、SQL ServerのSNAPSHOT分離（DB側で有効なら使う。design.md §4.9.2）、MariaDB（MySQLの `MAX_EXECUTION_TIME` がない。試していない）、HTTP・SharePoint・Google Drive・MCPのConnector、書込みの操作。PostgreSQLの証明書の確認（`verify-full`）は、OSの証明書（`sslrootcert=system`。libpq 16以降）を使い、社内のCAの証明書を指定する項目はまだない。PyMySQLは1.2.1〜1.2.3の不具合（作ったばかりのユーザーがTLSなしで初めてログインすると失敗する）を避けて1.2.0に固定している（`api/pyproject.toml`）。直った版が出たら上げる。
- Skillのスクリプトからの呼出しで後回しにしたもの（④a・④b）：実行中の画面への知らせ（今はRunが動いている間の5秒ごとの差分の読み直しで、SSEでは送らない）、実際のモデルが案内どおりにスクリプトから呼ぶかの確認（偽のLLMだけで確かめた）、モデルの応答を待つ間の要求の処理（今はジョブを待つ間とステップの区切りだけ。バックグラウンドのジョブのスクリプトは待つ）、どのジョブからの要求かの記録（今はそのときに実行していたToolの呼出しのIDだけ）、回数・頻度の上限をConnectorやRuntime Profileの設定にする（今は定数）、スクリプトから呼べる操作をSkillの宣言でさらに絞る（今はToolと同じ）、HTTP・SharePoint等のConnectorの操作（社内DBの `query`・`describe` だけ）。
- Skillで後回しにしたもの：Resource Collection、MD・CSV・PPTX以外の形式（XLSX・DOCX・PDF等）の検証、PPTXの画像としてのプレビュー（LibreOfficeをイメージに入れた後に作れる。design.md §7.6）、本文（説明・手順・Schema）の版の差分の表示（今は変わった項目名だけ。資料の差分は #40 で表示）、公開せずに捨てたDraftの資料の原本の回収、共通Skillから個人用を派生させる操作、Draftを開いた後に別の版が公開されたときの取り込み（今は破棄して開き直す）、画面のToolの選択肢と説明（今はフロントエンドに `harness.tools.ALL_TOOLS` と同じ一覧を持つ。APIで返すようにする）。
- SkillのConnectorの宣言で後回しにしたもの：個人Connectorの宣言、Draftだけが機密のSkillの印（一覧と詳細の「機密」は公開中の版で決める。Draftのフォームでは分かる）、Connectorの版で表の名前が変わったときに宣言を直す案内（今は画面の気になる点の知らせで分かるだけ）、宣言を加えるときの操作の既定（今は両方を選ぶので、Toolに `datasource_describe` がないと公開時に止まる）。
- Webhookの入口で後回しにしたもの：入口の一覧で警告のあるものだけに絞る（今は印を見るだけ）、1つのタスクの中で入口の名前を重ねない（今は重ねられる）、URLの取得（許可した取得先と取得用の資格情報を入口に置く形。必要な送り元が出てきたら。design.md §4.2）、型がファイルの項目の形式・大きさの制限（今は入口の本文の上限と、手動実行では添付と同じ上限だけ）、HMAC署名の検証、スケジュールの入口、重複のキーの保持期限（今は消さずに持ち続ける）、受付の記録とpayloadのファイルの清掃（Runの会話とあわせて）、入口ごとの頻度の上限（今はnginxの `/api/hooks/` のrate limitだけ）。
- タスクで後回しにしたもの：管理できる人へのRunの状態の通知（ユーザー宛てのSSEは起動した人だけ。タスクの画面の実行の履歴は、自分が手動実行したRunの状態の通知で読み直し、ほかの人・入口からのRunは「読み直す」ボタンで読む）、Skillの新しい版の知らせ（今はタスクの画面の「新しい版があります」だけ）、タスクのRunの会話の清掃、本番のタスクのRunの再実行（今のタスクの定義で新しいRunを作り、入力を引き継ぐ。authorization.md §6。⑦cで利用者と決めて後回しにした。実行記録からの修正チャットは design.md §11.11 で実装する）、版の履歴の中身と差分の表示（今は版の番号・変更の理由・保存した人だけ）。チャットからの起動で後回しにしたもの（⑦d-1）：出口が画面の通知でないタスクで、起動した人へ画面の通知を出すこと（今は出口が画面の通知のときだけ起動した人に届き、ほかは会話のカードとユーザー宛ての `run.status` だけ）、候補の検索（今は200件まで読み、`/` の一覧の中で絞る）、再読み込みでの二重の起動（起動の要求がnginxからDjangoへ渡り終わり、Djangoがファイルの保存とRun・カードのトランザクションを処理している間に画面を読み込み直すと、起動のIDは画面のメモリにしかないので失われる。再読み込みした画面のメッセージの取得がコミットより先に返ってカードが出ず、利用者が同じタスクをもう一度起動すると、別のIDなので2つ目のRunができる。Djangoは切断で同期のビューのスレッドを止めないので、1つ目もコミットされる。処理はふつう1秒もかからない。利用者の判断で、まれな事象として直していない。直すなら、起動の前に、起動のIDつき・`task_run` が空の「受付中」のカード（`ConversationMessage`）を先に作る要求を分け、起動の処理が済んだらカードにRunを結ぶ形がよい（新しいモデルもRunの状態も要らない。受付中のまま一定の時間が過ぎたカードは「受け付けられませんでした」にする。Runを先に受付中で作る形は、固定するものが空のRunとWorker・一覧・見える範囲で扱う新しい状態が要るので避ける。どの形でも、再読み込みで画面の入力は消える）。手動実行・チャットから起動したタスクのRunの状態の通知（ログインユーザー宛ての `run.status`）で、サイドバーの会話の一覧が一覧に出さない会話を取りに行き、404で一覧から外す（表示には出ないが、要求が1回むだになる）。
- 修正チャットで後回しにしたもの（⑦d-2a・⑦d-2b）：修正チャットでのSkillの修正（ふつうの会話のSkillの編集で行う）、出口へ送る内容の全体のプレビュー（今は `prepare_notification` の文章と渡すファイルだけ。通知の書き方がなければ「送っていません」とだけ出す）、タスクの画面から自分の修正チャットの一覧を開く（今はサイドバーの会話の一覧から開く）。再読み込みでの二重の実行（要求のIDは画面のメモリに持つので、送った後に応答の前に再読み込みして送り直すと、別のRunになりうる。⑦d-1 の起動と同じ制約で、出口へは送らない）。
- 会話からのSkill更新で後回しにしたもの：中身が前の版と同じDraftの公開を止めない（版が増える）、Draftに他の人の未公開の変更があっても一緒に公開する（`get_skill` の説明で、公開の前に利用者へ伝えさせるだけ）、編集のRunの対象は1つだけ（複数のSkillをまとめて直す依頼は分ける）、別のAttemptで再開すると `/workspace/master` のDraftの資料が置き直されない（`get_skill` を呼び直せば置く）。
- 資料の検証で後回しにしたもの：検証の結果に検証の仕組みの版を持たせていない（検証の規則を変えても、保存した結果は取り直すまで古いまま）、MDの差分は2MBまで、PPTXの「編集した候補」かどうかは置いた場所（`/workspace/input` か）だけで決める、CSVの行の差分はキーの列がなければ行の組合せの増減だけ、10MBを超えるCSVはキー・型・先頭の0・差分を調べない（全行を持たない方法が要る）。
- Draftの評価で後回しにしたもの：エージェントによる評価ケースの作成（今は画面だけ。design.md §3.7の「例示を公開先に適した内容へ一般化する」とあわせて決める）、評価の結果の画面での作業の記録（今は最後の応答・業務結果・失敗の理由だけ。評価のRunは依頼者の一覧に出さない会話にある）、評価のRunの会話・添付の清掃、評価のRunの同時実行の上限（今はQ2の順番待ちに任せる）、予算の上限（利用量の記録だけ）、停止中のSkillの評価（今は始めない）、編集のRunが評価を待つ間のRunの状態の表示（今は「確認待ち」で、画面の案内で「評価しています」と示す）、判定の指示文の調整（実際のモデルで試していない）。
- 外部のSkillの取り込みで後回しにしたもの（2026-11-02、利用者と決めた）：外部のSkill（`SKILL.md` と `scripts/`・`references/` 等のフォルダ）の取り込み（frontmatterと本文・資料をYorikiのSkillへどう写すか、Draft → 評価 → 公開を通すか、誰が取り込めるか）。いったん検討の外。必要な道具はイメージに入れる（design.md §7.6）。
- Sandboxで後回しにしたもの：`install_python_packages` と `/packages`（PackageBundle・EnvironmentRevision）、snapshotの保持期限での削除（1つ前より古いsnapshotは新しいものを確定した後に、答える人がいないRunのものは終わるときに消す。消せなかったものは行が残り、同じ会話で次に消すときにやり直す。答える人がいないRunの会話には次がないので、消せなかったものは定期の回収を作るまで残る）、確定する前に落ちたsnapshotの実体の回収、非公開のレジストリからのpullの認証。
- 引き継いだ文脈が、それだけでモデルの文脈に収まらないほど大きいとき（前の依頼が文脈の上限で失敗した等）は、要約の要求も収まらず、次の依頼も失敗する。前の依頼の中で圧縮が働くので通常は起きない。
- 成果物とファイルで後回しにしたもの：Storage Backendの `validate_config`・`healthcheck`（セットアップ時の確認。design.md §7.8）、既存のファイルを新しい保存先へ移すコマンド（design.md §7.10。以前の保存先を読む設定 `YORIKI_STORAGE_PREVIOUS` はある）、WorkspaceSnapshotのModel（今はCheckpointが保存先・key・hashを持つ）、ワークスペースの保存期限での自動削除（`cleanup_workspaces`）、添付と成果物の個別の削除、`Artifact.expires` と `YORIKI_ARTIFACT_RETENTION_DAYS` をなくす（実装で行う。design.md §8）、DBに記録する前に失敗した参照のないobjectの回収、S3の期限付きURL（今はapiが読んで返す）、送らずに残った添付の削除、画像のプレビュー、エージェントの `present_files`（harness.md §10.5）。
- gVisorで後回しにしたもの：rootless Dockerとの組み合わせ（確かめていない。gVisorではuserns-remapを使う）、メモリの超過でSandboxごと止まるのを避ける方法（gVisorの中の上限をホストのcgroupより先に効かせる設定は、runscの外から指定できなかった）、プロセス数の換算（×2＋64）の見直し（試験の値から決めた）。
- `read` の画像で後回しにしたもの：上限（1回の要求で20枚、長辺1568px、`read` の1回で5ページ）をモデル設定の値にする（harness.md §12。上限の小さいローカルLLMで要るとき）、画面の作業の記録でTool結果の画像を見せる、Tool結果の画像のBlobObjectの清掃（Runを消すときや保持期限。今は残り続ける）。
- 補助コマンドが返すパス（symlinkを辿った先）と、モデルが指定したパスが違うと、読んだ版の記録が見つからず `edit` が「まだ読んでいない」になる（symlinkを使うときだけ）。
- 会話の画面で後回しにしたもの：会話のタイトルの変更、実行履歴の画面（`/runs`）。
- Provider Adapterは、実際のプロバイダのAPIではまだ試していない（API keyが要る）。テストは各社の応答を差し替えて確かめている。
- LLMの接続の同時実行数・1分あたりの要求数の上限は、項目があるだけで、まだ守らせていない（DB接続の同時実行数は守らせている）。
- 契約テストの代表的なコマンドは111件。harness.md §8.5の目標（数百）へ、Skillで使われたコマンドから増やす。
- 認証プロキシの裏に置く場合は、前段のnginxが利用者のIPアドレスを `CF-Connecting-IP` 等で解決して `X-Forwarded-For` を上書きしているかを、導入先で確かめる（deployment.md §8.6）。解決していないと、ログインの試行回数の制限で、多くの利用者が同じIPアドレスとして数えられる。
- ハーネスで後回しにしたもの：予算の上限の強制（今は利用量を記録するだけ。クオータの設定とあわせて作る）、圧縮の2段（古いTool結果の置き換え。1段の大きいTool結果の退避はある。harness.md §9.1）、再開したRunを保存したsystem promptから組み立て直す（今は毎ステップ今のコードから作り、`request.header` にhashを記録するだけ。harness.md §6.1）、知らない種類のRunEventで止める（今は読み飛ばす。§5.2）、引数の形の明らかな誤りを直して実行する（今はエラーで返す。§7.6）、SandboxのないRunの大きいTool結果の退避（今は切るだけ。§7.7）、要約の後に読み込んだSkillの本文を読み込み直す（§9.2）、プロバイダの再試行のばらつきと `Retry-After`（今は1・2・4・8・16秒。§10.8）、知らないToolの呼出しの繰返しの検出（§11.2）、operation keyを持つ操作の障害の後の呼び直し（今は `unknown` で閉じる。§11.1）、`tool_choice` の名前の指定（§12）、モデルの切り替えを言葉で頼む（今は画面だけ。design.md §11.3）。
- フロントエンドのSPAを静的なファイルにして `yoriki-api` のイメージに入れ、`frontend` のコンテナをなくす（`ssr: false` なのでNode.jsのサーバーは要らない。アプリのイメージが1つになる）。
- 配信で後回しにしたもの：保存先の設定の誤りの検出（apiとWorkerを別のサーバーにしたのにLocal保存のままだと、Workerが書いた成果物をapiが読めず、取得が失敗するだけ。起動時の確認はまだない。deployment.md §8はS3を必須にしている）、出口へ送る内容に結果を確かめる画面へのリンクを入れること（画面の通知からはタスクのRunの画面へ移れる（⑦c））、通知の一覧の画面（今はサイドバーの最近の20件だけ）と通知の保持期限・清掃、ファイルの取得の記録（FileLink・FileFetch）の保持期限と清掃、渡すファイルの数・合計の大きさの上限（今は回収の上限（`MAX_OUTPUT_FILES`）だけ）、汎用のWebhookへファイルを本文に載せて送る方法（外へ開く口を増やしたくない導入先が出てきたら）、実際のZapierでのファイルの取得の確認、Cloudflareの裏での `/api/files/` のrate limitの送り元のIPアドレス（nginxの `real_ip`。`/api/hooks/` も同じ）、出口の接続の確認（試しに送る操作）、Plugin Registry（今は出口の種類からAdapterをコードで選ぶ）、送る直前の確認と送信の間の停止（確かめた後に停止しても、始めた1回の送信は止めない。出口の行をHTTPの間ロックし続けないため）、配信と配信の試行の保持期限と清掃、egressのproxy（`HTTPS_PROXY`。確かめたアドレスへ直接つなぐので使わない。proxyが要る導入先が出たら、proxyの側で宛先を絞る形を考える）、通知の内容の `result` と `status_and_result` の違い（今は同じ。結果を確かめる画面へのリンクを足すときに `status` 側に入れる）、`run.waiting_user`（対話の連携の段階）。
- 通知の書き方（以前の通知Skill）で後回しにしたもの：1つのタスクに複数の通知（例：失敗は画面の通知、結果はZapier。NotificationBindingを別のModelにし、配信キーにその版を加える）、通知の項目のSchema（許可項目。Zapierで項目ごとに使いたい導入先が出てきたら）、会話からの `request_delivery`、通知Pluginに同梱する既定の通知の書き方の雛形（Plugin Registryとあわせて）、通知の候補を作ったかどうかの画面での表示（今は作業の記録のTool呼出しで分かるだけ）、実際のモデルでの確認（偽のLLMのテストだけ）、権限を失った人の画面に取得済みの通知の文章が残る（開いたままの通知欄は、次に読み直すまで消えない。会話・Runの画面と同じで、権限の変更を画面へ知らせる仕組みはまだない）。
- 招待のリンクはYorikiからメールで送らない（画面でコピーして渡す）。メールの送信は必要になった時点で追加する（design.md §10.0）。
- 確認用の道具で後回しにしたもの：確認用のSkill・タスク・出口を `seed_dev` で作る（Skillの公開には評価などの確かめがあり、資料の原本も要る。今は手で作る）、偽のLLMが推論の途中経過を返すことと `tool_choice` で指定されたToolを自動で呼ぶこと（今は台本に書く）。

## 4 次の作業

**今の状態（2026-11-03）**：design.md §12の第1段階のうち、Skill（①〜⑤）と、Connector・タスク・配信（①〜⑦、Connectorの④a・④b）は終わった（#72・#73まで）。今は、業務（下の「第1段階の順番」の3）を作る前の整理を、利用者と決めた次の順で進めている。文書の整理は #74 と、その残り（design.md・harness.md の突き合わせ。#75）で、テストの高速化は #76 で行った（バックエンドのテスト全体は、手元（8コア）で約5分から約35秒に、4並列でも約45秒になった）。#74 では、status.md の終わった段階の「決めたこと・判断した点」を設計書と突き合わせ、設計書になかった今の動きに関わるものを design.md・authorization.md・harness.md・frontend-conventions.md・sandbox/CONTRACT.md へ移してから消した（経過・レビュー・確かめたことは消した。各PRの説明に残っている）。§1 はPRごとの短い一覧にした。確認用の道具は #77 で入れた（偽のLLM `fake_llm`、確認用のデータ `seed_dev`、ログイン済みのセッション `dev_session`。development.md §6）。 2026-11-06 に、agentセッションとSandboxのイメージの案を設計書へ反映した。実装は下の④（サブエージェント）のバックエンドまで進み、そのほかは design.md 0.45 の動きのまま。後回し：タスクの実行の論理削除の画面、物理削除の管理コマンド、監査ログの画面。

**次の作業**（#92 のマージを確かめてから）：
1. 設計書（design.md 0.46・0.47、harness.md §5.6・§8・§10.11）に合わせて実装を直す。1つの目的ごとのPull Requestに分け、次の順に進める（2026-11-06 に利用者と確かめた）。① snapshotを確定した2世代にし、Workerを手放す前に保存する（済み。保存するかは答える人がいるかで決め、修正チャットはふつうの会話と同じ扱いにした）、② Runの区切りと `WAITING_BACKGROUND`、ジョブを親の動きと切り離す（済み）、③ 発言の渡し方と、Skill・Connectorの一覧を発言ごとに作り直す（済み）、④ サブエージェント（済み。バックエンドは #91、画面は #92）、⑤ ワークスペースの保存期限の設定、アーカイブと論理削除、添付・成果物の期限をなくす、⑥ 修正チャット（実行記録から始める）、⑦ Sandboxのイメージ（下の確かめの後）。
   - ②で利用者と決めたこと（2026-11-06。design.md §5.3 に反映した）：回答期限が切れたら、ジョブが動いていても待たずに失敗にする（回答がなければ作業を進められないため）。回答を待つ間にWorkerが落ちても確認を残し、回答で最後の保存から再開する。Attemptの上限で移るときは、答える人がいないRunでも保存する（実装のときに決めた。§7.9）。
   - ②で利用者と決めたこと（続き）：ステップ数の上限で、ジョブが残っているか親が結果を見ていないジョブがあれば、止めて失敗にする（ステップ数はRun全体で数えるので、新しいAttemptでは解消しない。会話では次の発言の新しいRunで続ける）。Runを終えるときに止めたジョブと見ていないジョブは、そのRunの記録に書いて次のRunの文脈に入れる（harness.md §5.6）。
   - ②で入れなかったもの：サブエージェント（`WAITING_BACKGROUND` の「Attemptを手放していた」経路は④で足す）、発言の種類の選択をなくすこと（③）。実行時間（1つのRunの合計60分。harness.md §4.4）の上限はまだ守らせていない（今回入れたのはAttemptの上限だけ）。Attemptの上限はモデルの呼出しやToolの途中では判定しないので、55分の直前に始まった呼出しが5分を超えるとQ2に切られ、障害として最後の保存からやり直す（Codexの指摘。余裕の5分と障害の復旧で足りると判断して直していない。接続のtimeoutは3600秒まで設定できる）。
   - ③で利用者と決めたこと（2026-11-07。design.md §5.5 に反映した）：確認待ちの間に `/`・`@` を指定した発言は、本文を回答にして、指定したものを再開のときに取り込む（`UserInstruction.is_answer`）。指定だけで本文も添付もない発言は受け付けない。
   - ③で実装のときに決めたこと：停止中の発言とRunの終わる直前で取り込めなかった発言は、内部で `NEXT` にして新しいRunで始める。一覧は変わったときだけManifestへ追記し（`Run.current_catalog()`）、`<runtime_context>` を今の一覧で記録し直す（差分だけにはしない。圧縮や新しいAttemptの比較と同じ形にするため）。受け付けた後に使えなくなったSkill・Connectorは、モデルに名前を渡さない。スクリプトからの呼出しの中継は、一覧が空でもConnectorのToolがあれば始める（途中で使えるようになるため）。
   - ③で利用者が示した原則（2026-11-07。authorization.md §1の8・§5.4・§6・§14、design.md 0.51）：利用できることと、使うよう指示することは別。Runがアクセスできるものは権限モデルだけで決まり、会話は開始したユーザー、個人タスクは本人、部門・全社・システムのタスクはその範囲。`/`・`@`、タスクの定義のSkill・Connector、Skillの宣言と `tools` は使用の指示で、範囲を狭めない。#87 で会話・タスク・評価のRunを実装した。Skillについて権限が決めるのは、セッションの権限で選べるかだけ（宣言、停止・失効、必要な能力でRunを止めず制限しない。機密はSkill自身のフラグ。緊急停止は新しく選べなくするだけで、動いているRunは止めない。評価のRunだけは `skill` を出さない）。
   - **決めたこと**（2026-11-07）：システムタスクはSkill・Connectorともシステム・全部門・全社を使える（authorization.md §5.4）。宣言できるConnectorはSkillの範囲で決める（§5.1）。
   - **今後の方向**（2026-11-07 に利用者と確かめた）：まずはこの形（権限モデルの範囲をすべて使える）で作り、タスクは、制御しやすいように、必要が見えてきたところから狭めていく。
   - **決まっていないこと**（利用者に確かめる）：書込みのConnectorの許可。案は、出口と同じくConnector側に「書き込んでよい範囲」を持たせ、既定は同じ範囲だけ（部門で加工したものを全社へ書く業務は、その書込み先を許可する。問題ないかは業務によるので一律に決めない。加工されたかは保証できないので割り切る）。会話の書込みも実装のときに決める。
   - **設計に残る課題**：タスクの再現性（一覧から読むSkillは最新の版）、Draftの評価が依頼した人の権限に左右されること、一覧から省かれたものの名前を調べる手段がないこと、Skillの業務結果のSchemaの意味が会話とタスクで違うこと。
   - チャットから起動したRunは「タスクの範囲 ∩ 起動した人の今の権限」で判定する。起動できる人はタスクより狭い権限を持たないのでタスクの範囲と同じになり、共通部分はその検証を兼ねる（テストで確かめる）。一覧の量の上限は見せ方で、省いたものも名前で使える。Webhookの鍵を破られた場合やモデルへの指示で防げない場合の被害は、範囲と出口の中に収まるものとして割り切る。書込みのConnectorは今はないが対応する。
   - ③で入れなかったもの：サブエージェント（④）。実行記録の画面の作業の記録は、回答に添えた指定の取込みも「追加の指示を取り込みました」と出す。
   - 手元の環境（2026-11-09、#92 の確認の後）：開発用のDB `yoriki` は最新までmigrateした。#92 の画面の確認で、エージェントが一時の鍵（`.envrc` とは別の `YORIKI_SECRET_ENCRYPTION_KEYS`）で `seed_dev` を流したので、確認用のデータ（`check-*`、偽LLMの接続、確認用DBのConnector等）と #92 の確認の会話が入っている。暗号化した値は `.envrc` の鍵では読めないので、使う前にDBを作り直して（`DROP DATABASE yoriki` → `CREATE DATABASE yoriki OWNER yoriki` → `migrate`）、`.envrc` を読み込んだ端末で `createsuperuser`・`seed_dev --sandbox-image ...` を流す（エージェントのDBの削除は自動の許可で止められた）。Sandboxのイメージは手元のレジストリの `sha256:355d92b65937…` で最新（2026-11-09 に作り直して同じdigestを確かめた）。
   - ④で利用者と決めたこと（2026-11-08。#91）：PRはバックエンドと画面の2つに分ける（#91 はバックエンドで、画面には親の `subagent` の呼出しと `subagent.finished` だけを出す。子のRunEventは作業の記録のAPIとSSEから外している）。子のAttemptも、1つのqclusterで同時に動かすAttemptの数（`YORIKI_RUNS_PER_QCLUSTER`）に数える（deployment.md §3.2。子の実行時間の上限はclaimされて動き始めてから数える）。
   - ④で実装のときに決めて文書へ書いたこと：子だけを待ってWorkerを手放すときは、答える人がいないタスクのRunでも保存する（design.md §7.9。Attemptの上限と同じ）。子は新しいAttemptへ移らないので、子の実行時間の上限（Runtime Profileの `subagent_timeout_seconds`）は50分まで（harness.md §4.4）。子のWorkerは返すものを確定して親を起こすだけで、`subagent.finished` は親が次の区切りで記録する（親のTool呼出しと結果の間に挟まないため）。親が受け取る前にRunが終わったら、終わるときに記録して次のRunの文脈に入れる（`Run._close_subagents()`）。`subagent_kill` で止めた子は知らせない。子のステップ数は子ごとに数える（上限は親と同じ200）。`model` はモデル設定の名前。子が最後の応答のときにジョブが動いていれば1回だけ知らせ、それでも終えればジョブを止める（harness.md §10.11）。Attemptの番号は親と子で別に数え（`ExecutionAttempt.subagent`）、RunEvent・ToolInvocation・UsageLedgerは子を持つ（sequenceは親と通し）。子が読み込んだSkillは `Subagent.skills` に持ち、親のManifestへ追記しない。子のConnectorの結果は `Artifact.subagent` を持ち、親の `/workspace/input` には置かない（Codexの指摘で直した）。子の実行時間の上限は応答が届くたびにも確かめるが、最初の応答が届くまでの待ちは接続のtimeout（3600秒まで設定できる）まで切れない（親のAttemptの上限と同じ割り切り）。
   - ④の画面で利用者と決めたこと（2026-11-09。#92。harness.md §10.11 に反映した）：子の記録は親と同じ一覧で返して子のIDを付け、子の状態は別のAPIで返す。子の応答の途中経過（`run.delta`）は流さない（子が同時に複数動くと書きかけが混ざり、親の書きかけとも紛れるため）。動いている子の数をRunの状態の横に出す。1つずつ止める操作は置かない。実装のときに決めたこと：子の記録では会話の画面の読み直し（`useRunLive` の `onEvent`）を呼ばず、親の書きかけの応答も消さない。子は確認待ちの間も動くので、子が動いている間は5秒ごとに子の状態と照会を読み直す。Tool呼出しのIDは親と子で重なりうるので、画面では子ごとに分けてから結ぶ。
   - ④で試していないこと：実際のプロバイダのLLMでの `subagent` の使い方（いつ使うか、指示の書き方、差分の返し方）、子が複数同時に動くときの画面（偽のLLMの台本の `[subagent]` は1つだけ）。#92 で、`fake_llm`（`[subagent]`）・qcluster・Sandbox（runc）で親と子を動かし、実行中と終わった後の作業の記録をPlaywrightで確かめた。偽のLLMとSandboxで、コピーの上で動くこと・増えた／変わったファイルだけ返ること・親が `/workspace/input/subagents/` から読めることを確かめた（`harness/tests/test_subagents_sandbox.py`。手元は runc、#91 のCIの `sandbox-runner` で gVisor）。
   - #87 で利用者が確かめた進め方（2026-11-08）：**仕様の文書が正**。文書を正しく更新し、実装が合っていればPRを作ってよい。議論は仕様のレベルで行い、コードの都合に引っ張られない。アクセス範囲を狭める案を先回りして出さない。Codexのレビューは細かく繰り返さず、敵対的なレビューは使わない。
   - #87 に入っている、話に出ていなかった判断：Skill・タスクの編集のRunにも `skill` とConnectorのToolを出す。Draftの評価は範囲に合わない宣言でも始める（評価の結果に別の部門のデータが出うる）。評価のRunだけは `skill` を出さない。削除済みConnectorの宣言は公開を止めない。
   - ⑤（**次はここから**）の範囲は未確認。design.md §8（清掃と保持期間：保存期限・アーカイブ・論理削除・物理削除）と §11.4、authorization.md を読み、始めるときにコードと照らして案を作り、利用者と確かめる。
   - agentセッションの残り：ワークスペースの保存期限の設定の画面（自動削除はまだ作らない）、アーカイブと論理削除、実行記録からの修正チャット。Runtime Profileのメモリの初期値も見直す。
   - Sandboxのイメージ（design.md §7.6、harness.md §8.5）は、作る前に次を確かめる。試していない。`sandbox/CONTRACT.md` §1 は、入れるPull Requestで契約番号とあわせて直す。
     - gVisorでLibreOfficeが動くか。公開されているスキルの説明には、素の `soffice` がそのサンドボックスでは止まるので専用のスクリプトを使う、とある。runc・gVisorの両方で試す。
     - LibreOfficeの設定の置き場所。`HOME` は `/workspace/work/.home` なので、そのままだと設定がsnapshotに入る。別の場所へ向けるかを決める。
     - 資源の上限。LibreOfficeとNode.jsで足りるメモリ・プロセス数を測り、Runtime Profileの初期値を見直す。
     - 信頼しない文書を開くこと。LibreOfficeのマクロを無効にする設定。ネットワークがないことを前提にしてよいかも確かめる。
     - イメージの大きさとビルド。今は582MB。multi-arch（arm64）のビルドと、Docker Hubへの配布の時間。
     - 配布のライセンス。LibreOffice（MPL）、pandoc・poppler-utils（GPL）、Noto（OFL）、`sharp` が同梱するlibvips（LGPLと記憶。現物で確かめる）を入れて配布してよいか。
     - fontconfigの `monospace`。Noto Sans Mono CJK JP がSansのファイルに入っているはずなので、確かめる。

マイグレーションは後でリセットするので、古い行の移行は考えなくてよい。業務は人間が作り、足りない機能をエージェントが直す。外部のSkillの取り込みは、いったん検討から外す。ほかの作業（クオータと予算の強制、`/runs`、CSP等）の順番は未決。

**第1段階の順番**（design.md §12。1つの目的ごとに1つのPull Requestにして、上から進める）：

1. **Skill**：済み（#34〜#41）。後回しにしたものは §3。
2. **Connector・タスク・配信**：済み（#42〜#73）。範囲の外：HTTP・SharePoint・MCPのConnector、Teams・Slack・DiscordのInteraction Binding（下の4）。
   - design.md §12 の第1段階のうち、ほかに残るもの（§3）：Plugin Registry（下の4とあわせて）、クオータと予算の強制、清掃（`cleanup_workspaces` 等）、`install_python_packages`・PackageBundle・EnvironmentRevision、Sandboxのイメージの Docker Hub への配布、実行履歴の画面（`/runs`）。順番は、最初の2つの業務で要るものから利用者と決める。
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
- 確認用のデータと偽のLLMは、`seed_dev`・`fake_llm`・`dev_session`（development.md §6）で用意する。API keyがなくても、API → Q2 → Runner → SSE を一通り確かめられる。`seed_dev` は `.envrc` を読み込んで流す（DBのパスワードを今の鍵で暗号化するため。違う鍵で作った行は、確かめた後に消す。`rotate_secret_encryption` が読めない行で止まらないように）。開発用のDB `yoriki` は、2026-11-04 に以前の確認のデータごと作り直した（DBを消してmigrateしただけで、ユーザーもいない）。使う前に、`.envrc` を読み込んだ端末で `createsuperuser`（基盤管理者 `devadmin`）と `seed_dev --sandbox-image ...` を流す。以前の確認で作ったSkill（資料と業務結果のSchemaのある「請求書の照合」等）は残っていないので、Skillの確認では作り直す。
- 基盤管理者は `cd api && uv run python src/manage.py createsuperuser` で作る。開発時はDjangoの管理画面（`/api/admin/`）のパスワードログインでセッションを作れる。
- Sandboxのイメージは `docker build -f sandbox/images/docs/Dockerfile -t yoriki-sandbox-docs:dev sandbox` でビルドし、`cd sandbox && uv sync && uv run pytest` で契約テストを動かす（sandbox/README.md）。apiのLauncher・SandboxのToolのテスト（`pytest -m sandbox`）も、このイメージ（`YORIKI_SANDBOX_TEST_IMAGE`）で動く。イメージがなければ飛ばす。バックエンドのテストは既定で並列に動く（`-n auto`）。`--pdb` で1つを調べるときは `-n 0` を付ける。
- Sandboxは既定でgVisor（runsc）で動かすが、Docker DesktopにはgVisorがないので、手元の `.envrc` には `YORIKI_SANDBOX_RUNTIME=runc` を設定する（`.envrc.example`）。gVisorでの動きはCIで確かめる（PRでは `sandbox-gvisor`・`sandbox-runner` が動く。PRの前に手元のブランチで動かすなら、pushして `gh workflow run sandbox.yml --ref <ブランチ>`・`gh workflow run backend.yml --ref <ブランチ>`）。手元で試すときは sandbox/README.md のdindの方法を使う。
- Runを手元で動かすと、Workerの作業領域（ジョブの出力、Sandboxに見せる添付）を `YORIKI_WORKSPACES_DIR`（`.envrc.example` ではリポジトリの `.workspaces/`）に作る。添付・成果物・snapshotは `YORIKI_STORAGE_LOCAL_ROOT`（`.envrc.example` ではリポジトリの `.objects/`）に置く。既存の `.envrc` にはこの行を足す（ないと既定の `/var/lib/yoriki/objects` に書けず、アップロードが失敗する）。
- Runtime Profileを手元で登録するときは、手元のレジストリにイメージをpushし、`seed_dev --sandbox-image` か管理画面で登録する（development.md §6）。既定のRuntime Profileがないと、会話のRunは始まらない（`seed_dev` は、既定がなければ「文書処理（seed_dev）」を既定にする）。

- 手元のDocker Desktopの仮想ディスク（37GB）が満杯になると、PostgreSQLが `No space left on device` で止まり、テストが大量にエラーになる。#36 ではビルドキャッシュ（`docker builder prune -f`）を消して空けた。`docker system df` で確かめる。
- 環境変数はリポジトリルートの `.envrc`（direnv）。テストやmypyは、`.envrc` を読み込んだ状態で実行する。エージェントが `.envrc` を読めない設定のときは、`.envrc.example` の `export` の行と、新しく生成した `YORIKI_SECRET_ENCRYPTION_KEYS`（Fernet）・`YORIKI_SANDBOX_RUNTIME=runc` を一時ファイルに書き、リポジトリのルートでコマンドごとに `source` する（`$PWD` を使う行があるため）。`api/` で `source` すると保存先が `api/.objects` になり、既存の資料・添付の原本が見つからない（Sandboxの起動が「cannot prepare ... not found」で失敗する）。
- Sandboxの補助コマンド（`sandbox/helper/`）・ライブラリ（`sandbox/python/`）を変えたら、イメージをビルドし直す（#33で `read` の画像の符号化を変えた）。④aでSandboxの契約を2に上げた。手元のレジストリのRuntime Profileで会話を動かすときは、pushし直して、`seed_dev --sandbox-image` か管理画面で新しいdigestに更新する。`pytest -m sandbox` は `YORIKI_SANDBOX_TEST_IMAGE=yoriki-sandbox-docs:dev` を付けて動かす。
- 会話でのConnectorは、`seed_dev` の確認用のスキーマ `devtools_check` とConnector「確認用DB（seed_dev）」で確かめる（会話で `@` で指定し、偽のLLMに `[query]`・`[script]` を送る）。機密のConnectorを試すときは、会話を機密で始めるか、会話の設定の盾のボタンで機密にする（ローカルLLMのモデルが要る。偽モデル（seed_dev）はローカルLLM）。
- ConnectorのAdapterのテスト（`connector/tests/test_adapters.py`）は、PostgreSQLの分はいつも動き、MySQL・SQL Serverの分は管理者の接続先（`YORIKI_CONNECTOR_TEST_MYSQL`・`YORIKI_CONNECTOR_TEST_SQLSERVER`。`.envrc.example`）を渡したときだけ動く。#42 では一時のコンテナ（`docker run -d --rm --name yoriki-connector-mysql -p 127.0.0.1:3307:3306 -e MYSQL_ROOT_PASSWORD=... mysql:8.0`、`docker run -d --rm --platform linux/amd64 --name yoriki-connector-mssql -p 127.0.0.1:1434:1433 -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD=... mcr.microsoft.com/mssql/server:2022-latest`）で試した。手元のmacOSのODBC Driver 18（18.7.1.1まで）は `/opt/homebrew/opt/openssl/lib/` のOpenSSL 3以前を探すので、Homebrewの `openssl` が4系だと読み込めない。`export DYLD_LIBRARY_PATH=/opt/homebrew/opt/openssl@3/lib` を付け、`uv run` ではなく `.venv/bin/python -m pytest ...` で動かす（`uv run` を通すと `DYLD_LIBRARY_PATH` がPythonに届かない）。本体のイメージ（`docker build -t yoriki-api:dev api`）の中で、リポジトリを読取り専用でmountして動かしてもよい（`--user root` で `pip install uv`・`UV_PROJECT_ENVIRONMENT=/tmp/venv uv sync --locked`・`uv run --no-sync pytest ...`。DBのホストは `host.docker.internal`）。手元の `mysql@8.4`（Homebrew）は利用者のものなので使わない。
- 画面をブラウザで確かめるときは、api（上記）と `cd frontend && pnpm dev` を起動し、Playwright（Python）で操作する。手元のブラウザのキャッシュ（chromium-1208）に合う版は `uv run --no-project --with playwright==1.58.0 python <script>`。ログインは、`dev_session <ユーザー>` が表示するCookie `sessionid` を入れる（SSOの社員もこれで入れる）か、基盤管理者なら `/login?method=password` でログインして `storage_state` を使い回す。
- 手元のmacOSでは、qclusterが `kill`（SIGTERM）で止まらないことがある（2026-11-03 に、本体のチェックアウトで10月23日〜27日に起動したqclusterが十数個残っていた）。起動した端末でCtrl-Cで止め、残ったものはPIDを確かめて `kill -9` で止める。
- 手元のmacOS（Node.js 26.12.1）で、フロントエンドの単体テストのうちコンポーネントをマウントしないファイル（`test/nuxt/composables/`、`api/client.test.ts` 等）が、Nuxtの環境の準備で止まって「Hook timed out」になったことがある（#44〜#73のころ）。#76 で調べたときは、依存は同じままで、全体・composablesだけ・新しいworktreeでの最初の実行・バックエンドのテストで負荷をかけた状態のどれでも再現しなかった。また出たら、そのときの状態（Vitestのキャッシュ、`.nuxt`、他に動いているもの）を記録する。
- フロントエンドのPRの前は、`pnpm lint`・`pnpm format:check`・`pnpm typecheck`・`pnpm test` を流す（CIの `frontend` はすべて見る）。pytestが途中で落ちてテストDBにデータが残ったときは `--create-db` を付ける。
- 変更前に、AGENTS.md と、触る範囲の規約を読む。作業の進め方（ブランチ、コミット、Pull Request、CI、マージ）は development.md に従う。
- 作業の区切りでは、本書の §1・§3・§4 を更新する。Pull Requestで進めた作業は、マージの前にそのPull Requestの中で更新する（マージの後に `main` へ別にコミットしない）。マージは人間が指示して行うので、「次の作業」には「マージする」とは書かず、マージされる前提で、マージの後に始める作業を書く。
- セッションを始めるときは、「次の作業」の前提のPRが実際にマージされたことを確かめてから進める（`gh pr view <番号>` か git の履歴）。マージされていなければ、進めずに利用者に伝える。
- 文書が正：文書を正しく更新し、実装がそれに合っていることを確かめてからPRを作る。Codexのレビューは細かく繰り返さない（節目に普通のレビューを使うだけ。敵対的なレビューは使わない。2026-11-08、利用者の指示）。Codexのレビューを使うとき：手元で `git fetch origin main` の後に `codex review --base origin/main -c 'developer_instructions="<指示>"'` を実行し（手元の `main` は本体のチェックアウトにあって古いことがある）（数分かかる。出力の `[P1]`・`[P2]` の行と末尾を読む）、指摘をコードで確かめて直す（テストも足す）。`--base` と引数の指示文は一緒に使えないので、見る対象は `developer_instructions` で指定する。変更の種類で見る対象を変える：
  - 文書だけの変更（`docs/` 等）：文書を対象にする。指示は「レビューの指摘とまとめは、すべて日本語で書いてください。この変更は文書だけです。変えた文書と、関係する docs/ の文書（design.md・authorization.md・harness.md・各規約・status.md）の間の食い違い、古くなった記述、今の実装（api/・frontend/）と合わない記述を見てください。」
  - コードの変更：コードと文書を対象にする。指示は「レビューの指摘とまとめは、すべて日本語で書いてください。コードの不具合に加えて、変更が docs/ の設計書・規約（design.md・authorization.md・harness.md・各規約）と食い違っていないか、文書の直し漏れがないか（status.md を含む）も見てください。」
- ブランチの名前は `<種類>/<内容>`（development.md §1。例：`feature/skill-models`）。
- Pull Requestの進め方（AGENTS.md）：コードの変更は手元のブランチで作り、PRはユーザーに依頼されてから作る。マージはユーザーが指示したときに限り行う。本書の状況の更新や誤字の修正のように判断を含まない文書の変更は、PRを作らずに `main` へ直接コミットする。design.md や規約の判断を変える変更はPRにする（development.md §1）。PRごとにCodexのレビューを受け、指摘をコードで確かめてから直す。CIが通ったら報告し、マージの指示を待つ（squash merge。`[skip ci]` は development.md §4.1）。作業の中で見つけた小さな問題は、別のPRにせず、今のPRで直す。PRのページは消せないので、試しのPRや取り消しのPRは作らない。
- 設計の判断を変える話が出たら、コードを書く前に design.md と本書を直し、合意してから実装する。
