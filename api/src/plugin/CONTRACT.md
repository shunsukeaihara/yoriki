# Pluginの契約

Yorikiのサーバーに配備する拡張（Plugin）の作り方と、Yorikiが守る約束をまとめる。設計は [design.md §3.11](../../../docs/design.md) を正とする。

## 1 Pluginとは

- Pluginは、サーバーに配備する拡張の配布・管理の単位。有効・停止はPluginの単位で、基盤管理者が管理画面（`/manage/plugins`）で切り替える。
- Adapterは、Yorikiが呼び出す実装（Pluginの中に1つ以上）。入口・出口・Connectorの接続は、Adapterの `extension_id` で指す。
- Pluginのコードはレビュー済みのpackageとして配備する。DBや外部の入力からコードを読み込むことはない。画面からの追加とマーケットプレイスは、まだない。

## 2 拡張種別

| 種別 | 基底クラス | Yorikiが呼ぶもの | 契約の例外 |
|---|---|---|---|
| `trigger`（入口） | `task.triggers.TriggerAdapter` | `parse(request)`・`dedup_key(...)`・`input_values(...)` | `task.webhook.PayloadError` |
| `notification`（出口） | `delivery.adapters.NotificationAdapter` | `deliver(request) -> Outcome`、URLを持つ種類は `validate_url(url)` | 結果は `Outcome` で返す。`PluginTemporaryError` は送り直し、`PluginConfigError`・`PluginPermanentError` は失敗にする |
| `storage`（保存先） | `artifact.storage.StorageBackend` | `put_immutable`・`open_read`・`stat`・`delete`・`list_keys` | `artifact.storage.StorageError` |
| `connector`（Connector） | `connector.adapters.base.DatasourceAdapter` | `connect`・`open_cursor`・`translate_error` を実装し、`check`・`list_tables`・`describe`・`query` は基底クラスが行う | `connector.errors.ConnectorError`（種類つき） |
| `channel`（対話の出口） | `delivery.channels.base.ChannelAdapter` | `check_connection`・`check_channel`・`post`・`update`・`notify_user`・`verify`・`parse`（任意で `user_name`・`thread_url`（Runの画面のスレッドへのリンク。スレッドを作ったときに1回呼ぶ。分からなければ空）。capability `listen` を宣言するものは `listen`・`fetch_replies`、`list_channels` を宣言するものは `list_channels`） | 送信の結果は出口と同じ `Outcome`（投稿したメッセージの参照は `message_ref`）。確かめは `PluginConfigError`、読めない要求は `PluginPermanentError` |

対話の出口のAdapterは、チャットツールのAPIとの通信とメッセージの見せ方（ボタン等）だけを持つ。スレッドとRunの対応、回答の確定、配信の再試行・記録はYorikiが持つ（design.md §4.7）。資格情報は連携の接続（ChannelConnection）が暗号化して持ち、呼ぶたびに `secrets` で渡す。投稿の結果が分からないとき（送った後に応答が失われた等）は「届いたか分からない」を返す（Yorikiは自動では送り直さない）。受け口は `/api/hooks/channels/<extension_idの最後の部分>/<接続のID>/` で、Yorikiが接続を引いて `verify`・`parse` を呼ぶ。`parse` が `response` を返すと、Yorikiは出来事（`events`）を記録してから、その本文を応答にする（DiscordのPINGへの応答、ボタンへの「受付」）。受け付けなかったボタンへの知らせには、受け口で読んだ `reply_token`（Discordのinteractionのtoken等）を `notify_user` へ渡す（使えなければスレッドへの投稿でよい）。

HTTPでは受けられない出来事（DiscordのGatewayの返信）は、capability `listen` を宣言して常時接続で受ける。channel-listener（`run_channel_listener`）が接続ごとに担当を決め、`async listen(secrets, sink)` を呼ぶ（取り消されるまで返さない）。Adapterは受けた出来事を `await sink.events([...])` で渡し、つないだら `await sink.connected(resumed)` を呼ぶ（新しいセッションなら `resumed=False`。Yorikiは確認待ちのスレッドを `fetch_replies(secrets, channel_id, thread_ref, after_ref)` で読み直す）。資格情報・設定の誤りは `PluginConfigError`（つなぎ直しの間隔を空け、接続の受信の状態に出す）、それ以外の例外は少し待ってつなぎ直す。

