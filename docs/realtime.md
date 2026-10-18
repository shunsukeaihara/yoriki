# リアルタイム通信の方針（SSE / WebSocket）

チャット画面でのRunの進捗・Agentの応答の逐次表示、確認待ちや完了の通知を、ブラウザへリアルタイムに届ける方式を定める。

## 1 結論

- **サーバーからブラウザへの配信はSSE（Server-Sent Events）を使う。**
- **ブラウザからサーバーへの送信（メッセージ、回答、停止、追加指示）は、これまでどおりDRFのREST APIで行う。**
- DjangoはASGIアプリとして **Uvicorn** で起動する（gunicornの同期ワーカーは使わない）。
- 配信するイベントの型はDRFのSerializerで定義し、OpenAPIスキーマ経由でフロントエンドの型を生成する。
- WebSocketは使わない。双方向の高頻度な通信（共同編集、音声など）が必要になったら、改めて判断する。gRPCは使わない。

## 2 比較

| 観点 | SSE＋REST | WebSocket |
|---|---|---|
| 送信の検証・権限・冪等性 | 既存のViewSet・Serializer・permissionをそのまま使える（backend-conventions.md §0） | 同じ検証をWebSocketのメッセージ処理にも実装する必要がある。APIの入口が2つになる |
| 切断後の再開 | `id:` と `Last-Event-ID` が標準で使える。RunEventの `sequence`（design.md §5.1）をそのままIDにできる | 再開の手順を独自に設計する |
| 型定義 | イベントのSerializerをOpenAPIのcomponentとして出力し、openapi-typescriptで生成できる（RESTと同じ仕組み） | AsyncAPI等の別の定義と生成の仕組みが要る |
| サーバー構成 | Django（ASGI）＋PostgreSQLのLISTEN/NOTIFYで足りる | Django Channelsとchannel layer（実質Redis）が要る。design.mdの「Redisを初期必須にしない」と衝突する |
| プロキシ・ネットワーク | 通常のHTTP。HTTP/2なら1接続に多重化される | Upgradeが必要。社内プロキシで切られることがある |
| 制約 | テキストのみ。認証はセッションCookieで行うので、標準の `EventSource` をそのまま使える | 双方向・バイナリが扱える |
| LLMの逐次出力 | 各LLMのAPI自体がSSEで、そのまま中継しやすい | 中継できるが利点は少ない |

Yorikiの画面で双方向に必要なのは「利用者の送信」と「サーバーからの逐次配信」で、送信はRESTで検証・記録すべき操作である。リアルタイム性が必要なのはサーバーからの配信だけなので、SSEで足りる。

どちらを選んでも、接続を長時間保持するためにASGIサーバーが必要になる。gunicornの同期ワーカーではSSEの1接続がワーカーを1つ占有するため、Uvicornで起動する。

## 3 エンドポイント

| パス | 内容 | 再開 |
|---|---|---|
| `GET /api/app/v1/stream/` | ログインユーザー宛ての通知（確認待ち、Runの完了・失敗、配信失敗など） | 通知の `sequence` |
| `GET /api/app/v1/runs/{id}/stream/` | 1つのRunの詳細イベント（状態変更、Agentのメッセージ、Tool実行、LLM出力の差分） | RunEventの `sequence` |

- フロントエンドでは、他のAPIと同じくapiEndpoint（`/api/`）からの相対パス（`app/v1/stream/`、`app/v1/runs/{id}/stream/`）で指定する。

- 1つのタブで開くストリームは、上の2つまでにする（HTTP/1.1の同時接続数の制限を避ける）。
- LLM出力の差分（token単位の断片）は、保存しない一時イベントとして送る。`id:` を付けず、再接続しても再送しない。確定したメッセージはRunEventとして保存され、再接続時に送られる。

### イベントの種類

| `type` | 内容 |
|---|---|
| `run.status` | Runの状態の変更 |
| `run.message` | Agentの確定したメッセージ、Tool実行 |
| `run.delta` | LLM出力の差分（token単位の断片）。`id:` を付けない一時イベント |
| `clarification.requested` | Agentからの質問（回答待ち） |
| `notification` | ログインユーザー宛ての通知 |

### イベントの形式

```
id: 128
event: run.status
data: {"type":"run.status","sequence":128,"run":"8sJ2...","status":5,"status_display":"停止中"}

event: run.delta
data: {"type":"run.delta","run":"8sJ2...","message":"Qw9...","text":"了解しました。"}

: keep-alive
```

- `data` はSerializerの出力（snake_case）。フロントエンドで他のAPIと同じくcamelCaseに変換する。
- `event` と `data.type` は同じ値にする。
- 15秒ごとにコメント行（`: keep-alive`）を送り、プロキシに切断されないようにする。

## 4 バックエンドの実装

