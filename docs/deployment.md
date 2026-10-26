# 配備とオーケストレーションの方針

Docker Compose、Nginx、イメージの作り方と配り方、環境変数、更新手順をまとめる。製品の構成要素と役割は [design.md](design.md) §6・§7.3・§10（認証は§10.0）、リアルタイム通信は [realtime.md](realtime.md)、設定と認証の実装は [backend-conventions.md](backend-conventions.md) §6 を正とし、本書はそれを実際のファイルと手順に落とす方針を定める。

---

## 1 リポジトリの構成

本体・フロントエンド・Sandboxイメージ・配備設定を1つのリポジトリ（モノレポ）で管理する。エージェントがRunner・Sandboxの契約・イメージを1つの変更で更新できるようにするため。

```
yoriki/
├── api/                    # Django（api / qcluster / channel-listener / worker / migrate で共用するイメージ）
├── frontend/               # Nuxt
├── sandbox/                # Sandboxイメージ（Docker Hubで公開。sandbox/README.md）
│   ├── CONTRACT.md         # Runnerとイメージの取り決め（契約番号つき）
│   ├── helper/             # Sandboxの中の補助コマンド yoriki-sandbox（イメージ共通）
│   ├── images/<profile>/   # 用途別（docs、analysis 等）。Dockerfile、requirements.in・requirements.lock
│   └── tests/              # 契約テスト
├── deploy/
│   ├── compose.yaml            # 単一ホストの本番構成
│   ├── compose.dev.yaml        # ローカル開発の補助（PostgreSQL等）
│   ├── compose.cloudflare.yaml # Cloudflare Tunnelで公開するときに重ねる（§8.3）
│   ├── compose.edge.yaml       # 複数台構成のエッジ（cloudflared＋振り分け用nginx、§8.4）
│   ├── compose.app.yaml        # 複数台構成のアプリサーバー（postgresを除く、§8.4）
│   ├── compose.db.yaml         # 複数台構成のDBサーバー（§8.4）
│   ├── compose.qcluster.yaml   # qclusterだけのWorkerサーバー（§3.2）
│   ├── nginx/                  # Nginxの設定（sites/：tls.conf・http.conf、snippets/、edge/）
│   ├── env/                    # 本番の環境変数の一覧（yoriki.env・postgres.env・cloudflared.env の example）
│   └── .env.example            # Composeファイル自体の変数（イメージ名等。秘密情報は書かない）
├── .github/workflows/      # backend.yml、frontend.yml、api-types.yml、app-images.yml、sandbox.yml
├── mise.toml
└── .envrc.example
```

---

## 2 イメージ

| イメージ | 中身 | 使うサービス | 配布先 |
|---|---|---|---|
| `yoriki-api` | `api/`（Django、Runner、Launcher） | api、qcluster、channel-listener、migrate | 非公開のレジストリ（どれを使うかは未決。design.md §13） |
| `yoriki-frontend` | `frontend/` を `nuxt build` したNodeサーバー | frontend | 同上 |
| `nginx`（公式） | 設定は `deploy/nginx/` をmountする | nginx | Docker Hub公式 |
| `postgres`（公式） | — | postgres | Docker Hub公式 |
| `<org>/yoriki-sandbox-<profile>` | `sandbox/images/<profile>/` | Runごとに動的に起動 | 公開Docker Hub（design.md §10.1） |

- `yoriki-api` は1つのイメージを、起動コマンドを変えて複数のサービスで使う（design.md §10）。
- 本体のイメージ（`yoriki-api`、`yoriki-frontend`）は配布しない。導入する人がソースからビルドする。`yoriki-api` には、ライセンス上そのまま再配布できないもの（SQL Server用のMicrosoft ODBC Driver。ビルド時にMicrosoftのリポジトリから取得し、ビルドする人がライセンスに同意する）が入るため（design.md §4.9.2）。
- 本体のイメージには、ビルド時に秘密情報を入れない。`collectstatic` はビルド時に行う。
- Djangoの静的ファイル（管理画面等）はnginxが `/api/static/` で配る。apiのイメージに入れた静的ファイルを、一度だけ動く `static` サービスが共有のボリュームへコピーし、nginxはそれを読み取り専用でmountする。イメージを更新すると `up -d` のたびにコピーし直す（古い版のファイルは、開いたままの画面が使うので消さない）。ファイル名に内容のハッシュが付くので、長くキャッシュさせる。
- Dockerfileは `api/Dockerfile`、`frontend/Dockerfile`。apiは依存をuv（`uv sync --locked --no-dev`）で入れ、root以外のユーザーで動かす。frontendはNode 25以降にcorepackが同梱されないので、pnpmを `mise.toml` と同じ版でnpmから入れる。
- Dockerfileのベースイメージは `mise.toml` のバージョンに合わせる（Node 26、Python 3.13）。`mise.toml` を変えたら、Dockerfileも同じ変更で更新する。
- 公式イメージもタグだけでなくdigestで固定する。更新はDependabot等でdigestの変更を提案させ、レビューしてから取り込む。
- Sandboxイメージは、本体がdigestで固定して参照する（`RuntimeProfileVersion`）。本体のリリースとは連動させない。

### 2.1 Sandboxイメージの公開

- `.github/workflows/sandbox.yml` は、`sandbox/**` の変更時と毎週の定期実行で動く。今はイメージのビルドと契約テストまでで、Docker Hubへのpushは公開先のorgを決めてから加える。
- buildxでamd64とarm64をビルドし、provenanceとSBOMを付けてDocker Hubへpushする。イメージ名は `<org>/yoriki-sandbox-<profile>`、タグは日付（`YYYY.MM.DD`）とする（例：`<org>/yoriki-sandbox-docs:2026.10.16`）。
- 公開したdigest（index＝manifest listのdigest）をworkflowの出力とリリースノートに残す。管理画面でRuntime Profileの新しい版としてdigestを登録すると、以後に始めるRunで使われる（既定のRuntime Profileの場合。design.md §7.3）。
- イメージにはラベル `org.yoriki.sandbox-contract=<契約番号>` を付ける。Runnerが対応しない契約番号のイメージは、管理画面で登録できない。契約を変えたらCONTRACT.mdとラベルの番号を同時に上げ、契約テストで検出できるようにする。
- リポジトリがprivateの間も、Docker Hubのイメージはpublicで公開できる。ただし、レイヤーの中身は誰でも見られるので、秘密情報や社内向けの設定を入れない。

