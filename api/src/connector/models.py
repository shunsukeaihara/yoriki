"""外部データへの接続（Connector。design.md §4.9）。

基盤管理者が登録する。接続先と資格情報は接続（社内DBでは DatabaseConnection）に持ち、Connectorは1つの接続の上で
エージェントが扱える範囲を決める。1つの接続の上に、範囲と使える人の違うConnectorを複数作れる（design.md §4.9.1）。
資格情報は `EncryptedSecretField` で暗号化して保存し、平文はAdapterの中でだけ復号する（Runner・Sandbox・ログ・画面へ
出さない）。

エージェントが扱える範囲（境界。公開する表・ビュー、上限、機密のフラグ）は不変の版（ConnectorVersion）で持つ。
Runは開始時の版を固定して使う。

Connectorは範囲（部門・全社・システム）を持つ（authorization.md §5.2）。範囲と部門は作成時に決めて変えない。
会話で使えるのは、部門のものは使える部門の人、全社のものは全員で、システムのものは会話では使わない
（`ConnectorQuerySet.usable_by()`、`Connector.can()`）。作成・編集・削除と管理画面での参照は、所属を問わず
Django標準のpermission（`connector.add_connector` 等）だけで判定する（基盤の管理）。
接続は版を持たず、変えると次の操作から効く。
"""

from enum import Enum
from typing import Any

from auditlog.registry import auditlog
from django.conf import settings
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from user.models import Department, usable_department_ids
from utils.fields import EncryptedSecretField, NanoidField

from .errors import VersionConflict


class ConnectorKind(models.IntegerChoices):
    """Connectorの種別。今は社内DB（読取り専用のデータソース。design.md §4.9.2）だけ"""

    POSTGRESQL = 1, _("PostgreSQL")
    MYSQL = 2, _("MySQL")
    SQLSERVER = 3, _("SQL Server")

    @property
    def default_port(self) -> int:
        return {ConnectorKind.POSTGRESQL: 5432, ConnectorKind.MYSQL: 3306, ConnectorKind.SQLSERVER: 1433}[self]

    @property
    def default_schema(self) -> str:
        """表の名前にスキーマを書かなかったときのスキーマ。MySQLはDB名がスキーマなので空"""
        return {ConnectorKind.POSTGRESQL: "public", ConnectorKind.MYSQL: "", ConnectorKind.SQLSERVER: "dbo"}[self]


class TLSMode(models.IntegerChoices):
    """接続先との通信の暗号化"""

    DISABLE = 1, _("暗号化しない")
    REQUIRE = 2, _("暗号化する（証明書を確かめない）")
    VERIFY = 3, _("暗号化して証明書を確かめる")


class Operation(models.TextChoices):
    """Connectorの操作。値はToolの名前（harness.md §10.2）"""

    DATASOURCE_DESCRIBE = "datasource_describe", _("表・列の説明を見る")
    DATASOURCE_QUERY = "datasource_query", _("SELECT文で読み取る")


# 社内DBのConnectorの操作は、どれも読取り（design.md §4.9.1）
DATASOURCE_OPERATIONS = (Operation.DATASOURCE_DESCRIBE, Operation.DATASOURCE_QUERY)


class DatabaseConnection(models.Model):
    """社内DBへの接続先と資格情報。permissionを持つ人（既定では基盤管理者）だけが登録・変更する。エージェントには見せない。

    種別は作成した後は変えない（SQLの方言と既定のスキーマの意味が変わるため）。接続先（ホスト・ポート・DB名）は
    変えられるが、そのときはパスワードを入れ直す（登録済みのパスワードを別のサーバーへ送らないため。
    DatabaseConnectionUpdateSerializer）。版は持たず、変えると実行中のRunにも次の操作から効く
    """

    uid = NanoidField(unique=True)
    name = models.CharField(_("名前"), max_length=100)
    kind = models.PositiveSmallIntegerField(_("種別"), choices=ConnectorKind.choices)
    host = models.CharField(_("ホスト"), max_length=255)
    port = models.PositiveIntegerField(_("ポート"))
    database = models.CharField(_("DB名"), max_length=128)
    username = models.CharField(_("ユーザー名"), max_length=128)
    password = EncryptedSecretField(_("パスワード"))
    tls = models.PositiveSmallIntegerField(_("通信の暗号化"), choices=TLSMode.choices, default=TLSMode.VERIFY)
    connect_timeout_seconds = models.PositiveIntegerField(_("接続のタイムアウト（秒）"), default=10)
    # 全Workerを通した同時実行数の上限。この接続の上のConnectorを合わせて数える。Runnerが守る
    # （会話でConnectorを使う段階で実装する。design.md §4.9.5）
    max_concurrency = models.PositiveIntegerField(_("同時実行数の上限"), default=4)
    is_active = models.BooleanField(_("有効"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("接続先")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted=False), name="uniq_database_connection_name"
            ),
        ]

    def __str__(self):
        return self.name


