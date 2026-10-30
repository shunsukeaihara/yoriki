# バックエンド実装規約（Django / DRF）

Yorikiのバックエンド（Django、DRF、Django Q2）を実装するエージェント・開発者向けの規約。
アプリ構成、model、serializer、view、URL、管理コマンドの書き方を定める。§11「避ける書き方」に挙げた点は使わない。

製品としての要件・データモデル・状態遷移は [design.md](design.md) を正とする。本書は「どう書くか」を定め、「何を作るか」は定めない。

典型的なModel / Serializer / ViewSetの実装例は [backend-examples.md](backend-examples.md) にまとめている。

---

## 0 基本原則

本書で最も重要な章。以降の章と食い違う場合は、この章を優先する。

### 0.1 可能な限りViewSetで書き、DRFの標準に合わせる

- endpointは原則として `ViewSet`（`ModelViewSet`、`ReadOnlyModelViewSet`、`GenericViewSet` とmixinの組合せ）で書き、`DefaultRouter` に登録する。
- CRUD以外の操作も、独自のViewやURLを作らずに、ViewSetの `@action` として追加する。actionの入出力にはSerializerを付ける（§3.4）。
- 認証、権限、絞り込み、並べ替え、ページング、例外応答は、DRFの仕組み（`authentication_classes`、`permission_classes`、`get_queryset`、`filterset_class`、`ordering`、pagination class、exception handler）を使う。同じことをViewのコード内で独自に実装しない。
- DRFの標準の流れ（`get_serializer` → `is_valid(raise_exception=True)` → `save()` → `perform_create` / `perform_update`）から外れる書き方は、標準の方法で実現できない理由がある場合だけにする。
- `APIView` や関数ベースのViewは、Modelにも一覧にも対応しない単発のendpoint（ヘルスチェック等）だけに限る。その場合も `GenericViewSet` に `@action` を置いて済むなら、ViewSetを選ぶ。
- 例外として、SSEのストリーム（`stream` アプリ）はDRFのViewSetにせず、Djangoの非同期viewで書く（§4.5、[realtime.md](realtime.md) §4）。

### 0.2 入出力の型をSwagger（OpenAPI）で明確にする

最も大事なのは、Swaggerを見ればすべてのendpointのリクエストとレスポンスの型が分かることである。スキーマはフロントエンド（Nuxt）の型生成の元にもなる。

- リクエストとレスポンスに本文がある場合は、Serializerクラスで定義する。`request.data` を直接読んだり、dictを組み立ててそのまま `Response` に渡したりしない。
- 本文がないリクエスト・レスポンスは、Serializerを作らずに `None` とする（`@extend_schema(request=None, responses={204: None})`）。省略せずに明示する。
- 形が合う汎用のSerializer（`utils.serializers.CountSerializer` 等）や、そのリソースの既存の出力用Serializerがあれば、それを使う。同じ形のSerializerを操作ごとに作り直さない。
- 単発の入出力でも、`inline_serializer` ではなく名前付きのSerializerクラスを作る。名前のない型はフロントエンドで扱いにくい。
- `SerializerMethodField` には戻り値の型ヒントを付ける。dictやlistを返す場合は、`@extend_schema_field(XxxSerializer)` で形を指定する。
- `drf-spectacular` が警告なしでスキーマを生成できる状態を保つ（`python manage.py spectacular --validate --fail-on-warn`）。
- `SPECTACULAR_SETTINGS` に `COMPONENT_SPLIT_REQUEST: True` を設定し、入力用の型（`<名前>Request`）を出力用の型と分けて生成する。フロントエンドはこのスキーマから型を生成する（frontend-conventions.md §3.3）。

### 0.3 検証・整形・モデル化はSerializerの責務

- 入力値の検証（項目単体と項目間の両方）、正規化（全角半角、改行、空白、表記ゆれの統一）、出力の整形は、すべてSerializerに書く。
- 入力からModelのインスタンスを作る処理と、子オブジェクトをまとめて保存する処理は、Serializerの `create()` / `update()` に書く。
- Viewの責務は、対象の取得（`get_queryset` / `get_object`）、権限の強制、URLやログインユーザーから決まる値の受け渡し（`serializer.save(owner=request.user)`、`serializer.save(actor=request.user)`）、transactionの範囲指定に限る。
- 本文のないactionでは、Viewからドメインメソッドを直接呼んでよい（§4.3）。
- Modelの責務は、データ構造と、複数の入口から呼ばれるドメインの操作（状態遷移、Event記録等）とする。SerializerがこれらのModelのメソッドを呼ぶ。
- 同じ検証を複数のSerializerで使う場合は、Mixinかカスタムフィールド（`utils/fields.py`）にまとめる。管理コマンドやQ2 taskから同じデータを作る場合も、同じSerializerを通す。

### 0.4 必須の依存

- `django-extensions`：`runscript`、`shell_plus`、`show_urls` 等を使う。
- `drf-spectacular`：OpenAPIスキーマの生成。全ViewSetとactionに `@extend_schema` を付ける。
- `django-allauth`：社内SSOのログイン（§6.2）。
- `django-axes`：パスワードでのログインの試行回数の制限（§6.2）。
- `uvicorn`：ASGIサーバー。DjangoはASGIアプリとしてUvicornで起動する（`uvicorn app.asgi:application`）。SSEのストリームは接続を長く保つため、gunicornの同期ワーカーは使わない（[realtime.md](realtime.md)）。
- このほか、`djangorestframework`、`django-filter`、`django-q2`、`nanoid`、`django-auditlog` を使う。
- Python、uvのバージョンはリポジトリルートの `mise.toml` で固定する。

---

## 1 ディレクトリとアプリ構成

リポジトリは本体・フロントエンド・Sandboxイメージ・配備設定をまとめたモノレポとする（deployment.md §1）。

```
yoriki/
├── api/                      # Django（api / qcluster / channel-listener / worker / migrate で共用するimage）
│   ├── pyproject.toml        # uvで管理。mypy・pytestの設定もここに書く（ruffの設定はルートの ruff.toml）
│   ├── Dockerfile
│   └── src/
│       ├── manage.py
│       ├── conftest.py       # 全アプリ共通のpytest fixture
│       ├── app/              # プロジェクト設定（settings*.py, urls.py, asgi.py, wsgi.py）
│       ├── common/           # 複数アプリで使うChoices等（choices.py）
│       ├── utils/            # 横断的な部品（§6.3）
│       ├── scripts/          # runscriptで動かす単発・調査用スクリプト
│       └── <app>/            # 業務ドメインごとのDjangoアプリ
├── frontend/                 # Nuxt
├── sandbox/                  # Sandboxイメージ
├── deploy/                   # compose*.yaml（一覧は deployment.md §1）、nginx/ 等
├── docs/
├── mise.toml
├── ruff.toml                 # Pythonのlintと整形の設定（§10）
└── .envrc.example
```

- `api/` 以下は `src/` 構成にする。`manage.py` と各アプリは `src/` 直下に置き、`src/` をimportのルートにする（`from run.models import Run`）。
- `api/` のimageは1つで、起動コマンドを変えて api、qcluster、channel-listener、worker、migrate の各サービスで使う。
- プロジェクト設定パッケージ名は `app` とする。
- アプリ名は小文字の英単語1語（必要なら `_` で2語）。ドメインの名詞で切る。

### 1.1 アプリ内のファイル

各アプリは次のファイルを持つ。不要なものは作らなくてよいが、名前と役割は変えない。

| ファイル | 役割 |
|---|---|
| `models.py` | Model、QuerySet、アプリ固有のChoices |
| `serializers.py` | 入出力・検証・状態遷移のSerializer |
| `views.py` | ViewSet、FilterSet、アプリ内のViewSet用Mixin |
| `urls.py` | `DefaultRouter` への登録 |
| `admin.py` | Django管理画面 |
| `apps.py` | `AppConfig`（`default_auto_field = "django.db.models.BigAutoField"`） |
| `factories.py` | factory_boyのFactory |
| `tests.py` または `tests/` | テスト。肥大化したら `tests/test_*.py` に分割し、`tests/conftest.py` を置いてよい |
| `tasks.py` | Django Q2から呼ぶ関数（Yoriki追加） |
| `management/commands/*.py` | 運用で繰り返し使う管理コマンド |

それ以外の処理は、アプリ直下に役割名のモジュールを追加する（例：`report/pdf.py`、`report/csv_export.py`、`run/errors.py`）。`services/` のような汎用名のサブパッケージは作らない。