---

## 3 Compose

### 3.1 単一ホストの本番構成（`deploy/compose.yaml`）

| サービス | イメージ | コマンド | ネットワーク | 永続化 |
|---|---|---|---|---|
| nginx | nginx | — | edge | 証明書、設定、objects（RO。Local保存のファイルを `X-Accel-Redirect` で返す） |
| frontend | yoriki-frontend | `node .output/server/index.mjs` | edge | なし |
| api | yoriki-api | `uvicorn app.asgi:application ...` | edge、backend、egress | objects（Local保存のとき） |
| qcluster | yoriki-api | `python manage.py qcluster` | backend、egress | workspaces、objects（Local保存のとき）、Dockerソケット |
| channel-listener | yoriki-api | `python manage.py run_channel_listener` | backend、egress | なし（profile `channels` のときだけ起動。Channelの受信を実装するときにComposeへ追加する） |
| static | yoriki-api | 静的ファイルを共有ボリュームへコピーして終わる | なし | static（nginxと共有） |
| backup | postgres | `pg_dump` | backend | `/var/lib/yoriki/backups`（profile `tools`、cron等で実行） |
| postgres | postgres | — | backend | DB |
| migrate | yoriki-api | `python manage.py migrate` | backend | なし（profile `tools`、手動で実行） |

```yaml
# deploy/compose.yaml の考え方（抜粋。実際のファイルは compose.app.yaml のサービスを extends で取り込み、
# postgres、nginxのTLS、qclusterのDockerソケットを加える）
services:
  api:
    image: ${YORIKI_API_IMAGE:?}
    command: >
      uvicorn app.asgi:application --host 0.0.0.0 --port 8000 --workers ${YORIKI_API_WORKERS:-4}
      --proxy-headers --forwarded-allow-ips="*" --timeout-graceful-shutdown 30
    env_file: [/etc/yoriki/yoriki.env]
    networks: [edge, backend, egress]
    depends_on:
      postgres: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/ping/')"]

  qcluster:
    image: ${YORIKI_API_IMAGE:?}
    command: python manage.py qcluster
    env_file: [/etc/yoriki/yoriki.env]
    networks: [backend, egress]
    volumes:
      # Sandboxはホストのdockerdが起動するので、ホストとコンテナで同じパスにする
      - /var/lib/yoriki/workspaces:/var/lib/yoriki/workspaces
      - /var/run/docker.sock:/var/run/docker.sock
    stop_grace_period: 10m   # 実行中のRunがCheckpointを保存するまで待つ

  channel-listener:
    image: ${YORIKI_API_IMAGE:?}
    command: python manage.py run_channel_listener
    env_file: [/etc/yoriki/yoriki.env]
    networks: [backend, egress]
    profiles: [channels]

  migrate:
    image: ${YORIKI_API_IMAGE:?}
    command: python manage.py migrate --noinput
    env_file: [/etc/yoriki/yoriki.env]
    networks: [backend]
    profiles: [tools]

networks:
  edge: {}
  backend:
    internal: true   # 外へ出られない。外部へ通信するサービスは egress にも参加させる
  egress: {}         # 外へ通信するためのネットワーク（api、qcluster、channel-listener）
```

- **公開するポートはnginxの443（と80のリダイレクト）だけ。** api、postgres等のポートはホストへpublishしない。
- **Dockerソケットはqclusterにだけ渡す。** apiやfrontendには渡さない（design.md §7.3）。Dockerソケットはホストのroot権限と同等なので、qclusterのコンテナには必要なもの以外をmountしない。将来は、Launcherをホスト上の専用プロセスに分けるか、rootless Dockerにする。
- **workspacesはホストとコンテナで同じパスにする。** qclusterの中から見たパスで、ホストのdockerdにbind mountを指示するため。パスがずれると、Sandboxに空のディレクトリがmountされる。ディレクトリは、qclusterのコンテナの利用者（uid 10001）が書けるように前もって作る（§7.1。ないとDockerがrootの持ち物として作り、Workspaceを保存できない）。
- **Sandboxはcomposeのサービスにしない。** LauncherがRunごとに `network_mode: none` 等の制限付きで起動する（design.md §7.2）。
- **外へ通信するサービス**：api（LLM、Connector、外部連携）、qcluster（LLM、配信、イメージのpull）、channel-listener（Slack等への常時接続）は、`backend` に加えて外へ出られる `egress` ネットワークにも参加させる。postgresは参加させない。
- **migrateは自動で実行しない。** apiの起動時に実行すると、複数のプロセスが同時にmigrationを走らせるため。更新手順（§6）で明示的に実行する。

### 3.2 エージェントの実行を別のサーバーに出す

エージェントの実行（Runner・Sandbox）を別のサーバーに出すときは、QClusterだけを動かすWorkerサーバーを追加する（design.md §6.1）。すべてのサーバー（アプリ、Worker、DB、エッジ）は同じ私設ネットワークに置き、私設ネットワークの外のサーバーは想定しない。

#### QClusterだけのWorkerサーバー（`deploy/compose.qcluster.yaml`）

```yaml
# deploy/compose.qcluster.yaml（抜粋）
services:
  qcluster:
    image: ${YORIKI_API_IMAGE:?}
    command: python manage.py qcluster
    env_file: [/etc/yoriki/yoriki.env]
    volumes:
      - /var/lib/yoriki/workspaces:/var/lib/yoriki/workspaces
      - /var/run/docker.sock:/var/run/docker.sock
    stop_grace_period: 10m
    restart: unless-stopped
```

- Django Q2のクラスタは1つにする。どのqclusterも、エージェントの実行（Runner・Sandbox）と、配信・Outbox・清掃などの軽い処理の両方を受け持つ。処理の量は、qclusterを立てる数とworker数で調整する。
- Runは確認待ちまで長く動き、その間workerを1つ占有する。軽い処理が待たされないよう、1つのqclusterで同時に動かすRunの数を `YORIKI_RUNS_PER_QCLUSTER`（既定は worker数 − 1）までにする。上限に達しているqclusterはRunをclaimせず、ReconcilerがRunの実行を送り直す。
- qclusterはSandboxを起動するので、どこで動かす場合もDockerソケットとworkspacesのmountを渡す。アプリサーバーでqclusterを動かさず、Workerサーバーだけで動かしてもよい（その場合、アプリサーバーにはDockerソケットが要らない）。
- WorkerサーバーはDB・S3に直接接続するので、アプリサーバーと同じ `yoriki.env` を使い、同じ私設ネットワークに置く。ファイルの保存先はS3等の共有のものにする（Localは使えない）。
- 台数は実行枠に合わせて増減する。RunのclaimとleaseがDBで行われるので、何台あっても同じRunを二重に実行しない。
- Workerサーバーは外からの接続を受けないので、公開のホスト名やポートを用意しない。
- WorkerサーバーのRunnerも、LLMはProvider Adapter、社内DB・SaaSはConnectorを、HTTPを介さず同じプロセスの中で通す。Bindingの確認や上限、記録はその処理の中で行い、SecretはSandboxに渡さない（design.md §4.8、§4.9、§6.2）。

