"""ConnectorのAdapter。`get_adapter()` で取得し、共通の型（base.py）で呼ぶ。

Adapterは接続のextension_id（Pluginの宣言。design.md §3.11.1）で選ぶ。停止中・配備されていないPluginの接続は、
ドライバがないときと同じ ConnectorError（unavailable）にする
"""

from django.utils.translation import gettext as _

from plugin import registry
from plugin.errors import PluginUnavailable

from ..errors import ConnectorError, ConnectorErrorKind
from ..models import Connector, ConnectorVersion, DatabaseConnection
from .base import DatasourceAdapter


def adapter_class(extension_id: str) -> type[DatasourceAdapter]:
    try:
        extension, _spec = registry.get(extension_id)
    except PluginUnavailable as exc:
        raise ConnectorError(
            ConnectorErrorKind.UNAVAILABLE, _("この接続先の種別（Plugin）は、今は使えません（停止中等）")
        ) from exc
    return extension.adapter


def get_adapter(target: Connector | DatabaseConnection, version: ConnectorVersion | None = None) -> DatasourceAdapter:
    """Connectorなら、その接続と版（省略すると現在の版）で、公開した範囲の describe・query を行う。
    DB接続なら、接続の確認と表の一覧だけを行う（範囲がないので describe・query はできない）
    """
    if isinstance(target, Connector):
        return adapter_class(target.connection.extension)(target.connection, version or target.current_version)
    return adapter_class(target.extension)(target, version)
