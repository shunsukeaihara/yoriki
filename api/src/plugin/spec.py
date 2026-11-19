"""Pluginの宣言の型（design.md §3.11.1。書き方は plugin/CONTRACT.md）。

Pluginは `PluginSpec` を1つ作り、entry points（グループ `yoriki.plugins`）の値でそれを指す。entry pointの名前は
plugin_idと同じにする。本体のPluginは plugin/builtin_plugins.py で同じ型を作る。
"""

import builtins
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# 自社のPluginのentry pointsのグループ
ENTRY_POINT_GROUP = "yoriki.plugins"
# 本体のPluginのplugin_idの名前空間
BUILTIN_PREFIX = "yoriki."


class ExtensionType(StrEnum):
    """拡張種別（design.md §3.11）。基底クラスは plugin/interface.py の BASES"""

    TRIGGER = "trigger"
    NOTIFICATION = "notification"
    STORAGE = "storage"
    CONNECTOR = "connector"
    CHANNEL = "channel"


@dataclass(frozen=True)
class Extension:
    """1つのAdapter。`id` はどのPluginとも重ならない文字列で、plugin_idから始める"""

    id: str
    type: ExtensionType
    # 画面に出す名前（例：PostgreSQL）
    name: str
    # 実装のクラス。種別の基底クラスを継承する
    adapter: builtins.type
    # 対応能力。種別ごとの意味は CONTRACT.md
    capabilities: frozenset[str] = frozenset()
    # 設定のJSON Schema。画面の既定値（`default`）にも使う
    config_schema: dict[str, Any] = field(default_factory=dict)
    # 必要なSecretの種別（例：password）。値は持たない
    secrets: tuple[str, ...] = ()


@dataclass(frozen=True)
class PluginSpec:
    """Pluginの宣言。有効・停止の単位"""

    id: str
    name: str
    version: str
    extensions: tuple[Extension, ...]
    description: str = ""
    # 廃止の予告。理由を書くと deprecated になる（停止中なら有効にできず、新しく作るものの選択肢に出さない）
    deprecated: str = ""
    # 代わりのplugin_id
    replaced_by: str = ""
    # 有効にする時の自己診断（任意）。外へ出ず副作用のない確かめだけを行い、失敗は例外（文は画面に出す）
    self_check: Callable[[], None] | None = None