### 3.3 ローカル開発（`deploy/compose.dev.yaml`）

ローカル開発では、Composeで動かすのは補助サービスだけにする。apiとfrontendは手元で直接起動し、コードの変更をすぐ反映できるようにする。

```bash
docker compose -f deploy/compose.dev.yaml up -d      # PostgreSQL（必要ならメールの確認用サーバー等も）
cd api && uv run uvicorn app.asgi:application --reload --app-dir src
cd api && uv run python src/manage.py qcluster
cd frontend && pnpm dev
```

- 環境変数はリポジトリルートの `.envrc`（direnv）で設定する（`.envrc.example` をコピーする）。
- Sandboxを動かす確認には、手元のDockerを使う。Docker DesktopにはgVisorがないので、`YORIKI_SANDBOX_RUNTIME=runc` にする（`.envrc.example` に設定済み）。gVisorでの動きはCI（`sandbox-gvisor`、`sandbox-runner`）で確かめる。Sandboxイメージを手元で変更するときは `docker build -f sandbox/images/docs/Dockerfile -t yoriki-sandbox-docs:dev sandbox` でビルドし（contextは共通の補助コマンドを含む `sandbox/`）、`YORIKI_SANDBOX_IMAGE_OVERRIDE=yoriki-sandbox-docs:dev` で差し替える（開発時だけ有効な設定にする。`.envrc.example` に例がある）。
- frontendの開発サーバー（3000番）は、`/api` をapi（8000番）へ転送する（Nuxtの `nitro.devProxy`）。画面とAPIが同じオリジンになるので、セッションCookieがそのまま使え、CORSの設定は不要。
- devProxyは `xfwd: true` を付けて転送し、`changeOrigin` は付けない。ブラウザのHostヘッダー（`localhost:3000`）が保たれるので、DjangoのCSRFの確認が通り、開発時のSSOのリダイレクトURIは `http://localhost:3000/api/accounts/...` になる。認証基盤のアプリ登録にもこのURIを登録する。

---

## 4 Nginx

### 4.1 ルーティング

| パス | 転送先 | 設定 |
|---|---|---|
| `/` | frontend:3000 | — |
| `/api/` | api:8000（パスはそのまま転送。Djangoは `/api/` の下にURLを置く） | 通常のAPI |
| `/api/app/v1/stream/`、`/api/app/v1/runs/{id}/stream/` | api:8000 | SSE用（§4.2）。`location ~ ^/api/app/v1/(runs/[^/]+/)?stream/$` で両方に合わせる |
| `/api/hooks/` | api:8000 | Webhook・Channelの受信。bodyの上限を小さくし、rate limitを強くする |
| `/api/admin/`、`/api/schema/` | api:8000 | ログインとpermissionで守る（Django管理画面は基盤管理者、スキーマとSwagger UIはどの環境でもログインした利用者）。IPの制限は導入先が任意で追加する |
| `/api/accounts/` | api:8000 | SSOのログイン開始と認証基盤からのコールバック |
| `/api/files/<token>/` | api:8000 | 出口（Zapier等）へ渡した成果物の期限付き取得。セッションを使わずtokenを確認する。rate limitを付ける。社員向けのダウンロードには使わない（セッションで認証し、取得のたびに閲覧の判定をし直す。authorization.md §7.1） |
| `/_protected/` | （nginxの内部location） | `internal;` を付け、apiの `X-Accel-Redirect` からだけ使う。外から直接は取得できない |
| `/api/ping/` | api:8000 | ヘルスチェック（認証なし） |
| `/api/static/` | （nginxが直接返す） | Djangoの静的ファイル（§2）。長くキャッシュさせる |

- 画面とAPIを同じドメインにまとめる。認証はセッションCookie（`Secure`、`HttpOnly`、`SameSite=Lax`）で行うので、CORSは使わない。
- HTTPは443へリダイレクトし、HTTP/2を有効にする（SSEの複数の接続を1本にまとめるため）。
- `X-Forwarded-For`、`X-Forwarded-Proto` を付けて転送する。Uvicornは `--proxy-headers` で受け取り、Djangoは `SECURE_PROXY_SSL_HEADER` を設定する。
- `X-Forwarded-For` は、nginxが判定した利用者のIPアドレス（`$remote_addr`。前段がある構成では `set_real_ip_from` で解決済み）だけで上書きする。受け取った値に付け足さない（`$proxy_add_x_forwarded_for` を使わない）。付け足すと利用者が書いた値が先頭に残り、Uvicornがそれを `REMOTE_ADDR` にするので、ログインの試行回数の制限（backend-conventions.md §6.2）を逃れられる。apiのポートはnginxからしか届かないようにする（Dockerのネットワークの中だけに開く）。
- HSTS、`X-Content-Type-Options`、`Referrer-Policy`、フロントエンド向けのCSPを付ける（`deploy/nginx/snippets/`）。HSTSはHTTPSで受けたときだけ付ける。
- CSPは、NuxtのSPAがHTMLにインラインのスクリプト（色モードの初期化、`window.__NUXT__` の設定）を含むため、`script-src` に `'unsafe-inline'` を許可している。インラインのスクリプトのハッシュかnonceで絞るのは今後の課題とする。
- nginxの設定は2種類ある。nginxでTLSを終端して直接公開する `sites/tls.conf`（`compose.yaml`）と、前段（Cloudflare Tunnel、認証プロキシ、エッジのnginx）がTLSを終端し、80番で受ける `sites/http.conf`（`compose.app.yaml`、`compose.cloudflare.yaml`）。`http.conf` は前段が付けた `X-Forwarded-For`・`X-Forwarded-Proto` を、私設のアドレスからのものだけ信頼するので、インターネットへ直接公開しない。
- `client_max_body_size` はパスごとに決める（通常のAPIは小さく、添付のアップロードだけ大きくする）。
- 成果物のダウンロード（Local保存）は、apiが権限と期限付きtokenを確認した後、`X-Accel-Redirect` でnginxの内部location（`location /_protected/ { internal; }`）からファイルを返す。nginxから保存先のディレクトリを直接公開しない。