### 1.2 Yorikiのアプリ分割（初期案）

design.md §5.1の永続オブジェクトを、次の単位で分ける。実装を始めるときに見直してよい。

| アプリ | 主なModel |
|---|---|
| `user` | User、Groupとpermission、SSOのログイン（django-allauth）、Department・DepartmentMembership（部門と所属。使える部門・管理できる部門の判定。authorization.md §4） |
| `plugin` | PluginExtension（サーバーに配備する拡張のRegistry。design.md §3.11） |
| `skill` | Skill、SkillVersion、SkillDraft、SkillChange、SkillEvaluation、SkillAssetBinding（Skillが使うTool・資料・Connector・必要な能力も版で持つ。実行環境はRunの単位で決める。design.md §3.2、§7.3） |
| `resources` | Resource、ResourceVersion（Pythonの標準ライブラリの `resource` と名前が重なるので、複数形にする） |
| `conversation` | Conversation、ConversationMessage、UserInstruction |
| `run` | Run、ExecutionAttempt、RunEvent、ClarificationRequest、Checkpoint、ToolInvocation、ResultEnvelope、Outbox（`dispatch.py`） |
| `artifact` | Artifact、BlobObject、BlobLocation、WorkspaceSnapshot |
| `runtime` | RuntimeProfile、RuntimeProfileVersion、EnvironmentRevision、PackageBundle、Launcher |
| `task` | TaskDefinition、TaskDefinitionRevision（初期は結果の通知の設定（通知の書き方を含む）もここに持つ）、TriggerBinding、TriggerBindingRevision、TriggerReceipt、InteractionBinding、InteractionBindingRevision、NotificationBinding、NotificationBindingRevision（NotificationBindingは1つのタスクに複数の通知を持たせるときに作る。それまでは通知の設定をTaskDefinitionRevisionに持つ。design.md §4.3） |
| `delivery` | Destination、DestinationRevision、Delivery、DeliveryAttempt、FileLink・FileFetch（出口へ渡す成果物の取得）、Notification（画面の通知）、ExternalThreadBinding、ChannelMessageReceipt |
| `worker` | Workerの登録情報 |
| `usage` | UsageLedger、クオータ |
| `llm` | LLMConnection、ModelProfile、Provider Adapter（design.md §4.8） |
| `connector` | DatabaseConnection（社内DBの接続先と資格情報）、Connector（1つの接続の上の範囲。範囲（個人・部門・全社・システム）と部門を持つ）、ConnectorVersion（境界の版）、操作のAdapter（社内DB、SharePoint、Google Drive、MCP。design.md §4.9） |
| `stream` | SSEのストリームview、イベントのSerializer（realtime.md） |
| `harness` | Modelなし。Runner（実行のループ）、Tool、RunEventからの文脈の組立、圧縮、Sandboxの操作（harness.md §3） |

---

## 2 Model

```python
from django.db import models
from django.utils.translation import gettext_lazy as _

from utils.fields import NanoidField


class RunStatus(models.IntegerChoices):
    QUEUED = 1, _("待機中")
    RUNNING = 2, _("実行中")
    WAITING_USER = 3, _("回答待ち")
    RETRY_WAIT = 4, _("再試行待ち")
    CANCELLING = 5, _("停止中")
    COMPLETED = 10, _("完了")
    FAILED = 20, _("失敗")
    CANCELLED = 30, _("取消")


class RunQuerySet(models.QuerySet):
    def prefetch_events(self) -> "RunQuerySet":
        return self.prefetch_related("events")


class Run(models.Model):
    objects = RunQuerySet.as_manager()

    uid = NanoidField(unique=True)
    conversation = models.ForeignKey("conversation.Conversation", on_delete=models.PROTECT,
                                     related_name="runs", verbose_name=_("会話"))
    status = models.PositiveSmallIntegerField(_("状態"), choices=RunStatus.choices, default=RunStatus.QUEUED)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("実行")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["status", "-created"]),
        ]

    def __str__(self):
        return self.uid
```

### 2.1 ID

- 内部の主キーは `BigAutoField`（`AppConfig.default_auto_field`）を使う。
- APIやURLに出すIDは `uid = NanoidField(unique=True)` とする。`utils/fields.py` の `NanoidField` は、22文字の英数字のIDを生成する。
- APIでは `uid` を `id` という名前で返す（§3.1）。内部の整数PKはAPIに出さない。

### 2.2 共通フィールド

- 作成日時と更新日時は `created = models.DateTimeField(auto_now_add=True)`、`updated = models.DateTimeField(auto_now=True)` とする。名前に `_at` を付けない。一覧で並べ替えるものは `created` に `db_index=True` を付ける。
- 論理削除する可変のマスター（Skill、Destination等）は `deleted = models.BooleanField(default=False)` を持つ。QuerySetでは `filter(deleted=False)` を明示して書く。既定のManagerで自動的に除外する仕組みは作らない。
- 不変オブジェクト（`*Version`、`*Revision`、`RunEvent`、`UsageLedger`、`ChannelMessageReceipt` 等の履歴・監査記録）は `deleted` を持たせず、作成後に更新・削除しない。切り戻しも新しい版を作って行う（design.md §3.2、§3.10）。

### 2.3 Choicesと状態

- 状態や種別は `models.IntegerChoices` で定義し、`PositiveSmallIntegerField(choices=...)` に保存する。ラベルは必ず `gettext_lazy` で包む。`_("Webhook")` のような固有名詞も包む。
- 値は後から間に挟めるよう飛び番にしてよい。「不明・その他」は `0` にする。
- 外部仕様の文字列をそのまま保存する場合だけ `TextChoices` を使う。
- 複数アプリで使うChoicesは `common/choices.py`、1アプリで閉じるChoicesはそのアプリの `models.py` に置く。
- 状態遷移の履歴はEventモデルに記録する。状態を変える処理はModelのメソッド（例：`set_status`）にまとめ、Event記録と同じtransactionで行う。Yorikiでは `RunEvent` がこれに当たる。
- 許可する遷移はModelに遷移表として持ち、行ロック（`select_for_update`）の中で判定する。
- 状態を誰が進めるかを区別する。Run・Deliveryのようにシステムが進める状態は、Worker・Runner・受付処理の中だけで変更し、APIから遷移先を指定させない（§3.5、§4.3）。

### 2.4 リレーション

- 参照するだけのマスターへのFKは `on_delete=models.PROTECT` にする。親が所有する子（明細、メッセージ、添付）は `CASCADE` にし、`related_name` を付ける。
- 別アプリのModelを参照すると循環importになる場合は、`"app_label.Model"` の文字列参照にする。
- 種類の異なる参照元を1つのFKで表す必要がある場合は、`GenericForeignKey` と専用QuerySetの `GenericPrefetch` を使う。

### 2.5 QuerySetとN+1対策

- 関連取得は `XxxQuerySet(models.QuerySet)` に `prefetch_xxx()` メソッドとして定義し、`objects = XxxQuerySet.as_manager()` で使う。
- `to_attr` で別名を付けて取得した結果は、Serializer側で `getattr(obj, "latest_xxx", [])` で読む。
- `select_related` は、ViewSetの `queryset` クラス属性に必要なものを列挙する。

### 2.6 振る舞い

- ドメインの操作（承認、状態変更、作成時の付随処理）はModelのメソッドかclassmethodに書く（例：`create_xxx()`、`approve()`）。複数行を書き換える場合は `@transaction.atomic` を付ける。
- 操作者が必要なドメインメソッドは、`actor` 引数で受け取る。`request` には依存させない（Worker・管理コマンドからも呼ぶため）。
- `Meta` には `verbose_name = _("日本語名")` と `verbose_name_plural = verbose_name` を必ず書く。
- `__str__` を定義する。
- 監査対象のModelは、ファイル末尾で `auditlog.register(Model)` する。権限・公開・設定変更に関わるModelはすべて対象にする。

### 2.7 マイグレーション

- `makemigrations` で生成したファイルは、適用前に必ず内容を読んで確認する。
- 既存データを消す操作（既存テーブルの作り直し、データ移行なしの列削除）は、ユーザーの了承なしに行わない。

---

## 3 Serializer

### 3.1 IDと関連