class ConnectorScope(models.IntegerChoices):
    """Connectorの範囲（authorization.md §2）。値はSkillの範囲（skill.models.SkillScope）とそろえる。
    個人（本人のOAuthのtoken）は、会話でConnectorを使う段階で足す"""

    DEPARTMENT = 2, _("部門")
    COMPANY = 3, _("全社")
    SYSTEM = 4, _("システム")


class ConnectorAction(Enum):
    """Connectorに対する操作。管理画面の操作はpermissionだけで判定するので、ここには持たない"""

    USE = "use"  # 会話で使う


class ConnectorQuerySet(models.QuerySet):
    def in_service(self) -> "ConnectorQuerySet":
        """Connectorと接続が有効で、版があるもの"""
        return self.filter(
            deleted=False,
            is_active=True,
            current_version__isnull=False,
            connection__deleted=False,
            connection__is_active=True,
        )

    def usable_by(self, user) -> "ConnectorQuerySet":
        """会話で使えるConnector（`ConnectorAction.USE`）。部門のものは使える部門の人、全社のものは全員"""
        if not user.is_active:
            return self.none()
        return self.in_service().filter(
            models.Q(scope=ConnectorScope.DEPARTMENT, department__in=Department.objects.usable_by(user))
            | models.Q(scope=ConnectorScope.COMPANY)
        )


class Connector(models.Model):
    """1つの接続の上で、エージェントが扱える範囲（境界）を決める。

    使う接続は作成した後は変えない（公開した表・ビューは、その接続のDBのものなので）。今は社内DBのConnectorだけで、
    他の種類（SharePoint等）を足すときは、種類ごとの接続を持たせる
    """

    uid = NanoidField(unique=True)
    name = models.CharField(_("名前"), max_length=100)
    connection = models.ForeignKey(
        DatabaseConnection, on_delete=models.PROTECT, related_name="connectors", verbose_name=_("接続先")
    )
    current_version = models.ForeignKey(
        "ConnectorVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("現在の版"),
    )
    scope = models.PositiveSmallIntegerField(_("範囲"), choices=ConnectorScope.choices)
    # 部門の範囲の部門。範囲と部門は作成時に決めて変えない（別の部門へ移すのは組織の再編の操作。authorization.md §9）
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, null=True, blank=True, related_name="+", verbose_name=_("部門")
    )
    is_active = models.BooleanField(_("有効"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)

    objects = ConnectorQuerySet.as_manager()

    class Meta:
        verbose_name = _("Connector")
        verbose_name_plural = verbose_name
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(scope=ConnectorScope.DEPARTMENT, department__isnull=False)
                    | models.Q(scope__in=[ConnectorScope.COMPANY, ConnectorScope.SYSTEM], department__isnull=True)
                ),
                name="connector_scope_department",
            ),
            models.UniqueConstraint(fields=["name"], condition=models.Q(deleted=False), name="uniq_connector_name"),
        ]

    def __str__(self):
        return self.name

    @property
    def kind(self) -> int:
        return self.connection.kind

    def get_kind_display(self) -> str:
        return self.connection.get_kind_display()

    @property
    def version_number(self) -> int:
        return self.current_version.number if self.current_version else 0

    @property
    def operations(self) -> tuple[Operation, ...]:
        """このConnectorが提供する操作"""
        return DATASOURCE_OPERATIONS

    def in_service(self) -> bool:
        return (
            not self.deleted
            and self.is_active
            and self.current_version_id is not None
            and not self.connection.deleted
            and self.connection.is_active
        )

    def can(self, user, action: ConnectorAction) -> bool:
        """利用者がこの操作をできるか。一覧の `ConnectorQuerySet.usable_by()` と同じ規則"""
        if not user.is_active or not self.in_service():
            return False
        if self.scope == ConnectorScope.DEPARTMENT:
            return self.department_id in usable_department_ids(user)
        return self.scope == ConnectorScope.COMPANY

    def operations_for(self, user) -> set[Operation]:
        """利用者がこのConnectorで使える操作。使えるならすべての操作"""
        return set(self.operations) if self.can(user, ConnectorAction.USE) else set()

    @classmethod
    @transaction.atomic
    def create_with_version(
        cls, *, actor, change_reason: str = "", content: dict[str, Any], **fields: Any
    ) -> "Connector":
        """Connectorと最初の版を作る"""
        connector = cls.objects.create(created_by=actor, **fields)
        connector.revise(actor=actor, expected_version=0, change_reason=change_reason, **content)
        return connector

    @transaction.atomic
    def revise(self, *, actor, expected_version: int, change_reason: str = "", **fields: Any) -> "ConnectorVersion":
        """境界（公開する表、上限、機密のフラグ）を変えた新しい版を作り、現在の版にする。

        編集を始めた後に他の人が版を作っていた・削除されていたら VersionConflict。中身が現在の版と同じなら版を作らず、
        現在の版を返す。Connectorの行のロックは呼び出し元のtransactionの終わりまで続く
        """
        current = Connector.objects.select_for_update(of=("self",)).select_related("current_version").get(pk=self.pk)
        if current.deleted:
            # 編集を始めた後に削除された
            raise VersionConflict("deleted")
        latest = current.current_version
        latest_number = latest.number if latest else 0
        if latest_number != expected_version:
            raise VersionConflict(f"expected={expected_version} latest={latest_number}")
        if latest and all(getattr(latest, name) == fields[name] for name in VERSION_FIELDS):
            self.current_version = latest
            return latest
        version = ConnectorVersion.objects.create(
            connector=current, number=latest_number + 1, created_by=actor, change_reason=change_reason, **fields
        )
        current.current_version = version
        current.save(update_fields=["current_version", "updated"])
        self.current_version = version
        return version