### 4.2 SSE

```nginx
# deploy/nginx/conf.d/yoriki.conf（抜粋）
# /api/app/v1/stream/ と /api/app/v1/runs/{id}/stream/ の両方に合わせる
location ~ ^/api/app/v1/(runs/[^/]+/)?stream/$ {
    proxy_pass http://api:8000;
    include snippets/sse.conf;
}
```

```nginx
# deploy/nginx/snippets/sse.conf
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 1h;
gzip off;
```

SSEのパスにだけ適用する。apiのレスポンスにも `X-Accel-Buffering: no` を付ける（[realtime.md](realtime.md) §4）。

---

## 5 環境変数と秘密情報

- 本番の環境変数は、ホストの `/etc/yoriki/yoriki.env`（所有者root、権限600）に置き、Composeの `env_file` で渡す。
- 変数の一覧は `deploy/env/yoriki.env.example` に、説明と空の値で管理する。新しい変数を追加したら、`.envrc.example` と合わせて更新する。
- PostgreSQLの初期設定は `/etc/yoriki/postgres.env`（`deploy/env/postgres.env.example`）、Cloudflare Tunnelのトークンは `/etc/yoriki/cloudflared.env`（`deploy/env/cloudflared.env.example`）に置く。どれも所有者root、権限600にする。
- Composeファイル自体の変数（`YORIKI_API_IMAGE` 等のイメージのdigest、証明書のディレクトリ、dockerグループのGID）は、`deploy/.env`（`deploy/.env.example` をコピー）に置く。ここには秘密情報を書かない。
- LLMのAPI key、Connector・外部連携の資格情報は、permissionを持つ管理者（Connector管理者等。authorization.md §10）が管理画面で接続と一緒に登録し、DBに暗号化して保存する（`utils/crypto.py`）。apiとqcluster（Workerサーバーのqclusterを含む）の中でだけ復号して使い、APIの応答・画面・ログ・監査ログ・Sandboxへは出さない。画面には「設定済み」と末尾の4文字だけを出す。
- 暗号化の鍵は `YORIKI_SECRET_ENCRYPTION_KEYS`（Fernetの鍵。カンマ区切り）で渡し、DBには置かない。DBやそのバックアップだけが漏れても、Secretは読めない。apiとqcluster（Workerサーバーを含む）で同じ値にする。
  - 生成：`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`（apiのイメージの中で実行できる）。
  - 鍵を失うと、登録したSecretはすべて入力し直しになる。DBのバックアップとは別の場所にも控える。
  - 鍵の交換：新しい鍵を先頭に足して再起動し（古い鍵は残す）、`python manage.py rotate_secret_encryption` で暗号化し直してから、古い鍵を外して再起動する。
  - 鍵が設定されていなくても起動はする。Secretを保存しようとしたときに入力エラーになる。
- クラウドのSecret管理を使う環境では、暗号化の鍵を起動時に環境変数として渡す仕組み（ECSのsecrets、systemdのcredential等）を使う。アプリは渡し方に依存せず、環境変数を読むだけにする。

---

## 6 更新手順

```bash
# 1. イメージのdigestを更新する（deploy/.env の YORIKI_API_IMAGE / YORIKI_FRONTEND_IMAGE）
docker compose -f deploy/compose.yaml pull

# 2. Workerをdrainする（新しいclaimを止め、実行中のRunのCheckpoint保存を待つ）
docker compose -f deploy/compose.yaml exec api python manage.py drain_workers --wait

# 3. バックアップを取る（DBとファイルをセットで）
# 4. migrationを実行する
docker compose -f deploy/compose.yaml run --rm migrate

# 5. サービスを入れ替える
docker compose -f deploy/compose.yaml up -d

# 6. Workerのdrainを解除する（再起動だけでは解除されない）
docker compose -f deploy/compose.yaml exec api python manage.py resume_workers

# 7. 確認する（/api/ping/、管理画面、Runの試行）
```

- design.md §10の順序（イメージ → migration → 起動）に従う。
- `drain_workers`・`resume_workers` は、Workerの実行（Run）を実装するときに作る。それまでは手順2と6を飛ばす。
- migrationは、旧バージョンのコードでも動く形（列の追加は先、削除は次のリリース）で書き、更新中に旧apiが動いていても壊れないようにする。
- Workerサーバー（§3.2）は、手順2のdrainの後、手順6の再開の前に同じイメージへ入れ替える。

---

## 7 運用

- **ヘルスチェック**：apiは `/api/ping/`（認証なし）、frontendはトップページ、postgresは `pg_isready`。`depends_on` の `condition: service_healthy` で起動順を制御する。
- **ログ**：すべてのサービスはstdoutにJSONで出力する。Dockerのlogging driverでローテーションを設定する（`max-size`、`max-file`）。Sandboxの出力はRunの記録として保存し、ホストのログには流さない。
- **バックアップ**：`pg_dump` と、Local保存のobjectsのディレクトリを同じ時点で取得する（design.md §10）。復元手順を定期的に試す。
- **掃除**：使わなくなったSandboxイメージは、RunやCheckpointが参照していないことを確認してから削除する（design.md §10.1）。`docker image prune` を無条件に実行しない。
- **Sandboxのコンテナ**：LauncherはAttemptを終えるときにSandboxを `docker rm -f` で消す。Workerが落ちて残ったものは、次にSandboxを起動するときに、終わったAttemptのもの（ラベル `org.yoriki.attempt`）を探して消す。
- **ファイルの保存先**：添付・成果物・Workspaceのsnapshot（tar）は、`YORIKI_STORAGE_BACKEND` で選んだ保存先（Localなら `/var/lib/yoriki/objects`、S3ならbucket）に置く（§7.2）。保持期限での削除はまだない（docs/status.md §3）。

### 7.1 Sandboxを動かすホスト