```python
class MessageSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="uid", read_only=True)
    conversation = serializers.SlugRelatedField(slug_field="uid", read_only=True)
    attachments = serializers.SlugRelatedField(slug_field="uid", many=True, required=False,
                                               queryset=Artifact.objects.none())
    attachments_info = ArtifactSerializer(source="attachments", many=True, read_only=True)

    class Meta:
        model = ConversationMessage
        fields = ["id", "conversation", "body", "attachments", "attachments_info", "created"]
        read_only_fields = ["created"]

    def get_fields(self):
        fields = super().get_fields()
        # 添付に指定できるのは、ログインユーザーが所有するArtifactだけ
        if request := self.context.get("request"):
            fields["attachments"].child_relation.queryset = Artifact.objects.filter(
                owner=request.user, deleted=False)
        return fields
```

- `id = serializers.CharField(source="uid", read_only=True)` とし、`uid` そのものは出さない。
- `ModelSerializer` の `Meta` には、`fields` で出す項目を明示して列挙する（推奨）。`fields` に `uid` は入れない。`exclude` を使うのは、`uid` を含めて除外する場合だけにする。
- 関連の入力は `SlugRelatedField(slug_field="uid", queryset=...)` で受ける。表示用の展開は、別フィールド `<name>_info` に読み取り専用のネストSerializerで出す。
- 関連の選択肢（`queryset`）は利用者が扱える範囲に絞る。`Xxx.objects.all()` のままにすると、他人のオブジェクトのuidを指定できてしまう。利用者によって範囲が変わる場合は、`get_fields()` で `context["request"]` を使って絞る。
- 所有者やURLで決まる親は入力させない。`read_only` にして、Viewの `perform_create` で `serializer.save(owner=...)` として渡す。

### 3.2 用途別の命名

1つのModelに対して、用途ごとにSerializerを分ける。

| 接頭辞・接尾辞 | 用途 |
|---|---|
| `Minimum*Serializer` / `Simple*Serializer` | 他のSerializerに埋め込む最小限の表示 |
| `*Serializer` | 標準の入出力 |
| `*WithDetailSerializer` | 詳細画面用の拡張表示 |
| `App*Serializer` | 通常社員向け（管理者向けより項目を絞る） |
| `ReadOnly*Serializer` | すべて読み取り専用（`utils.serializers.ReadOnlyModelSerializer` を継承） |
| `<対象><操作>Serializer` | `@action` の入力（例：`RunCancelSerializer`、`ClarificationAnswerSerializer`、`SkillPublishSerializer`）（§3.4） |
| `*UpdateSerializer` / `*PatchSerializer` | 一部項目だけの更新 |
| `To<状態>Serializer` | 人が業務状態を一段ずつ進めるワークフローに限る（§3.5） |

### 3.3 検証

- 項目をまたぐ検証は `validate()` で行う。エラーはフィールド名ごとに `defaultdict(list)` に集め、最後にまとめて `serializers.ValidationError(errors)` で返す。
- 特定の項目に属さないエラーは `{"non_field_errors": _("...")}` で返す。
- エラーメッセージは `gettext_lazy` で包んだ日本語にする。
- ネストした子を作る場合は、`create()` で子のデータを `pop` し、`transaction.atomic()` の中で親と子を保存する。

### 3.4 action用Serializer

CRUD以外の操作（`@action`）の入出力は、次の順に決める。

| 入力 | 使うもの |
|---|---|
| 本文なし | `request=None`。Serializerは作らない |
| 入力の形が既存のSerializerと同じ | そのSerializer（汎用のものを含む） |
| 操作に固有の入力・検証がある | 操作用のSerializerを作る。名前は `<対象><操作>Serializer` |

| 出力 | 使うもの |
|---|---|
| 本文なし | `responses={204: None}` で、`Response(status=204)` を返す |
| 操作した対象を返す | そのリソースの出力用Serializer（例：`RunSerializer`） |
| 件数など | 汎用のSerializer（`CountSerializer` 等） |

操作用のSerializerを作る場合は、次のようにする。

- そのSerializerが、操作の入力の定義、検証、実行（`save()`）を担う。
- 既存のインスタンスに対する操作（停止、回答、承認等）は `instance` を渡して `update()` で実行する。新しいオブジェクトができる操作（公開、招待等）は `create()` で実行する。使わない方には `raise NotImplementedError` を書く。
- 操作者は、Viewから `serializer.save(actor=request.user)` で渡す。Serializerは `create()` / `update()` で `validated_data.pop("actor")` として取り出し、ドメインメソッドに渡す。`self.context["request"].user` を読むのは、検証に利用者がどうしても必要な場合だけにする。
- 実行の中身は、Modelのドメインメソッド（例：`run.request_cancel()`）を呼ぶ。ドメインの例外は、Serializer側で `ValidationError` や `Conflict`（409、`utils.exceptions`）に変換する。
- レスポンスの形が入力と異なる場合は、`to_representation` で出力用のSerializerに処理を任せる。`@extend_schema` には `request=` に入力用、`responses=` に出力用のSerializerを指定する。

```python
class RunCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate(self, attrs):
        if not self.instance.is_cancellable():
            raise serializers.ValidationError({"non_field_errors": _("この実行は停止できません")})
        return attrs

    def update(self, instance: Run, validated_data) -> Run:
        actor = validated_data.pop("actor")
        try:
            instance.request_cancel(validated_data.get("reason", ""), actor=actor)
        except InvalidTransition as exc:
            # validate()の後に、Workerが先に状態を変えた場合
            raise Conflict() from exc
        return instance

    def create(self, validated_data):
        raise NotImplementedError("instanceを渡して使う")

    def to_representation(self, instance: Run):
        return RunSerializer(instance, context=self.context).data
```

実装の全体は [backend-examples.md](backend-examples.md) の例3・例4を参照する。

### 3.5 To<状態>Serializer（業務ワークフロー限定）

人が業務の状態を一段ずつ進めるワークフローには、遷移先ごとのSerializerと `to_<状態>` actionの組合せを使ってよい（例：経費申請の `ToSubmittedSerializer`、`ToApprovedSerializer`）。これは特殊な形で、状態遷移を常にこの形式で書くわけではない。

- 使うのは、利用者の操作がそのまま「この状態に進める」という意味を持つ場合だけにする（提出済みにする、承認済みにする等）。
- RunやDeliveryのように、Worker・Runner・復旧処理などシステムが状態を進めるものには使わない。APIは依頼（停止、回答、追加指示）を受け付ける操作として§3.4の形で作り、状態はその結果としてサーバー内部で変える。

```python
class BaseExpenseReportStatusChangeSerializer(serializers.Serializer):
    STATUS: ExpenseReportStatus
    comment = serializers.CharField(required=False, allow_blank=True)

    def update(self, instance: ExpenseReport, validated_data) -> ExpenseReport:
        actor = validated_data.pop("actor")
        with transaction.atomic():
            instance.set_status(self.STATUS, validated_data.get("comment", ""), actor=actor)
        return instance

    def create(self, validated_data):
        raise NotImplementedError("instanceを渡して使う")


class ToApprovedSerializer(BaseExpenseReportStatusChangeSerializer):
    """経費申請を承認済みにする"""

    STATUS = ExpenseReportStatus.APPROVED

    def validate(self, attrs):
        if self.instance.status != ExpenseReportStatus.SUBMITTED:
            raise serializers.ValidationError({"non_field_errors": _("経費申請のステータスが不正です")})
        return attrs
```

- 遷移元の状態の確認は `validate()` で行う。
- ViewSetでは、`to_<状態>` actionから共通の `_change_status()` を呼ぶ（§4.3）。

### 3.6 Serializer共通

- `ModelSerializer` でない `Serializer` では、使わない `create` / `update` に `raise NotImplementedError` を書く。表示専用で書き込みが一切ない場合は、書かなくてよい。
- `SerializerMethodField` の `get_xxx` には戻り値の型ヒントを付ける（drf-spectacularのスキーマに反映される）。dictやlistを返す場合は `@extend_schema_field` で形を指定する。
- 出力専用の項目でも `serializers.CharField(read_only=True)` のように型の決まったフィールドで宣言する。`SerializerMethodField` は計算が必要な場合だけに使う。choicesの表示名は `serializers.CharField(source="get_<field>_display", read_only=True)` として `<field>_display` で出す。
- 入力の正規化は `to_internal_value` をオーバーライドしたカスタムフィールド（`utils.fields.NormalizedTextField` 等）、または `validate_<field>` で行う。
- `context["request"]` に依存する処理は、`request` がない場合（テストや内部呼出し）も動くようにする。

---

## 4 View