- ストリームのendpointは、Djangoの非同期viewで `StreamingHttpResponse`（async generator）を返す。DRFのViewSetにはしない。認証はセッション（`request.auser()`）で行い、権限の判定（対象Runの所有者か等）はViewSetと同じQuerySetの絞り込み関数を使う。
- 新しいイベントの通知には、PostgreSQLの `LISTEN/NOTIFY` を使う。RunEventや通知を保存するのと同じtransactionの中で `pg_notify`（payloadはIDだけ）を実行する。PostgreSQLはcommitした時点で通知を届け、rollbackすれば通知も出ない。ストリーム側は通知を受けたらDBから `sequence` より後のイベントを読む。
- `LISTEN` 用のDB接続はプロセスごとに1本だけ持ち、各ストリームへasyncioのキューで配る。ストリームごとにDB接続を持ち続けない。
- 接続時に `Last-Event-ID` があれば、それより後のイベントをDBから先に送ってから、新しい通知を待つ。
- LLM出力の差分は保存しないため、Runnerから `NOTIFY` のpayload（8000バイト未満）で直接流す。QClusterだけのWorkerサーバー（design.md §6.1）でも、同じDBへ `NOTIFY` を送るので同じ方法で流せる。
- イベントのSerializerは `stream/serializers.py` にまとめ、`PolymorphicProxySerializer`（`resource_type_field_name="type"`）で1つの型にする。ストリームのviewに `@extend_schema(responses={(200, "text/event-stream"): StreamEventSerializer})` を付け、スキーマにイベントの型を出力する。
- Nginxでは、ストリームのパス（`location ~ ^/api/app/v1/(runs/[^/]+/)?stream/$`）に `proxy_buffering off;`、`proxy_read_timeout 1h;`、`gzip off;` を設定する（deployment.md §4.2）。レスポンスにも `X-Accel-Buffering: no` を付ける。

```bash
# 起動（deploy/compose.yaml の api サービス。全体は deployment.md §3.1）
uvicorn app.asgi:application --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers --forwarded-allow-ips="*"
```

### 4.1 LISTEN/NOTIFYを選んだ理由と、Redisへの切り替え

通知の仕組みは「新しいイベントがあることを知らせる」だけで、イベントの正本はDBにある。そのため、届け損ねても、再接続時に `sequence` でDBから読み直せば欠けない。

| 観点 | PostgreSQL LISTEN/NOTIFY | Redis Pub/Sub |
|---|---|---|
| 追加の運用 | 不要（DBもQ2のbrokerもPostgreSQL） | Redisのサービス、監視、バックアップ、認証の設定が増える |
| DBの更新との整合 | 同じtransactionで送れる。commitされた変更の通知だけが届く | DBのcommit後に別途publishする必要があり、片方だけ成功する場合を考える必要がある |
| 処理量 | 社内利用の規模（同時接続が数百程度）なら十分 | 大量の同時接続・高頻度のイベントに強い |
| 制約 | payloadは8000バイト未満。LISTENする接続はPgBouncerのtransaction poolingを通せない。commitの多いDBで大量にNOTIFYすると、commitの処理が直列になりやすい | 届け損ねたメッセージは残らない（LISTEN/NOTIFYと同じ） |

初期はLISTEN/NOTIFYを使う。通知を送る処理と受け取る処理は1つのモジュール（`stream/notifier.py`）にまとめ、次のような状況になったら、APIやイベントの形を変えずにRedis Pub/Subへ差し替える。

- 同時接続が数千を超える、またはLLM出力の差分（`run.delta`）の頻度でDBのcommitが詰まる
- 接続数を抑えるためにPgBouncerのtransaction poolingを入れる

LLM出力の差分は頻度が高いので、Runnerで100〜200ミリ秒ごとにまとめてから送る。

テストも本番と同じPostgreSQLで実行し、NOTIFYを送ってからLISTENで受け取るまでを実際に確認する（backend-conventions.md §9）。

## 5 フロントエンドの実装

- `useEventStream(path, { onEvent })` を `composables/stream.ts` に用意する。標準の `EventSource` を使う（画面と同じオリジンなので、セッションCookieが自動で送られる）。切断時は `EventSource` が `Last-Event-ID` を付けて自動で再接続する。
- イベントの型は、OpenAPIから生成した `StreamEvent`（`type` で判別できるunion型）を使い、`switch (event.type)` で処理を分ける。
- サーバーは `retry:` で再接続までの間隔を指定する。
- `EventSource` からはHTTPのステータス（401等）が見えない。`onerror` で `readyState === EventSource.CLOSED`（ブラウザが再接続をやめた状態）になったら、REST APIの `app/v1/my/` を呼んでログイン状態を確認する。未ログインなら401が返り、APIクライアント（axios）がログイン画面へ移動させる。確認の呼び出しがネットワークエラーやJSONでない応答になった場合（前段の認証プロキシのセッション切れ等）は、APIクライアントが画面ごと再読み込みする（frontend-conventions.md §3.1）。
- タブが非表示の間は切断してもよい。再接続後、取りこぼしの可能性がある場合（Runが終了していた等）は、該当画面のデータをREST APIで取得し直す。
- ストリームが使えない環境でも画面が壊れないよう、Runの詳細などは表示時にRESTで最新の状態を取得し、ストリームは差分の反映に使う。