Sandboxから脱出されたときの影響を小さくするための手順（design.md §7.2の対策の2・4・5）。qclusterを動かすサーバー（単一ホストならそのホスト、分けるならWorkerサーバー）で行う。

**作業領域**：qclusterのコンテナの利用者（uid 10001）が書けるディレクトリを、ホストとコンテナで同じパスに用意する（§3.1）。ジョブの出力と、Sandboxに見せる添付を置く。Sandboxには、Attemptごとの添付のディレクトリだけを `/workspace/input` に読取り専用でmountする（Sandboxの書ける場所はすべて大きさを決めたtmpfs）。Sandboxの利用者（userns-remapではホストの別のuid）が添付を読めるよう、添付のディレクトリとファイルは誰でも読める権限で作る。

```bash
sudo install -d -o 10001 -g 10001 -m 0700 /var/lib/yoriki/workspaces
```

**ホストの更新（対策の2）**：カーネル、Docker Engine、containerd、runcの脆弱性がそのままSandboxからの脱出につながるので、更新を止めない。

- Dockerは公式のaptリポジトリから入れ、`unattended-upgrades` の対象に `docker-ce`、`containerd.io` とカーネルを含める（Ubuntuの例：`/etc/apt/apt.conf.d/50unattended-upgrades` の `Origins-Pattern` に `origin=Docker` を足す）。
- カーネルの更新は再起動するまで効かない。`/var/run/reboot-required` を見て、月に1回など決まった時間に再起動する。再起動の前にWorkerをdrainする（§6の2）。
- Dockerを更新すると動いているコンテナが止まる（`live-restore` を使わない場合）。実行中のRunは中断され、Reconcilerが別のAttemptでやり直す。

**userns-remap（対策の4）**：コンテナの中のroot（uid 0）を、ホストの権限のないuidに対応させる。Sandboxの利用者（uid 10001）も、ホストでは別のuidになる。

```json
// /etc/docker/daemon.json
{ "userns-remap": "default" }
```

- 有効にした後は、Dockerの保存先が変わるので、イメージを取り直す（以前のイメージ・コンテナ・named volumeは見えなくなる）。
- qclusterは `userns_mode: host` で動かす（`compose.app.yaml` に指定済み）。Dockerソケットはホストのroot権限と同等なので、再マップしても守れるものがなく、作業領域のbind mountの所有者もそのままにできるため。LauncherはSandboxに `userns_mode: host` を付けさせない（起動後の確認で断る）。
- 単一ホストの構成では、ほかのサービス（postgres、nginx等）も再マップされ、bind mount（`/var/lib/yoriki/backups`、証明書等）の所有者を合わせ直す必要がある。Workerサーバーを分ける構成（§3.2）なら、そのサーバーのqclusterとSandboxだけが対象になる。

**gVisor（対策の5）**：Sandboxは既定でgVisor（runsc）で動かす（設定 `YORIKI_SANDBOX_RUNTIME`、既定 `runsc`）。Sandboxの中のコマンドのシステムコールをgVisorが受け、ホストのカーネルに直接届かない。gVisorの公式のaptリポジトリから入れ、Dockerに `runsc` の名前で登録する（aptなら `unattended-upgrades` で更新できる。対策の2）。

```bash
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
  | sudo tee /etc/apt/sources.list.d/gvisor.list
sudo apt-get update && sudo apt-get install -y runsc
sudo runsc install            # /etc/docker/daemon.json の runtimes に runsc を足す
sudo systemctl restart docker
docker run --rm --runtime=runsc <Sandboxのイメージ> dmesg | head -1   # "Starting gVisor..." と出る
```

- `unattended-upgrades` の `Origins-Pattern` に gVisor のリポジトリを足す（`origin=` の値は `apt-cache policy runsc` で確かめる）。runscの更新は、次に起動するSandboxから効く。
- userns-remapと併せて使える（gVisor自体も、再マップしたuidで動く）。rootless Dockerとの組み合わせは確かめていないので、gVisorを使うときはuserns-remapにする。
- Dockerに `runsc` が登録されていないWorkerでは、Launcherは既定のruncで動かさず、Sandboxを起動しない（Runは失敗し、ログに理由が出る）。gVisorを入れられない環境だけ `YORIKI_SANDBOX_RUNTIME=runc` にする（Launcherがログに警告を出す）。
- CI（`.github/actions/install-gvisor`）は、版を固定したリリースのtarを、SHA-512を確かめて入れる。
- runcとの違い（design.md §7.2）：Sandboxの中からcgroupのファイルは見えず、CPUの数とメモリの量が上限のとおりに見える。Runtime Profileの「プロセス数の上限」は、Launcherがホストの上限に換算して渡す（gVisorでは中のプロセスごとにホストのプロセスが増えるため）。メモリの上限を超えると、Sandboxごと止まることがある。

**rootless Docker**：Dockerのデーモン自体を権限のない利用者で動かす方法もある。dockerdが乗っ取られてもホストのrootにならない。qclusterにはその利用者のソケット（`$XDG_RUNTIME_DIR/docker.sock`）を `/var/run/docker.sock` としてmountする。cgroup v2とsystemdの委譲が要り、資源の上限（`--memory`・`--cpus`・`--pids-limit`）が効くことを確かめてから使う。

Launcherは、Dockerがuserns-remapでもrootlessでもなければ、qclusterのログに警告を出す（起動は止めない）。

---

### 7.2 ファイルの保存先

添付・成果物・Workspaceのsnapshotは、Storage Backend（design.md §7.8、`api/src/artifact/storage.py`）に置く。どれを使うかは `YORIKI_STORAGE_BACKEND`（`local`・`s3`）で決め、apiとqcluster（Workerサーバーを含む）で同じ設定にする。

- **Local**（単一ホスト）：`/var/lib/yoriki/objects` に置き、api・qclusterが読み書きし、nginxが読取り専用でmountする。ダウンロードは、apiが権限を確かめた後に `X-Accel-Redirect`（`YORIKI_STORAGE_ACCEL_REDIRECT=/_protected/`）でnginxに返させる。バックアップはDBと同じ時点で取る（§7）。

  ```bash
  sudo install -d -o 10001 -g 10001 -m 0755 /var/lib/yoriki/objects
  ```