```python
@extend_schema(description="会話のメッセージに関するAPI", tags=["app/conversation/message"])
class AppConversationMessageViewSet(MyConversationMixin, ListModelMixin, CreateModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    queryset = ConversationMessage.objects.select_related("conversation", "sender").prefetch_related("attachments")
    serializer_class = MessageSerializer
    ordering = "-pk"

    def perform_create(self, serializer):
        # 複数テーブルの書き換えは MessageSerializer.create() の transaction で囲んでいる
        serializer.save(conversation=self.conversation, sender=self.request.user, role=MessageRole.USER)
```

### 4.1 ViewSetの基本

- `ModelViewSet`、`ReadOnlyModelViewSet`、または `GenericViewSet` と必要なmixinの組合せで書く。不要な操作（update、destroy等）はmixinを外して公開しない。
- `lookup_field = "uid"` を設定する。例外として、版番号で識別する子リソース（`SkillVersion` 等）は `lookup_field = "number"` としてよい。
- `queryset` はクラス属性に書き、論理削除の除外、`select_related`、`prefetch_xxx()` までを含める。利用者ごとの絞り込みは `get_queryset()` で `super().get_queryset().filter(...)` として追加する。
- `ordering` を必ず指定する（カーソル方式のページングに必要）。カーソル方式では並び順が一意に決まる必要があるため、最後の項目は一意な項目にする（例：`("-last_posted", "-pk")`、`("name", "pk")`、`("-created", "-pk")`）。`"-pk"` や `"sequence"`（親の中で一意）のように、それだけで一意な場合は1項目でよい。
- 一覧の検索条件は、原則として `django_filters.FilterSet` で定義する。FilterSetは同じ `views.py` の、使うViewSetより上に置き、`filterset_class` に指定する。完全一致だけの単純な絞り込み（`status` 等）に限り、`filterset_fields` で済ませてよい。
- 論理削除するModelは、`utils.views.SoftDeleteMixin` を使って `perform_destroy` で `deleted=True` にする。

### 4.2 利用者別のViewSet

- 同じリソースでも、利用者の範囲ごとにViewSetを分ける（例：管理者向けの `XxxViewSet` と通常社員向けの `AppXxxViewSet`。名前の付け方は下の表のとおり）。共通部分は `BaseXxxViewSet(ModelViewSet, ABC)` にまとめる。
- Yorikiでは次の命名にする。

| 接頭辞 | 利用者 | 認証 | permission |
|---|---|---|---|
| `App*` | 業務画面（利用と業務の管理。自分の会話・Run、Skill、タスク） | 既定（セッション） | `AppUser`。対象はQuerySetの絞り込みとModelの判定（§6.3）で決める |
| （接頭辞なし） | 管理画面（基盤の管理） | 既定（セッション） | `ModelPermissions`、または `require_permission(AppPermission.XXX)` |
| `Hook*` | Trigger・Channelの受信endpoint | ViewSetで `authentication_classes` を指定する（連携ごとの署名・token） | 認証した連携に対象を限るpermissionを指定する |

- `DEFAULT_PERMISSION_CLASSES` は `DenyAll` なので（§6.1）、`permission_classes` を書き忘れたViewSetは誰も使えない（基盤管理者も）。すべてのViewSetで明示する。
- 利用者の範囲で絞り込む処理はMixinにまとめる。URLに含まれる親IDは、Mixinのpropertyで `get_object_or_404` によって取得し、`hasattr` で結果をキャッシュする。

```python
class MyConversationMixin:
    """URLのconversationをログインユーザーの会話に限定して取得する"""

    @property
    def conversation(self) -> Conversation:
        if not hasattr(self, "_conversation"):
            self._conversation = get_object_or_404(
                Conversation.objects.all(), uid=self.kwargs["conversation"], owner=self.request.user, deleted=False)
        return self._conversation

    def get_queryset(self):
        return super().get_queryset().filter(conversation=self.conversation)
```

- 権限は必ずサーバー側の `permission_classes` と `get_queryset` の絞り込みで強制する。LLMの判断やプロンプトの指示に頼らない（design.md §1）。

### 4.3 アクション

- CRUD以外の操作は、`@action(detail=True, methods=["post"], serializer_class=...)` で定義する。入出力のSerializerの選び方は§3.4に従い、必ず `@extend_schema` の `request=` と `responses=` を明示する。
- action名は操作を表す動詞にする（`cancel`、`answer`、`publish`、`invite`、`subscribe`）。
- actionの本体は、DRFの標準の流れどおりに書く。操作者は `serializer.save(actor=request.user)` で渡す（§3.4）。

```python
@extend_schema(description="実行の停止を依頼する", request=RunCancelSerializer, responses=RunSerializer)
@action(detail=True, methods=["post"], serializer_class=RunCancelSerializer)
def cancel(self, request, *args, **kwargs):
    serializer = self.get_serializer(instance=self.get_object(), data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save(actor=request.user)
    return Response(serializer.data)
```

- 本文のない操作は、Serializerを作らずに書く。入力の検証が不要なので、Viewから対象のドメインメソッドを直接呼んでよい（§0.3）。

```python
@extend_schema(description="会話の通知を受け取るようにする", request=None, responses=ConversationSerializer)
@action(detail=True, methods=["post"], serializer_class=ConversationSerializer)
def subscribe(self, request, *args, **kwargs):
    conversation = self.get_object()
    conversation.subscribe(request.user)
    return Response(self.get_serializer(conversation).data)

@extend_schema(description="会話の通知を止める", request=None, responses={204: None})
@action(detail=True, methods=["post"], serializer_class=None)
def mute(self, request, *args, **kwargs):
    self.get_object().mute(request.user)
    return Response(status=status.HTTP_204_NO_CONTENT)
```

- 新しいオブジェクトができるactionでは、`self.get_serializer(data=request.data)` で作り、親や操作者は `serializer.save(skill=skill, actor=request.user)` のように渡す。レスポンスは `201` で返す。
- システムが進める状態（Run、Delivery等）を直接変更するactionは作らない。APIが受け付けるのは依頼や回答で、状態はその結果としてサーバー内部で変わる。
- 人が業務状態を進めるワークフロー（§3.5）に限り、`to_<状態>` actionを並べて、共通の `_change_status()` から呼んでよい。

- 件数を返すだけのような軽いアクションは `detail=False` にして、`CountSerializer` のような小さなレスポンス用Serializerで返す。
- 複数のテーブルを書き換える処理（action、`perform_create`、Serializerの `create()` / `update()`）はtransactionで囲む。ドメインメソッド（`@transaction.atomic`）やSerializerの `create()` / `update()` の中で囲んでいれば、Viewで重ねて付けなくてよい。
- 同時実行で重複が起きうる `get_or_create` 等は `utils.lock.lock(name, timeout=...)`（PostgreSQLのadvisory lock）で囲む。ただし、RunやDeliveryの実行権の獲得はlockではなく、design.md §5.2・§6.3のclaim / lease / fencing tokenで行う。

### 4.4 スキーマ

- すべてのViewSetに `@extend_schema(description="日本語の説明", tags=["<利用者>/<リソース>"])` を付ける。tagの先頭は利用者で分け、`app/<リソース>`（通常社員）、`manage/<リソース>`（管理者）、`hooks/<リソース>`（Webhook・Channel）とする。子リソースは `app/run/event` のように続ける。
- `@action` には `@extend_schema(description=..., request=..., responses=...)` を付ける。リクエストやレスポンスがない場合は `None` を明示する。
- 1か所でしか使わない小さな入出力でも、`inline_serializer` は使わずにSerializerクラスを作る（§0.2）。

### 4.5 APIViewと非同期view

- `APIView` は、ViewSetで表せない単発のendpoint（ヘルスチェック等）だけに使う（§0.1）。この場合も `permission_classes`、`@extend_schema`、名前付きのSerializerを付ける。
- ヘルスチェックの `PingAPIView`（`utils/views.py`）は `/api/ping/` に置き、`permission_classes = [AllowAny]`、レスポンスは `PingSerializer` とする。
- SSEのストリームは、DRFを使わずDjangoの非同期viewで `StreamingHttpResponse` を返す。認証・権限・スキーマの付け方は [realtime.md](realtime.md) §4 に従う。権限の判定には、対応するViewSetと同じQuerySetの絞り込みを使う。

---

## 5 URL

```python
# <app>/urls.py
from rest_framework.routers import DefaultRouter

from .views import ConversationViewSet, AppConversationViewSet, AppConversationMessageViewSet

router = DefaultRouter()

router.register("v1/conversations", ConversationViewSet, basename="conversation_viewset")

router.register("app/v1/conversations", AppConversationViewSet, basename="app_conversation_viewset")
router.register("app/v1/conversations/(?P<conversation>[^/]+)/messages", AppConversationMessageViewSet,
                basename="app_conversation_message_viewset")

urlpatterns = router.urls
```