# 版に入れる項目（境界）。変えると新しい版になる
VERSION_FIELDS = (
    "description",
    "default_schema",
    "tables",
    "max_rows",
    "max_result_bytes",
    "timeout_seconds",
    "is_sensitive",
)


class ConnectorVersion(models.Model):
    """Connectorの境界の版。作成後に変更・削除しない（切り戻しも新しい版を作る）。

    `tables` は公開する表・ビューの一覧で、`[{"schema": "public", "name": "vendors", "description": "取引先",
    "columns": {"code": "取引先コード"}}]` の形にする（列の説明は任意）。エージェントはここにある表・ビューだけを
    問い合わせられる。個人情報を含む列は、その列を除いたビューを作って公開する（design.md §4.9.2）
    """

    uid = NanoidField(unique=True)
    connector = models.ForeignKey(
        Connector, on_delete=models.PROTECT, related_name="versions", verbose_name=_("Connector")
    )
    number = models.PositiveIntegerField(_("版"))
    description = models.TextField(_("説明"), blank=True)
    # 表の名前にスキーマを書かなかったときのスキーマ。実行するSQLでは、表の名前にこのスキーマを付ける
    default_schema = models.CharField(_("既定のスキーマ"), max_length=128, blank=True)
    tables = models.JSONField(_("公開する表・ビュー"), default=list)
    max_rows = models.PositiveIntegerField(_("行数の上限"), default=1000)
    max_result_bytes = models.PositiveIntegerField(_("結果のサイズの上限（バイト）"), default=10 * 1024 * 1024)
    timeout_seconds = models.PositiveIntegerField(_("実行時間の上限（秒）"), default=30)
    # 個人情報を主な中身とする接続先。ローカルLLMのモデルを使うタスク・会話にだけ割り当てる（design.md §4.9.1）
    is_sensitive = models.BooleanField(_("機密"), default=False)
    change_reason = models.CharField(_("変更理由"), max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name=_("作成者")
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Connectorの版")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["connector", "number"], name="uniq_connector_version_number"),
        ]

    def __str__(self):
        return f"{self.connector} v{self.number}"


# パスワードは暗号化した値でも監査ログに残さない
auditlog.register(DatabaseConnection, exclude_fields=["password", "updated"])
auditlog.register(Connector, exclude_fields=["updated"])
auditlog.register(ConnectorVersion)