- **S3**（Workerを別のサーバーに置くとき）：非公開のbucketと、Yorikiのprefixだけに `s3:PutObject`・`s3:GetObject`・`s3:DeleteObject`（と、存在の確認のための `s3:ListBucket`）を許したIAM roleを使う。資格情報はインスタンスプロファイル等で渡し、`yoriki.env` に書かない（どうしても書くときは `AWS_ACCESS_KEY_ID`・`AWS_SECRET_ACCESS_KEY`）。上書きしないことを条件付きの書込み（`If-None-Match: *`）で守るので、S3互換のストレージは、これに対応しているかを確かめてから使う。ダウンロードはapiが保存先から読んで返す。保存先のライフサイクルの設定で、Yorikiの保持期限より先に消さない（design.md §8）。
- 保存したファイルは、保存先のID（`YORIKI_STORAGE_ID`）とkeyで記録する。rootやbucketを変えるときは、同じIDのまま付け替えず、新しいIDにする（design.md §7.10）。既定の保存先を変えた後も以前の保存先のファイルを読めるよう、以前の保存先を `YORIKI_STORAGE_PREVIOUS`（JSON。読むだけ）に残す。既存のファイルを新しい保存先へ移すコマンドはまだない（docs/status.md §3）。

## 8 公開の方法

利用者のブラウザや外部サービスから、Yorikiにどう到達させるかを定める。どの方法でも、画面とAPIは同じドメインに置き、`/api/` をパスで振り分ける（§4.1）。アプリの設定（`/api/` の構成、セッションCookie、SSOのリダイレクト先）は変わらない。

| 方法 | 向いている場合 | TLSの終端 | 外から開けるポート |
|---|---|---|---|
| nginxで直接公開（§3.1の構成） | 社内ネットワークやVPNの中だけで使う、単一ホスト | nginx | 443（80はリダイレクト） |
| ALB等のロードバランサーの裏（§8.2） | AWS等で複数台に増やす | ALB | ALBの443だけ（ホストは非公開） |
| Cloudflare Tunnel（§8.3〜§8.5） | ホストのポートを一切開けずにインターネットへ公開する | Cloudflare | なし |
| 認証プロキシ（oauth2-proxy）をバリアとして置く（§8.6） | 既存の認証プロキシ（oauth2-proxy付きのnginx等）で複数のアプリを守っている | 前段（Cloudflare等） | Yorikiのホストは前段のnginxにだけ開く |

### 8.1 スケールしてもドメインは分けない

- 台数を増やすときも、画面とAPIのドメインは分けない。前段（ALBやnginx）がパスで振り分ければ、`/` と `/api/` を別々のプールにして増やせる。SSEの接続が増えたら、`/api/app/v1/.../stream/` だけを専用のプールに振り分ける。
- APIの状態はDBにあり、SSEも再接続時にDBから読み直すので、特定のサーバーにつなぎ続ける設定（sticky session）は不要。Djangoのセッションは、どのサーバーからも読めるDBに保存する（既定のまま）。
- ドメインを分けると、CookieつきのCORS、`CSRF_TRUSTED_ORIGINS`、Cookieのドメイン、SSOのリダイレクト先の設定が増え、ブラウザの事前確認のリクエストで遅くもなるので、スケールのためには分けない。
- 利用者のブラウザを通らない入口は、必要になったら別のホスト名にしてよい。Webhookの受信（`/api/hooks/`）は、WAFやrate limitを分けたくなったら `hooks.<ドメイン>` のように分ける（Cloudflareを使う場合は、証明書の制約により1段目のサブドメインにする。§8.5）。

### 8.2 ALB等のロードバランサーの裏に置く

- TLSはALBで終端し、各ホストのnginxは残す（`X-Accel-Redirect`、パスごとのリクエストサイズの上限、SSEの設定を§4と同じ設定で使うため）。
- ALBのアイドルタイムアウトは、既定の60秒から延ばす（数分〜1時間）。SSEは15秒ごとにkeep-aliveを送るので既定でも切れにくいが、余裕を持たせる。ALBは応答をバッファしないので、SSEはそのまま通る。
- デプロイでターゲットを外すと（deregistration delayの後）SSEは切れるが、ブラウザが `Last-Event-ID` を付けて再接続するので、イベントは欠けない。
- ターゲットグループのヘルスチェックは `/api/ping/` にする。
- 利用者のIPアドレスで判定する設定（rate limit、任意で追加するIPの制限）が効くように、nginxで `set_real_ip_from <VPCのCIDR>` と `real_ip_header X-Forwarded-For` を設定する。apiのポートが他のホストから届く構成では、Uvicornの `--forwarded-allow-ips` も、`"*"` ではなく各ホストのnginxのアドレスに絞る。
- Workerサーバーは外からの接続を受けないので、ALBには載せない。
- CloudFrontを前に置く場合は、オリジンの読み取りタイムアウトがSSEのkeep-aliveの間隔（15秒）より長いことを確認する。

### 8.3 Cloudflare Tunnel

サーバー上の `cloudflared` がCloudflareへ外向きに接続し、利用者のリクエストはその接続を通ってnginxに届く。ホストのポートは公開しない。

```yaml
# deploy/compose.cloudflare.yaml（compose.yaml に重ねて使う）
services:
  nginx:
    ports: !reset []          # ホストのポートを公開しない。nginx は内部の 80 番だけで待ち受ける
  cloudflared:
    image: cloudflare/cloudflared:<digestで固定>
    command: tunnel --no-autoupdate run
    env_file: [/etc/yoriki/cloudflared.env]   # TUNNEL_TOKEN
    networks: [edge]
    restart: unless-stopped
    deploy:
      replicas: 2             # 片方が止まっても公開を続けられるようにする
```

```bash
docker compose -f deploy/compose.yaml -f deploy/compose.cloudflare.yaml up -d
```