```python
# app/urls.py（抜粋）
urlpatterns = [
    path("api/", include([
        path("ping/", PingAPIView.as_view()),
        path("admin/", admin.site.urls),
        path("accounts/", include("allauth.urls")),
        path("schema/", SpectacularAPIView.as_view(), name="schema"),
        path("schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="schema")),
        path("", include("user.urls")),
        path("", include("conversation.urls")),
        # ...各アプリ
    ])),
]
```

- APIのURLはすべて `/api/` の下に置く。`app/urls.py` で `path("api/", include([...]))` とし、各アプリの `urls.py` はその中で `path("", include("<app>.urls"))` として取り込む。nginxと開発用のプロキシはパスを書き換えずに転送する（deployment.md §4.1）。
- 各アプリの `urls.py` では `DefaultRouter` に登録する。`/api/` を含めずに、次のprefixから書く。

| prefix | 対象 |
|---|---|
| `app/v1/` | 通常社員向けUI。ログイン中の利用者自身の情報（`app/v1/my/`）と認証（`app/v1/auth/session/`、`app/v1/auth/providers/`）も、役割によらずここに置く |
| `v1/` | 管理者向けUI |
| `hooks/v1/` | Trigger・Channelの受信 |
| `accounts/` | django-allauth（SSOのログイン開始とコールバック） |
| `ping/` | ヘルスチェック（`AllowAny`、§4.5） |
| `files/<token>/` | 成果物の期限付き取得（セッションを使わずtokenを確認する。deployment.md §4.1） |

- リソース名は複数形のsnake_caseにする（例：`approval_requests`、`subscribed_boards`）。
- 子リソースは `(?P<親名>[^/]+)/子` の形で入れ子にする。`basename` は `<prefix>_<リソース>_viewset` とする。
- OpenAPIのスキーマとSwagger UIは、すべての環境で `/api/schema/` に置き、ログインした利用者だけが見られるようにする（`SPECTACULAR_SETTINGS` の `SERVE_PERMISSIONS` に `AppUser`）。Django管理画面（`/api/admin/`）は基盤管理者だけが使える。どちらも送信元のIPアドレスでは守らない（IPの制限は導入先が任意で追加する）。
- テストでも `/api/` から始まるURLで呼ぶ（例：`/api/app/v1/runs/{uid}/cancel/`）。

---

## 6 設定と横断部品

### 6.1 settings

- `app/settings.py` を基本設定にし、環境ごとの差分を `settings_docker.py`、`settings_production.py`、`settings_test.py` に分ける。差分のファイルは `from .settings import *` で基本設定を取り込んで上書きする。
- 秘密情報と環境依存の値は、すべて環境変数から `os.getenv` で読む。settingsファイルやコードに値を直接書かない。変数名は `YORIKI_` で始める（Django・Nuxtの規定の名前を除く）。
- ローカル開発では、リポジトリルートの `.envrc`（direnv）で環境変数を設定する。`.envrc.example` をコピーして値を埋める。`.envrc` はgitに入れない。
- 本番では `.envrc` を使わず、実行環境から環境変数を渡す（Docker Composeの `env_file` / `environment`、systemdの `EnvironmentFile`、クラウドのSecret管理など）。アプリは渡し方に依存せず、環境変数を読むだけにする。
- 新しい環境変数を追加したら、`.envrc.example` に説明付きで追記する。秘密情報の実際の値は書かない。
- `REST_FRAMEWORK` は次の方針にする。

```python
REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "utils.pagination.CursorPaginationWithPageSizeQuery",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ),
    "DEFAULT_AUTHENTICATION_CLASSES": ("utils.authentication.SessionAuthentication",),  # §6.2
    "DEFAULT_PERMISSION_CLASSES": ("utils.permissions.DenyAll",),  # 指定漏れは誰も通さない
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "utils.handlers.exception_handler",
}
```