保存先は環境変数で選ぶので（deployment.md §7.2）、Registryには版と状態を見せるために載せるだけで、画面からは止めない。作業ファイルの退避（file_snapshot）は、まだAdapterに分けていない。

### 2.1 対応能力（capabilities）

| 名前 | 種別 | 意味 |
|---|---|---|
| `start` | trigger | 新しいRunを起動する |
| `url` | notification | 外へ送る（宛先のURLを持つ）。登録時に `validate_url` で確かめ、送るたびに成果物のURLを発行する。機密の結果は、出口の版で許したときだけ送る |
| `personal` | notification | 個人のタスクの出口に使える（Yorikiが作る共通の画面の通知） |
| `file_fetch_auth` | notification | 渡した成果物のURLの取得に、出口の署名の鍵（`Authorization: Bearer`）を要る |
| `schemas` | connector | 表の名前にスキーマがある（MySQLはDB名がスキーマなので持たない） |
| `listen` | channel | 常時接続で受ける（`listen`・`fetch_replies` を持つ）。channel-listenerが接続ごとに呼ぶ |
| `list_channels` | channel | 出口の登録の画面で選ぶチャネルの一覧を取れる（`list_channels(secrets) -> ChannelList`。名前の順、選べないものは `unavailable` に理由）。宣言しないものは、画面でIDを入力する |

### 2.2 設定

- Adapterの設定は、`Extension.config_schema`（JSON Schema）で宣言する。画面は `default` を既定値に使う（例：接続先のポート）。
- コンストラクタの引数は、基底クラスの形で呼べること。入口・出口は引数なし、Connectorは接続と版で作る。保存先だけは、基底クラスにないキーワード専用の引数を設定とみなし、settingsの値を渡す（例：`root`・`bucket`）。
- Secretは値を宣言に書かない。必要な種別だけを `Extension.secrets` に書く。

## 3 宣言のしかた

自社のPluginは、別のpackageとして作り、entry points（グループ `yoriki.plugins`）で `PluginSpec` を指す。entry pointの名前は `plugin_id` と同じにする。

```toml
# 自社のPluginの pyproject.toml
[project.entry-points."yoriki.plugins"]
"acme.chat" = "acme_yoriki.plugin:PLUGIN"
```

```python
# acme_yoriki/plugin.py
from plugin.spec import Extension, ExtensionType, PluginSpec

PLUGIN = PluginSpec(
    id="acme.chat",
    name="ACME Chat",
    version="0.3.0",
    extensions=(
        Extension(
            id="acme.chat.notification",
            type=ExtensionType.NOTIFICATION,
            name="ACME Chat",
            adapter=AcmeChatNotification,
            capabilities=frozenset({"url"}),
        ),
    ),
    self_check=check_settings,  # 任意。外へ出ず、副作用のない確かめだけ
)
```

- `plugin_id` は自社の名前から始める（`yoriki.` は本体だけ）。`extension_id` は `plugin_id` から始める。
- Yorikiのimageから派生させたimageに入れる。Yoriki本体のsettingsやコードは変えない。

  ```dockerfile
  FROM <yoriki-api のimage>
  RUN uv pip install acme-yoriki==0.3.0
  ```

- 本体（`api/`）はpackageとして入れないので、本体のPluginはentry pointsではなく `plugin/builtin_plugins.py` で同じ型を作る。

## 4 登録と有効化