- Cloudflare側のトンネルの設定で、`<ドメイン>` を `http://nginx:80` へ転送する。`/` と `/api/` の振り分けはnginxが行う。TLSの証明書はホストに置かない。
- 利用者のIPアドレスは、nginx（`sites/http.conf`）で `set_real_ip_from <私設のアドレス>`、`real_ip_header X-Forwarded-For`、`real_ip_recursive on` を設定して取得する。Cloudflareは、利用者が送った `X-Forwarded-For` の後ろに実際の接続元のIPアドレスを付け足すので、右から読んで信頼しない最初のアドレス（実際の利用者）を使えば、先頭に書かれた偽の値は使われない。nginxはその値だけを `X-Forwarded-For` に入れてapiへ渡す（§4.1）。管理画面と `/api/schema/` はYorikiのログインと役割で守っているので、Cloudflare Accessは使わなくてよい（§8.5）。
- SSEは、Cloudflareが `text/event-stream` をバッファしないのでそのまま通る。無通信が100秒続くと切られるが、15秒ごとのkeep-aliveで防げる。
- Webhook（Zapier、Slack、Teams等）のPOSTが止められないよう、WAFのルールで `/api/hooks/` を除外する。Bot Fight Modeは無料プランではパスごとに除外できないので有効にしない（§8.5）。Cloudflare Accessを使う場合も、`/api/hooks/` は対象にしない。
- キャッシュは、フロントエンドの静的ファイル（`/_nuxt/*`）だけを対象にし、`/api/` はキャッシュしない。
- リクエストの本文の上限（無料プランは100MB。§8.5）を超える添付は受け付けられないので、アップロードの上限をそれ以下に設定する。
- Workerサーバーには公開のホスト名を割り当てない（外からの接続を受けない。§3.2）。
- cloudflaredは `X-Forwarded-Proto: https` を付けるので、Djangoの `SECURE_PROXY_SSL_HEADER` はそのまま効く。

### 8.4 Cloudflare Tunnelの裏に複数のサーバーを並べる

Cloudflare Tunnelで複数のアプリサーバーへ振り分ける方法は3つある。

| 方法 | 振り分け | 向いている場合 |
|---|---|---|
| A. 各サーバーで同じトンネルの `cloudflared` を動かす | Cloudflareが接続の近さで選ぶ。負荷やサーバーの状態を見た振り分けにはならない | 冗長化だけが目的のとき |
| B. サーバーごとにトンネルを作り、Cloudflare Load Balancingで振り分ける | Cloudflareがヘルスチェックしながら振り分ける | 使わない（有料の機能。§8.5） |
| C. トンネルの出口に自前のロードバランサー（nginx）を置き、そこから各サーバーへ振り分ける | 自分のnginxがヘルスチェックしながら振り分ける | **推奨**。パスごとのプール分け（§8.1）も自分で制御できる |

Cの構成：

```
利用者 ─▶ Cloudflare ─▶ トンネル ─▶ エッジのホスト×2（cloudflared＋nginx LB）
                                          │ パスで振り分け
                         ┌────────────────┼──────────────────┐
                         ▼                ▼                  ▼
                 アプリサーバー1     アプリサーバー2     …（nginx・frontend・api・qcluster）
                         └────────────────┼──────────────────┘
                                          ▼
                              PostgreSQL ／ S3（共有）
```

- **エッジのホスト**：`cloudflared` と、振り分け用のnginxを動かす。2台にして、同じトンネルの `cloudflared` を両方で動かせば、片方が止まっても公開を続けられる（Aの仕組みを冗長化に使う）。振り分け用のnginxは `upstream` に各アプリサーバーを並べ、`/api/app/v1/.../stream/` だけを別の `upstream` にすることもできる。Composeは `deploy/compose.edge.yaml` にする。
- **アプリサーバー**：§3.1の構成からpostgresを除いたもの（`deploy/compose.app.yaml`）。ポートは社内ネットワークにだけ開き、インターネットには公開しない。
- **ネットワーク**：エッジ、アプリ、Worker、DBのサーバーはすべて同じ私設ネットワークに置く。私設ネットワークの外のサーバーは想定しない。
- **共有するもの**：複数台にした時点で、次のものはサーバーの外に置く。
  - PostgreSQL：専用のサーバー、またはRDS等のマネージドサービス。
  - ファイルの保存先：Local Backendはサーバーをまたいで共有できないので、S3（または互換のストレージ）に切り替える（design.md §7.10の移行手順）。成果物のダウンロードは、`X-Accel-Redirect` ではなく、社員向けはapiがセッションで閲覧を確かめてapi経由で返す（署名付きURLへリダイレクトしない。取り消した権限で再利用させないため）。出口向けは、apiがtokenと出口の有効性・範囲を確かめた後に、ごく短い期限のS3の署名付きURLへリダイレクトしてよい（authorization.md §7.1）。
- **増やしてよいもの・1つだけにするもの**：
  - frontend、api：何台でも増やせる（状態を持たない。セッションはDB、SSEの取りこぼしはDBから読み直す）。
  - qcluster：何台でも増やせる（RunとDeliveryはDBのclaimで重複しない）。クラスタは1つで、どのqclusterもエージェントの実行と配信・Outbox・清掃を受け持つ（§3.2）。qclusterを動かすサーバーには、Dockerソケットとworkspacesの領域を用意する。
  - channel-listener：Slack Socket Mode等の接続ごとに、受信する担当を1つにする（design.md §10。複数台で動かす場合も、DBで担当を決めて重複して受信しない）。
  - migrate：更新のたびに1か所から1回だけ実行する（§6）。
- **Composeでの立て方**：どのサーバーもDocker Composeで立てる。サーバーの役割ごとにComposeファイルを分け、1台に複数の役割を重ねてよい。

| 役割 | Composeファイル | 台数 | 動かすサービス |
|---|---|---|---|
| アプリ | `compose.app.yaml` | 必要なだけ | nginx、frontend、api、qcluster（動かさなくてもよい。必要なら channel-listener） |
| Worker | `compose.qcluster.yaml` | 実行枠に合わせて | qclusterだけ（エージェントの実行、配信・Outbox・清掃）。§3.2 |
| エッジ | `compose.edge.yaml` | 2（冗長化） | cloudflared、振り分け用のnginx |
| DB | `compose.db.yaml` | 1 | postgres、バックアップ（マネージドのDBを使うなら不要） |

  専用のエッジのホストを用意せず、アプリサーバーのうち2台にエッジの役割を重ねてもよい。

```bash
# アプリ兼エッジのサーバー（1台目・2台目）
docker compose -f deploy/compose.app.yaml -f deploy/compose.edge.yaml up -d
# アプリだけのサーバー（3台目以降）
docker compose -f deploy/compose.app.yaml up -d
# Workerサーバー
docker compose -f deploy/compose.qcluster.yaml up -d
# DBのサーバー
docker compose -f deploy/compose.db.yaml up -d
```

  - 各サーバーの `/etc/yoriki/yoriki.env` は、DBの接続先・S3の設定・Secretを同じ内容にする。サーバーごとに違うのは、Worker IDと実行枠の数だけにする。
  - 振り分け用のnginxの `upstream` に並べるアプリサーバーの一覧は、`deploy/env` 等で管理し、台数を増減したらエッジの2台で再読込（`nginx -s reload`）する。
  - 更新は、エッジのnginxで1台ずつ振り分けから外し、§6の手順（drain → 入れ替え → 戻す）で順に行う。migrationは最初の1台でだけ実行する。