- Yoriki自身のDBはPostgreSQLで、ドライバはpsycopg 3（依存は `psycopg[binary,pool]`）を使う。DjangoはUvicorn（ASGI）で動かし、同期のviewはリクエストごとに別のスレッドで実行されるので、接続を持ち続ける `CONN_MAX_AGE` ではなく、psycopg 3の接続プールを使う。
- qcluster（`manage.py qcluster`）ではプールを使わない（`settings.USE_DATABASE_POOL`）。Django Q2はworkerを `fork` で作り、Djangoはプールをクラス変数に持つので、forkの前に `close_all()` してもプールが開いた接続はforkした先のプロセスと共有される。共有した接続に複数のプロセスが同時に問い合わせると、別の問い合わせの結果を読んだり、transactionが混ざったりする。qclusterでは、Q2がtaskの前後で古い接続を閉じ、taskごとに接続する。

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("YORIKI_DATABASE_NAME"),
        "USER": os.getenv("YORIKI_DATABASE_USER"),
        "PASSWORD": os.getenv("YORIKI_DATABASE_PASSWORD"),
        "HOST": os.getenv("YORIKI_DATABASE_HOST"),
        "PORT": os.getenv("YORIKI_DATABASE_PORT", "5432"),
        "CONN_MAX_AGE": 0,  # プールを使うので、Djangoの側では接続を持ち続けない
        "OPTIONS": {
            "pool": {
                "min_size": int(os.getenv("YORIKI_DATABASE_POOL_MIN", "2")),
                "max_size": int(os.getenv("YORIKI_DATABASE_POOL_MAX", "10")),
                "timeout": 10,
            },
        },
    }
}
```

- DBへの接続数は、およそ「Uvicornのワーカー数 × プールの最大数 ＋ qclusterのプロセス数 ＋ SSEのLISTEN（プロセスごとに1本）」になる。サーバーを増やすときは、PostgreSQLの `max_connections` に収まるか確認する。PgBouncerを入れる場合、transactionモードではLISTENが使えないので、LISTENの接続はPgBouncerを通さない（realtime.md §4.1）。
- SSEのLISTENには、ORMのプールとは別に、psycopg 3の非同期接続（`AsyncConnection`）をプロセスごとに1本持つ（realtime.md §4）。
- Djangoの非同期ORM（`aget`、`afilter` 等）は、内部では同期の接続をスレッドで使う。非同期のview（SSE）でイベントループを止めずにDBを読むために使い、非同期のviewから同期のORMを直接呼ばない。
- Connector（design.md §4.9）のドライバ（pyodbc、psycopg 3、PyMySQL）は、Yoriki自身のDBとは別に、同期で使う。Connectorを呼ぶのはqclusterと同期のviewだけなので、非同期のドライバは使わない。

### 6.2 認証（社内SSOとYorikiのアカウント）

- ログインはバックエンドで処理し、Djangoのセッションでログイン状態を保つ。フロントエンドにトークンを持たせない（frontend-conventions.md §4）。ログインの方法は社内SSOとYorikiのアカウント（メールアドレスとパスワード）の2つで、どちらを使うかは有効なSSOの設定があるかで決まる（design.md §10.0）。
- SSOはdjango-allauthのsocialaccountで実装する。最初に対応するのはGoogle WorkspaceとMicrosoft 365（Entra ID）とする。Auth0やその他のOpenID Connectの認証基盤は、必要になったときに同じ仕組みで追加する。
- SSOの設定（種別、client ID、client secret、テナントID、Workspaceのドメイン、許可するメールドメイン、有効・無効）は、`user` アプリのModel（`SSOProvider`）に持たせ、管理画面（`v1/sso_providers`、`ModelPermissions`）で登録する。環境変数では設定しない。client secretは `EncryptedSecretField`（§6.4）にし、種別は作成した後は変えない（更新用のSerializerで読み取り専用）。
- django-allauthへは、`SocialAccountAdapter.list_apps()` で有効な `SSOProvider` から保存しない `SocialApp` を作って渡し、client secretはその中でだけ復号する。allauthの `SocialApp` のテーブルとDjango管理画面のその画面は使わない（client secretが平文で保存されるため）。
- 前段に認証プロキシ（oauth2-proxy等）を置く場合も、ログインは常にYoriki自身で行う。認証プロキシはバリアとして扱い、プロキシが付けるヘッダーやIDトークンで利用者を決めない（前段の構成は deployment.md §8.6）。
- 自社のアカウントだけを受け付ける。Entra IDはテナントID、Google Workspaceは `hd`（ドメイン。Googleの設定では必須にする。IDトークンの `hd` が一致しないアカウントは、個人のGoogleアカウントとみなして拒否する）、後から追加する認証基盤はissuerで確認する。どの認証基盤でも、メールアドレスのドメインがその設定の許可するドメインにあることを確認する（`user/adapters.py`）。許可しないアカウントは `/login?error=not_allowed`、認証の失敗は `/login?error=failed` へ戻す。
- SSOで初めてログインしたとき、同じメールアドレス（大文字・小文字を区別しない）のユーザーがいれば、そのユーザーに紐付ける（`pre_social_login` で `sociallogin.connect()`）。いなければ作成し、通常社員（所属もGroupもない）から始める。Group（permissionの集まり）と所属は管理画面で割り当て、基盤管理者は `is_superuser` にする（§6.3。IdPのグループとの連携は必要になってから追加する）。
- Yorikiのアカウントは、ユーザー管理者（`user.add_user` 等を持つ人）が管理画面（`v1/users`。Groupの選択肢は `v1/groups`）で作る。ユーザーは削除せず、無効にする。作成と再設定では、期限付きで1回だけ使える招待のtoken（既定は7日。DBにはハッシュだけを保存する）を発行し、そのURLを応答に一度だけ含めて画面に表示する。tokenはアクセスログに残らないよう、URLのフラグメント（`/invitation#<token>`）に置く。本人の画面は本文でtokenを送り、`POST app/v1/auth/invitations/verify/`（宛先のメールアドレスと期限を返す）と `POST app/v1/auth/invitations/accept/`（パスワードを設定してログインする）を呼ぶ。Yorikiからメールは送らない。
- `verify`・`accept` では、発行した人が今も有効で、対象のpermissionが発行した人の今のpermissionに含まれるかも確かめ、外れていれば断る（authorization.md §10）。
- 有効なSSOの設定がある間は、招待・再設定のtokenを基盤管理者以外に発行せず、`verify`・`accept` でも対象が基盤管理者でなければ断る（ログインの判定と同じ `PasswordBackend` の規則を使い、セッションを作る前に確かめる）。SSOの設定が有効になった時点（作成・有効への変更）で、基盤管理者以外の未使用のtokenを同じtransactionで無効にする。
- パスワードでのログインは `POST app/v1/auth/session/`（メールアドレス、パスワード）で行う。判定は認証バックエンド（`utils.backends.PasswordBackend`）に置き、有効なSSOの設定があれば基盤管理者（`is_superuser`）だけに、なければ有効なユーザーすべてに許可する。パスワードは `AUTH_PASSWORD_VALIDATORS` で検証する。allauthのメール・パスワードのログインとアカウント作成は使わない（`SOCIALACCOUNT_ONLY`）。
- ログインの試行回数の制限は django-axes で行う（記録はPostgreSQL）。失敗はアカウント（`username`）ごとと送信元のIPアドレスごとに別々に数える（`AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]`）。アカウントと送信元のIPアドレスの組だけで数えると、多数のIPアドレスから同じアカウントを推測できるため。止める時間は短くし（`AXES_COOLOFF_TIME` は15分）、解除は `axes_reset_username` 等の管理コマンドで行う。止めたことはログに残し、運用の通知に流す。送信元のIPアドレスは `REMOTE_ADDR` を使う（nginxの `set_real_ip_from` とUvicornの `--proxy-headers`・`--forwarded-allow-ips` で利用者のIPアドレスになる。deployment.md §8）。Django管理画面のログインも同じ制限を受ける。
- 最初の基盤管理者は `createsuperuser` で作る。SSOの設定の誤りや認証基盤の停止に備え、基盤管理者のパスワードでのログインはSSOの有無に関係なく残す。
- `DEFAULT_AUTHENTICATION_CLASSES` は `utils.authentication.SessionAuthentication` だけにする。DRF標準の `SessionAuthentication` は未ログインを403で返すため、`authenticate_header` を実装したサブクラスにして401を返す。フロントエンドは401でログイン画面へ移動し、403（権限不足）とは区別する。JWT、Token、Basicの認証は使わない。
- CSRFはDjango標準の仕組みを使う。`GET /api/app/v1/auth/session/`（`ensure_csrf_cookie`）でCSRFのCookieを渡し、書き込みの要求は `X-CSRFToken` ヘッダーで検証する。パスワードでのログインは `POST /api/app/v1/auth/session/`、ログアウトは `DELETE /api/app/v1/auth/session/` で行う。有効なログイン方法の一覧（SSOの認証基盤と、パスワードの欄を出すか）は `GET /api/app/v1/auth/providers/` で返す。
- セッションCookieは `Secure`、`HttpOnly`、`SameSite=Lax` にする。画面とAPIは同じオリジンに置くので、CORSは使わない。
- APIのURLは `/api/` の下に置く（§5）。nginxと開発用のプロキシはパスを書き換えずに転送する（deployment.md §4.1）。
- 別の認証を使う入口は、ViewSetで `authentication_classes` を指定して分ける（§4.2）。`Hook*`（`hooks/v1/`）は連携ごとの署名やtokenで認証し、セッションは使わない。

### 6.3 utilsとcommonに置くもの

次のモジュールを、この名前と役割で用意する。

| モジュール | 内容 |
|---|---|
| `utils/fields.py` | `NanoidField`、`EncryptedSecretField`・`SecretValue`（Secretの暗号化保存。§6.4）、`NormalizedTextField`（改行・行末空白の正規化）、ファイル・Base64系のSerializerフィールド |
| `utils/admin.py` | `ReadOnlyAdmin`（Django管理画面で見るだけにするModelAdmin。§7） |
| `utils/views.py` | `SoftDeleteMixin`、利用者範囲のMixin、`PingAPIView` |
| `utils/serializers.py` | `CountSerializer`、`PingSerializer`、`ReadOnlyModelSerializer`、複数アプリで使う最小Serializer |
| `utils/permissions.py` | `AppPermission`（Yorikiで定義するpermissionの一覧）、`AppUser`（ログインしている社員）、`ModelPermissions`（管理のModelの標準のpermission）、`require_permission()`、`DenyAll`（書き忘れを閉じる既定） |
| `utils/authentication.py` | 401を返す `SessionAuthentication`（とそのOpenAPIの定義）、`Hook*` 用の認証クラス |
| `utils/backends.py` | パスワードでのログインの認証バックエンド（有効なSSOの設定があれば基盤管理者だけに許可する） |
| `utils/exceptions.py` | `Conflict`（409）等、DRFの `APIException` のサブクラス |
| `utils/pagination.py` | `CursorPaginationWithPageSizeQuery`（`page_size` クエリ、上限200） |
| `utils/handlers.py` | DRFの例外ハンドラ（500系だけ通知に流す） |
| `utils/lock.py` | DBのadvisory lock |
| `utils/crypto.py` | Secretの暗号化・復号（Fernet。鍵は `YORIKI_SECRET_ENCRYPTION_KEYS`）。通常は `EncryptedSecretField` を通して使う |
| `common/management/commands/` | 複数アプリにまたがる管理コマンド（`rotate_secret_encryption`） |
| `common/choices.py` | 複数アプリで使うChoices |

- 権限の設計（範囲、3つの層、実行許可）は [authorization.md](authorization.md) を正とする。ここではDjangoでの書き方を定める。
- 権限は「所属」と「permission」の2軸で判定する。判定にGroupを使わない（`user.groups` やGroupの名前で絞らない）。Userのフラグも判定に使わない。
- **所属**は、`Department.objects.usable_by(user)`（使える部門）と `Department.objects.manageable_by(user)`（管理できる部門）、`Department.objects.joined_by(user)`（実際の所属。permissionで広がらない。部門タスクのチャットからの起動と、本人の所属の表示に使う）だけで判定する。ほかの場所で `DepartmentMembership` を直接引かない。使える部門は、permission `user.can_use_all_departments` か `user.can_manage_all_departments` を持てばすべての部門を返す。どの関数も、有効な部門（無効にしていない部門）だけを返す。管理できる部門は、`user.can_manage_all_departments` を持つときだけすべての部門を返す（`can_use_all_departments` では広がらない）。1つのリクエストの中では結果を `request.user` に持たせて使い回し、所属・Groupを変える処理の後では使わない。
- **permission**はDjangoのpermissionで判定する。
  1. Yorikiで定義するpermissionは `utils.permissions.AppPermission` に（app_label、codename、表示名）として1か所にまとめる。codenameは `can_<動詞>_<対象>` にする（例：`skill.can_publish_company_skills`）。
  2. 対象アプリのModelの `Meta.permissions` に `AppPermission.XXX.meta` を書き、マイグレーションでpermissionを作る。
  3. Groupは「permissionの集まり」。初期のGroup（authorization.md §11）は、`user` のデータのマイグレーションで最初に一度だけ作る（`migrate` のたびに作り直さない。消したGroupや外したpermissionを戻さないため）。マイグレーションの中では、初期のGroupに入れるpermissionを `create_permissions` を `apps` 付きで呼んで先に作る。
  4. Groupの中身はDjangoの管理画面（`/api/admin/`）で編集する。ユーザーへの割り当ては、Yorikiの管理画面（`v1/users`）かDjangoの管理画面で行う。