- 各プロセス（api・qcluster）は、本体のPluginとentry pointsを読み込み、形と宣言を確かめる（`plugin.registry.loaded()`）。読み込めない・確かめに通らないPluginは使わず、理由を記録する（本体は止めない）。
- DBへの登録は、`migrate` の後（配備のたびに流れる）と、管理コマンド `sync_plugins` で行う。初めて見つかったPluginは、本体のものは有効、それ以外は停止の状態で登録する。配備から消えたPluginは、行を残して「配備されていない」にする。
- 有効にする時は、形と宣言、廃止の予告がないこと、自己診断（`self_check`）に通ることを確かめる。動くかどうかは、Connector・出口を作るときの接続確認で確かめる。
- 停止したPluginのAdapterを使う入口・出口・Connectorの接続は、それぞれを停止したのと同じに扱う（新しく作れない、Webhookを受け付けない、配信の直前に確かめて送らない、モデルへ渡す一覧に出さない）。設定は残る。

## 5 インターフェースの確かめ方と変え方

- 登録の時に確かめるのは形だけ：基底クラスを継承しているか、抽象メソッドが残っていないか、Yorikiが呼ぶ形の引数を受け取れるか（`inspect.signature`）。実行時に引数・戻り値の型は確かめない。
- 契約の版の番号は持たない。
- 広げるときは、今のPluginを壊さない形にする：既定の実装のある任意のメソッドを足す、引数をまとめたデータの型（`DeliveryRequest`・`TriggerRequest`）に既定値のある項目を足す。
- 破壊的な変更は、型に表れる形（新しいメソッドの名前、必須の引数）で行い、今のPluginが登録の時の確かめで落ちるようにする。移行の期間を置くときは、古い形を新しい形へつなぐ変換を本体に置き、それを使うPluginをdeprecatedとする。
- Plugin自身が廃止を予告するときは、`PluginSpec.deprecated`（理由）と `replaced_by`（代わりのplugin_id）を書く。停止中なら有効にできず、新しく作るものの選択肢に出ない。有効なものは動き続ける。

## 6 例外と失敗

- Adapterが投げてよいのは、契約の例外（上の表）と、`plugin.errors` の `PluginConfigError`（設定の誤り）・`PluginTemporaryError`（一時的な障害。時間切れは `PluginTimeoutError`）・`PluginPermanentError`（恒久的な失敗）だけ。メッセージは利用者に見せてよい内容にする（接続文字列やパスワードを入れない）。
- それ以外の例外は、Yorikiが呼ぶ境界で「予期しない失敗」として数え、失敗にする（再試行しない）。本文にSecretが入りうるので、記録と画面には例外の型と起きた場所だけを残す。
- 冪等性（同じ `event_id` の再送）、読取りだけ、timeout等の意味は、コードでは強制しない。timeoutのように外から守れるものはYorikiが守る。

## 7 合致度の追い方

- **適合テストの道具**（`plugin.testing`）：拡張種別ごとのpytestのクラス（`TriggerContract`・`NotificationContract`・`StorageContract`・`ConnectorContract`・`ChannelContract`）。継承して `spec`・`extension_id` と、相手の要るもの（`contract_*` のfixture）を用意して流す。テストはcapabilityごとに分け、宣言していないcapabilityのテストは飛ばす。Adapterの呼出しは、基底クラスの型ヒントで引数と戻り値を確かめる（beartypeを入れる）。本体のPluginも同じ道具で確かめる（`plugin/tests/test_contracts.py`、`connector/tests/test_adapters.py`）。mypy等の静的な検査も勧める。

  ```python
  from plugin.testing import NotificationContract


  class TestAcmeChat(NotificationContract):
      spec = PLUGIN
      extension_id = "acme.chat.notification"

      @pytest.fixture
      def contract_request(self):
          return DeliveryRequest(delivery=..., attempt=1, payload={...}, url="https://chat.example.com/hook")
  ```

- **実行時の観察**：境界で捕まえた予期しない失敗と時間切れを、AdapterとPluginの版・日ごとに数え（`PluginFailure`）、管理画面に直近7日の件数と最後のエラーを出す。
- Runは受付時に、使いうるAdapterとPluginの版をManifestの `plugins` に固定する。