- **利用者のIPアドレス**：`CF-Connecting-IP` をエッジのnginxで受け取り、アプリサーバーへは `X-Forwarded-For` で渡す。アプリサーバーのnginxは、エッジのホストのIPだけを信頼する（`set_real_ip_from`）。

### 8.5 Cloudflareは無料プランを前提にする

Cloudflareは無料プランだけを使う。§8.3・§8.4は、次の制約の中で成り立つようにしている。

| 機能 | 無料プランでの扱い | Yorikiでの対応 |
|---|---|---|
| Tunnel | 使える | 公開に使う |
| Load Balancing | 使えない（有料） | 振り分けはエッジのnginxで行う（§8.4のC） |
| リクエストの本文の上限 | 100MB | 添付のアップロード上限をそれ以下にする |
| 応答を待つ時間 | 100秒（変更できない） | SSEは15秒ごとのkeep-aliveで切れない。100秒を超える処理はRunとして非同期にする（APIの応答では待たない） |
| Bot Fight Mode | WAFのルールで除外できない | 有効にしない。Webhookのような外部サービスからのPOSTを止めてしまうため |
| WAFのカスタムルール・rate limitのルール | 数に上限がある | 細かいrate limitはnginxで行い、Cloudflareのルールは最小限にする |
| Cloudflare Access（Zero Trust） | 50ユーザーまで | 使わなくてよい（認証はYorikiが行う。前段のoauth2-proxyはバリアとして置くだけ。§8.6） |
| 証明書（Universal SSL） | ルートのドメインと1段目のサブドメインだけ | 公開するホスト名は `yoriki.example.com` のような1段目にする（`hooks.yoriki.example.com` のような2段目は使えない）。Webhookの入口を分ける場合も `yoriki-hooks.example.com` のようにする |

### 8.6 認証プロキシ（oauth2-proxy）をバリアとして置く

既存の認証プロキシ（oauth2-proxy等、nginxの `auth_request` で使う）を前段に置き、複数のアプリを守っている構成では、Yorikiはその裏に1つのアプリとして置く。oauth2-proxyは社員以外を通さないためのバリアとして扱い、Yorikiのログインには使わない。

```
利用者 ─▶ Cloudflare等 ─▶ 前段のnginx（oauth2-proxyはauth_requestだけ）─▶ Yorikiのnginx ─┬─▶ frontend
                                                                                        └─▶ api（/api/）
```

- **ログインはYorikiでも行う**：oauth2-proxyの裏でも、Yorikiは自身のログイン（django-allauth）を行い、DjangoのセッションCookieで保つ（design.md §10.0、backend-conventions.md §6.2）。社員はoauth2-proxyとYorikiで2回ログインすることになるが、同じIdPを使っていれば2回目は通常、操作なしのSSOで済む。oauth2-proxyのAuthorizationヘッダーやIDトークンはYorikiへ転送しない（`--set-authorization-header` や `proxy_set_header Authorization` を設定しない）。
- **nginxは二段にする**：前段のnginxは、Yorikiのホスト名をYorikiのnginxへ転送するだけにする。SSEの設定、パスの振り分け、`X-Accel-Redirect` などYoriki固有の設定はYorikiのnginxに置き、前段のnginxの設定を増やさない。
- **SSOのリダイレクトとコールバック**：Yorikiのログイン開始と認証基盤からのコールバック（`/api/accounts/`）は、ほかの画面と同じくoauth2-proxyを通す（利用者はバリアで認証済みなので、そのまま通る）。
- **oauth2-proxyを通さないパス**：次のパスは、前段のnginxで `auth_request` を外す（またはoauth2-proxyの `--skip-auth-route` に入れる）。Yoriki側でそれぞれ専用の認証を行う。

| パス | 理由 |
|---|---|
| `/api/hooks/` | Zapier・Slack・Teams等からのWebhook。ブラウザのログインができない |
| `/api/files/<token>/` | Zapier等が期限付きのtokenで成果物を取得する |
| `/api/ping/` | ヘルスチェック |

- **SSE**：前段のnginxは `auth_request`（認証だけをoauth2-proxyに問い合わせる方式）で使っているので、応答はoauth2-proxyを通らず、SSEはそのまま通る。前段のnginxでもSSEのパスのバッファリングを切る（Yorikiは `X-Accel-Buffering: no` を返すので、設定がなくても効く）。
- **バリアのセッション切れ**：oauth2-proxyのセッションが切れると、APIの呼び出しはIdPへのリダイレクトになり、XHRではCORSのエラーかJSONでない応答になる。フロントエンドは、APIの呼び出しがネットワークエラーやJSONでない応答になったら画面ごと再読み込みし、oauth2-proxyに改めて認証させる（frontend-conventions.md §3.1）。
- **ログアウト**：消すのはYorikiのセッションだけにする。oauth2-proxyのセッションは前段の基盤のもので、ほかのアプリと共有しているので、そのままにする。
- **IPアドレスの制限は使わない**：前段の他のアプリと同じく、oauth2-proxyのバリアとYorikiのログイン・permissionで守る。
- **利用者のIPアドレスを前段のnginxで解決して渡す**：ログインの試行回数の制限は送信元のIPアドレスごとにも数える（backend-conventions.md §6.2）。前段のnginxは、Cloudflareから受けた `CF-Connecting-IP` で利用者のIPアドレスを解決し（`set_real_ip_from <CloudflareのIPアドレスの範囲、またはcloudflaredのネットワーク>`、`real_ip_header CF-Connecting-IP`）、`X-Forwarded-For` を `$remote_addr` で上書きしてYorikiのnginxへ渡す。解決しないまま自分から見た接続元（CloudflareのエッジのIPアドレス）を付け足すと、Yorikiには多くの社員が同じIPアドレスに見え、誰かの失敗が続くと関係のない人も一定時間ログインできなくなる（制限を逃れられるわけではない。アカウントごとの制限はそのまま効く）。Yorikiのnginxは `sites/http.conf`（私設のアドレスだけを信頼する）を使う。