- **`is_superuser` はアプリの判定に使わない**。Djangoの `has_perm()` は有効なsuperuserにすべてのpermissionを返すので、アプリはいつもpermissionで判定すればよい。`is_superuser` を見るのは、Django管理画面、パスワードでのログインの判定（`utils.backends.PasswordBackend`。SSOが有効でも基盤管理者だけはパスワードでログインできる）、基盤管理者のアカウントの保護（変更と招待・再設定のリンクの発行・受け入れは基盤管理者だけ）だけにする。APIの応答（`app/v1/my/` やユーザーの管理API）に `is_superuser` を出さない。
- **範囲を持つもの**（Skill・Connector・タスク・入口・出口）の判定は、各アプリのQuerySetとModelに置き、Viewに条件を書かない。
  - 一覧の絞り込みはQuerySetのメソッド（`visible_to(user)`・`manageable_by(user)` 等）、1件の判定はModelの `can(user, 操作)` にする。操作は「見る・使う・管理・起動・結果を見る・ログを見る・候補に出す」等を区別する。範囲ごとの規則は1つの表に書き、QuerySetとModelの両方がそれを読む。
  - Viewは `get_queryset` でQuerySetのメソッドを使い、操作の前にModelの `can()` を呼ぶ。一覧では隠れるのにIDを指定すると読める、という食い違いを作らない。
  - Serializerの関連の項目（Skill・Connector・入口・出口のID）の `queryset` も同じメソッドで絞り、保存の直前（`validate`）にも `can()` で確かめる。
  - Worker（Django Q2）と管理コマンドでも同じ判定を呼ぶ。Q2にはIDだけを渡し、WorkerはユーザーとRunをDBから読み直して判定する（§8.4）。
  - 一覧は部門・範囲・持ち主・有効の条件でSQLで絞り、1件ずつ判定しない。
- **管理のModel**（基盤の管理。Connector・接続、LLMの接続・モデル設定、Runtime Profile、SSOの設定、ユーザー、部門と所属、入口・出口等）のViewSetは `ModelPermissions` で、操作ごとにそのModelの標準のpermission（一覧・詳細は `view_*`、作成は `add_*`、更新は `change_*`、削除は `delete_*`）を確かめる。所属は問わない。`@action` は `action_permissions = {"check": "view"}` のように種類を書く（書いていない操作は断る）。Djangoの管理画面と同じく、`change_*` を持つ人は `view` の操作もできる。対象のModelが違うとき（版の履歴等）は `permission_model` で指定する。
- Yorikiで定義したpermissionだけで決まる操作は `require_permission(AppPermission.XXX)` で確認する。`require_permission` は、ログイン済みで `user.has_perm()` が真の場合に許可するpermissionクラスを返す。Django標準のpermissionは `"<app_label>.<codename>"` の文字列で渡す。
- permissionを持つユーザーの一覧（通知の宛先等）は `User.objects.with_permission(AppPermission.XXX)` で取得する（直接・Group経由・基盤管理者を含む）。
- `AppUser` は `_AppUser(BasePermission)` を定義し、公開名を `AppUser = IsAuthenticated & _AppUser` にする。`DenyAll` はどの要求も通さない。
- 管理を委任したときの防止策（authorization.md §10）：ユーザーの更新APIで書ける項目は表示名・有効・Group・所属だけにする。自分の所属・Groupは変えられない。自分の持たないpermissionを含むGroupは付け外しできない。自分の持たないpermissionを持つアカウントは、変更も招待・再設定のリンクの発行もできない。画面には、操作する人から見た可否（`can_change`・`can_issue_invitation`）を返す。
- `app/v1/my/` は、本人の `all_permissions`（Group経由と直接を含む）、`departments`（実際の所属）、`usable_departments`、`manageable_departments` を返す（authorization.md §4.1.1）。1件ごとの操作の可否は、詳細のAPIで返す。

### 6.4 Secret

- 管理画面で登録するSecret（LLMのAPI key、Connectorのパスワード・token、HTTP Connectorのヘッダー、SSOのclient secret等）は、Modelの `utils.fields.EncryptedSecretField` に持たせる。平文の列を作らない。

```python
class LLMConnection(models.Model):
    api_key = EncryptedSecretField(_("API key"))  # blank=True、editable=False が既定
```

- フィールドの値は `SecretValue`。平文の文字列を代入するとその場で暗号化し、インスタンスに平文を残さない。`str()`・`repr()` は末尾の4文字だけ（`****abcd`）なので、ログやSerializerに紛れても平文は出ない。平文は `.reveal()` でだけ取り出し、外部へ接続する処理（Provider Adapter、Connector）の中でだけ呼ぶ。`bool()` でSecretの有無、`.hint` で末尾の4文字が分かる。
- Serializerでは、Secretを `write_only=True` の `CharField` で受け、応答には `has_<名前>` と `<名前>_hint` だけを出す。送られなければ変更しない。暗号化の鍵が設定されていなければ、保存時ではなく検証で入力エラーにする（`utils.crypto.is_encryption_configured()`）。
- 版（`*Version`）が参照する接続（LLMの接続等）は、接続先（種別、base URL等）を作成した後は変えられないようにする。接続先ごとに接続を作り、Secretは差し替えられるようにする。登録済みのSecretを別のサーバーへ送らせないためと、接続を参照する版の意味が後から変わらないようにするため。更新用のSerializer（`<名前>UpdateSerializer`）でその項目を `read_only_fields` にする。
- 版を持たない接続（ConnectorのDB接続。design.md §4.9.1）は、種別だけを変えられないようにし、ホスト等の接続先は資格情報を入れ直すことを条件に変えられる（接続先を変える更新でSecretが送られなければ入力エラーにする）。
- 監査ログ（auditlog）では、Secretの列を `exclude_fields` に入れる。Django管理画面には `editable=False` により出ない。
- Secretを持つModelは、Django管理画面では見るだけにする（§7）。管理画面のフォームにはSecretが出ないので、そこで作るとSecretのない有効な接続ができ、Serializerの検証（種別ごとの必須項目等）も通らないため。
- 鍵の交換は `rotate_secret_encryption` コマンド（`common`）で行う。`EncryptedSecretField` は自分を登録するので（`utils.fields.ENCRYPTED_SECRET_FIELDS`）、Secretを持つModelを追加してもコマンドの変更は要らない。

---

## 7 管理画面

```python
@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    raw_id_fields = ("conversation",)
    readonly_fields = ("uid", "created", "updated")
    list_display = ("id", "uid", "status", "created", "updated")
```

- `@admin.register` で登録する。
- FKは `raw_id_fields` にする（全件のプルダウンを作らないため）。
- `uid`、`created`、`updated` は `readonly_fields` に入れる。
- 計算した値を一覧に出す場合はメソッドを定義し、`short_description` に日本語名を設定する。
- 不変オブジェクトの管理画面は、`has_change_permission` と `has_delete_permission` で `False` を返す。
- Secretを持つModel（§6.4）と、作成・変更をSerializerやModelのメソッドに任せているModel（版を作るもの等）は、見るだけにする（`utils.admin.ReadOnlyAdmin` を継承する）。登録・変更はYorikiの管理画面（API）で行う。

---

## 8 管理コマンド・バッチ・非同期処理

### 8.1 使い分け

| 種類 | 置き場所 | 用途 |
|---|---|---|
| 管理コマンド | `<app>/management/commands/<name>.py` | 運用で繰り返し使う操作（清掃、再送、データのdump/load、Storage移行） |
| runscript | `scripts/<name>.py`（django-extensions） | 単発の移行、調査、ダミーデータ作成 |
| Q2 task | `<app>/tasks.py` | Run実行の通知、配信、定期清掃（Q2のScheduleで登録） |

### 8.2 管理コマンド

```python
"""期限切れArtifactを清掃するコマンド。

Usage:
    # 削除対象の件数だけを表示する
    python manage.py cleanup_artifacts --dry-run

    # 削除を実行する
    python manage.py cleanup_artifacts
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "保持期限を過ぎたArtifactを清掃する"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="変更せずに対象を表示する")
        parser.add_argument("--limit", type=int, default=1000, help="1回で処理する最大件数")

    def handle(self, *args, **options):
        if options["limit"] <= 0:
            raise CommandError("--limitは1以上を指定してください")
        ...
        self.stdout.write(self.style.SUCCESS("清掃完了"))

    def _collect(self, limit: int):
        ...
```

- 先頭のdocstringに、目的と `Usage:` の実行例を書く。
- `help` と引数の `help` は日本語にする。選択肢のある引数は `choices` で制限する。
- 入力が不正な場合は `CommandError` を送出する。
- 出力は `print` ではなく `self.stdout.write` を使い、完了時には `self.style.SUCCESS` を使う。件数は `f"{count:,}件"` の形で表示する。
- 処理は `handle` から `_dump` / `_load` のような非公開メソッドに分ける。
- データを変更するコマンドには `--dry-run` を用意する。
- 大量に作成する場合は `bulk_create(..., batch_size=...)` を使う。DB固有の処理を書く場合は `connection.vendor` で分岐する。

### 8.3 runscript

- `scripts/<name>.py` に `def run(*args):` を定義し、`python manage.py runscript <name> --script-args ...` で実行する。
- 先頭のdocstringに `Usage:` を書く。`dry_run` 引数を受け付ける。
- 失敗の通知は、通知用のutil経由で行う。

### 8.4 Q2 task

- taskの引数にはIDだけを渡す（Runには `run_id`、配信には `delivery_id`）。本文、成果物、Secretをキューに載せない（design.md §10）。
- 実行状態の正本はPostgreSQLのRun / Deliveryに置く。Q2の結果テーブルを履歴として使わない。
- taskは再配送されても安全にする。最初にclaimし、claimできなければ何もせずに終わる。
- Q2のクラスタは1つにし、Runの実行taskも配信・Outbox・清掃のtaskも同じクラスタへ送る。増やすときはqclusterの数で調整する。Runは長くworkerを占有するので、1つのqclusterで同時に動かすRunの数を `YORIKI_RUNS_PER_QCLUSTER` までにし、上限に達していればclaimしない（deployment.md §3.2）。

---

## 9 テスト

- pytestとpytest-djangoを使う。`pyproject.toml` の `addopts` で `--ds=app.settings_test` を指定し、`python_files = ["tests.py", "test_*.py", "*_test.py"]` を設定する。
- テストはローカルでもCIでも、本番と同じPostgreSQLで実行する（`select_for_update` の行ロック、advisory lock、LISTEN/NOTIFY、JSONの検索等に依存するため）。SQLiteは使わない。ローカルでは `deploy/compose.dev.yaml` のPostgreSQLを起動しておく。
- テストデータは各アプリの `factories.py` に `DjangoModelFactory` で定義する。
- 複数アプリで使うfixtureはルートの `conftest.py` に置き、`@pytest.fixture(name="xxx")` で名前を付ける。ユーザーのfixtureは役割を表す名前にする（例：`app_user`、`other_user`、`platform_admin`）。権限の判定のテストは、必要なpermissionと所属だけを付けたユーザーで行い、superuser（`platform_admin`）では行わない（存在しないpermissionの名前でも通り、書き間違いを隠すため）。`platform_admin` は、Django管理画面とパスワードでのログインのテストに使う。
- APIを呼ぶクライアントのfixtureは `api_client` とする。`client` はpytest-djangoの同名のfixtureを隠してしまうので使わない。
- DBを使うテストには `@pytest.mark.django_db` を付ける。
- Serializerの検証は、Serializerを直接使ってテストする。

```python
class TestDestinationValidation:
    @pytest.fixture
    def base_data(self):
        return dict(name="通知先", kind=DestinationKind.WEBHOOK, address="https://example.com/hook")

    @pytest.mark.django_db
    def test_valid(self, base_data):
        s = DestinationSerializer(data=base_data)
        assert s.is_valid(), s.errors

    @pytest.mark.django_db
    def test_webhook_requires_https(self, base_data):
        base_data["address"] = "http://example.com/hook"
        s = DestinationSerializer(data=base_data)
        assert not s.is_valid()
        assert "address" in s.errors
```

- 権限、利用者範囲の絞り込み、状態遷移の可否、claimによる重複実行の防止は、必ずテストを書く。
- 本番データのIDに依存するコードは、`monkeypatch` でテスト用fixtureのIDに差し替えてテストする。

---

## 10 コードスタイル

- Python 3.13（リポジトリルートの `mise.toml` で固定）を使い、依存は `uv` で管理する（`uv add`、開発用は `uv add --group dev`）。
- lintと整形はruffで行う（`ruff check`、`ruff format`）。pylint・black・autopep8は使わない。設定はリポジトリルートの `ruff.toml` に書き、行の長さは120、規則は `DJ`（flake8-django）、`B`、`I`（import順）、`UP` 等を有効にする。コマンドとCIは development.md §3.1 に従う。
- 型チェックにはmypy（django-stubs、djangorestframework-stubs）を使う。設定は `api/pyproject.toml` に書く。VS CodeのPylanceの型検査は使わない。新しく書く関数には型ヒントを付ける。
- 警告は抑止せずにコードを直して解消する。`# pylint: disable=...` は書かない。どうしても抑止が必要な場合だけ `# noqa: <規則コード>` を付け、同じ行か直前の行に理由をコメントで書く。
- VS CodeではRuff拡張を使い、保存時に整形する。拡張はリポジトリルートの `ruff.toml` の同じ設定を読む（設定の詳細は development.md §3.0）。
- import文はファイル先頭にまとめる。順序は標準ライブラリ、サードパーティ、プロジェクト内の別アプリ、同じアプリの相対import（`from .models import ...`）とする（ruffの `I` 規則で整える）。
- 循環importになる場合、型注釈のためだけのimportであれば `if TYPE_CHECKING:` に入れ、型を文字列で書く。実行時に必要な場合だけ、関数内でimportし、その理由をコメントに書く。
- コメント、docstring、ユーザーに見せるメッセージは日本語で書く。ユーザーに見せる文字列は `gettext_lazy as _` で包む。
- 翻訳元の言語は日本語とする（`_("待機中")`）。当面は日本語話者が主に開発するため。他の言語は `locale/<言語>/LC_MESSAGES/django.po` に日本語からの訳を追加して対応する。エラーメッセージ・choicesのラベル・`verbose_name` は必ず `_()` で包み、後から翻訳できる状態を保つ。f-stringで値を埋め込まず、`_("%(name)sは使用できません") % {"name": name}` の形にする。
- フロントエンドも同じく日本語の文を翻訳キーにする（frontend-conventions.md §10）。サーバーが返すメッセージは、`Accept-Language` に応じてサーバー側で翻訳される。
- 命名：ModelはPascalCase、フィールドはsnake_case、真偽値は `is_xxx` か状態を表す形容詞・過去分詞（`approved`、`deleted`）にする。

---

## 11 避ける書き方

次の書き方は、Yorikiでは使わない。

- 同じメソッドを複数のViewSetに複製すること（`save_bookmark` 等）。共通Mixinにまとめる。
- コメントアウトしたコードや、使われていない `urls` 登録を残すこと。不要になったら削除する。
- `create()` の中の `assert False`。`raise NotImplementedError` にする。
- `Model.clean()` での `ValueError` の送出。`django.core.exceptions.ValidationError` を使う。
- Model・Admin・Serializer名のtypo。名前は正しい綴りにする。
- Viewやactionの中で `request.data.get(...)` から値を取り出して処理すること。Serializerを通す。
- `# ToDo: 後で直す` のまま入れる仮実装のpermission。permissionは最初から判定条件を確定させる。
- 動作中のコードで `user_id=1` のような固定IDに依存すること。システムユーザーはsettingsか専用のModelで解決する。
- Auth0のIDトークンによる認証、simplejwtのJWT、`BasicAuthentication`。認証はセッションだけにする（§6.2）。
- django-cors-headersと `CORS_ALLOW_ALL_ORIGINS`。画面とAPIは同じオリジンに置き、CORSは使わない。
- pylint・black・autopep8と、`# pylint: disable=...` のコメント。ruffとmypyに置き換える（§10）。
