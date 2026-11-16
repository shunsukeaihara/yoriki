"""Plugin Registry（design.md §3.11.1）。

各プロセスは、配備されているコード（本体のPluginと、entry points `yoriki.plugins` の自社のPlugin）を読み込み、形と宣言を
確かめる（`loaded()`）。DBの行は `sync_plugins()`（migrateの後と管理コマンド `sync_plugins`）で作り直し、有効・停止は
DBで持つ。Adapterを使えるのは、このプロセスが読み込めて確かめに通り、かつDBで有効なものだけ（`available_ids()`）。
"""

import functools
import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points

from django.db import transaction

from .errors import PluginUnavailable
from .interface import check_plugin
from .models import Plugin, PluginExtension
from .spec import ENTRY_POINT_GROUP, Extension, ExtensionType, PluginSpec

logger = logging.getLogger(__name__)


@dataclass
class LoadedPlugin:
    plugin_id: str
    spec: PluginSpec | None
    builtin: bool
    problems: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.spec is not None and not self.problems


def _builtin_specs() -> list[PluginSpec]:
    from .builtin_plugins import plugins  # noqa: PLC0415  本体のPluginは各アプリのModelを読むので、使うときに読む

    return plugins()


@functools.cache
def loaded() -> dict[str, LoadedPlugin]:
    """このプロセスが読み込んだPlugin（plugin_idごと）。確かめに通らなかったものも、理由とともに入れる"""
    result: dict[str, LoadedPlugin] = {}
    for spec in _builtin_specs():
        result[spec.id] = LoadedPlugin(spec.id, spec, True, _check(spec, spec.id, builtin=True))
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        if ep.name in result:
            result[ep.name].problems.append(f"plugin id {ep.name!r} is declared more than once")
            continue
        try:
            spec = ep.load()
        except Exception as exc:  # 自社のPluginのimportの失敗で、本体を止めない
            logger.exception("plugin %s cannot be loaded", ep.name)
            result[ep.name] = LoadedPlugin(ep.name, None, False, [f"cannot be loaded: {type(exc).__name__}: {exc}"])
            continue
        problems = _check(spec, ep.name, builtin=False)
        result[ep.name] = LoadedPlugin(ep.name, spec if isinstance(spec, PluginSpec) else None, False, problems)
    # Adapterのidが別のPluginと重なれば、両方とも使わない
    owners: dict[str, list[str]] = {}
    for item in result.values():
        for extension in _declared_extensions(item.spec):
            owners.setdefault(extension.id, []).append(item.plugin_id)
    for extension_id, plugin_ids in owners.items():
        if len(plugin_ids) > 1:
            for plugin_id in plugin_ids:
                result[plugin_id].problems.append(f"extension id {extension_id!r} is declared by {plugin_ids}")
    for item in result.values():
        if item.problems:
            logger.warning("plugin %s is not usable: %s", item.plugin_id, "; ".join(item.problems))
    return result


def _declared_extensions(spec: PluginSpec | None) -> list[Extension]:
    """宣言のうち、Extensionの形でidが文字列のもの（壊れた宣言でも、重複の確かめと登録を止めない）"""
    extensions = spec.extensions if spec is not None and isinstance(spec.extensions, tuple | list) else ()
    return [e for e in extensions if isinstance(e, Extension) and isinstance(e.id, str) and e.id]


def _check(spec, entry_point: str, *, builtin: bool) -> list[str]:
    """宣言の確かめ。確かめの途中の例外（壊れた宣言）でも本体を止めず、理由にする"""
    try:
        return check_plugin(spec, entry_point=entry_point, builtin=builtin)
    except Exception as exc:
        logger.exception("plugin %s cannot be checked", entry_point)
        return [f"cannot be checked: {type(exc).__name__}"]


def extensions() -> dict[str, tuple[Extension, PluginSpec]]:
    """確かめに通ったPluginのAdapter（extension_idごと）"""
    return {
        extension.id: (extension, item.spec)
        for item in loaded().values()
        if item.valid and item.spec is not None
        for extension in item.spec.extensions
    }


def available_ids(extension_type: ExtensionType | None = None, *, for_new: bool = False) -> set[str]:
    """使えるAdapterのid：読み込めて確かめに通り、DBで有効なもの。`for_new` なら、新しく作るものの選択肢
    （deprecatedなPluginのものを除く）"""
    rows = PluginExtension.objects.filter(
        deployed=True, plugin__enabled=True, plugin__deployed=True, plugin__problem=""
    )
    if extension_type is not None:
        rows = rows.filter(extension_type=extension_type)
    if for_new:
        rows = rows.filter(plugin__deprecated="")
    code = extensions()
    return {extension_id for extension_id in rows.values_list("extension_id", flat=True) if extension_id in code}


def is_available(extension_id: str) -> bool:
    return extension_id in available_ids()


def get(extension_id: str) -> tuple[Extension, PluginSpec]:
    """使えるAdapter。停止中・配備されていない・確かめに通らなかったものは PluginUnavailable"""
    found = extensions().get(extension_id)
    if found is None or not is_available(extension_id):
        raise PluginUnavailable(extension_id)
    return found


def declared(extension_id: str) -> tuple[Extension, PluginSpec] | None:
    """有効かどうかによらず、このプロセスが読み込んだAdapter（画面の表示名・既定値等に使う）"""
    return extensions().get(extension_id)


def versions(extension_ids) -> dict[str, str]:
    """AdapterのidとPluginの版（RunのManifestに固定する。design.md §3.11.1）"""
    code = extensions()
    return {i: code[i][1].version for i in sorted(set(extension_ids)) if i in code}


@transaction.atomic
def sync_plugins() -> None:
    """読み込んだPluginをDBへ登録・更新する。初めて見つかったものは、本体のものは有効、それ以外は停止。
    配備から消えたものは「配備されていない」にする"""
    seen: set[str] = set()
    for item in loaded().values():
        seen.add(item.plugin_id)
        spec = item.spec
        defaults = {
            "name": (spec.name if spec else item.plugin_id)[:200],
            "description": spec.description if spec else "",
            "version": (spec.version if spec else "")[:100],
            "builtin": item.builtin,
            "deployed": True,
            "problem": "\n".join(item.problems),
            "deprecated": spec.deprecated if spec else "",
            "replaced_by": spec.replaced_by if spec else "",
            "has_self_check": bool(spec and spec.self_check),
        }
        plugin, created = Plugin.objects.select_for_update().get_or_create(
            plugin_id=item.plugin_id, defaults={**defaults, "enabled": item.builtin}
        )
        if not created:
            for name, value in defaults.items():
                setattr(plugin, name, value)
            plugin.save()
        current: set[str] = set()
        for extension in _declared_extensions(spec):
            current.add(extension.id)
            other = PluginExtension.objects.filter(extension_id=extension.id).exclude(plugin=plugin).first()
            if other is not None:
                # 別のPluginが前に宣言していたid。行は付け替えない（指しているものが別のPluginのものになるため）
                continue
            PluginExtension.objects.update_or_create(
                extension_id=extension.id,
                defaults={
                    "plugin": plugin,
                    "extension_type": str(extension.type),
                    "name": extension.name[:200],
                    "capabilities": sorted(extension.capabilities),
                    "config_schema": extension.config_schema,
                    "deployed": True,
                },
            )
        plugin.extensions.exclude(extension_id__in=current).update(deployed=False)
    Plugin.objects.exclude(plugin_id__in=seen).update(deployed=False)


def has_capability(extension_id: str, capability: str) -> bool:
    found = declared(extension_id)
    return bool(found and capability in found[0].capabilities)


def config_default(extension_id: str, key: str, fallback=None):
    """Adapterの設定のSchemaの既定値（例：接続先のポート）"""
    found = declared(extension_id)
    if found is None:
        return fallback
    return found[0].config_schema.get("properties", {}).get(key, {}).get("default", fallback)


def display_name(extension_id: str) -> str:
    """画面に出す名前。読み込んでいなければDBの名前、それもなければid"""
    found = declared(extension_id)
    if found is not None:
        return found[0].name
    row = PluginExtension.objects.filter(extension_id=extension_id).only("name").first()
    return row.name if row else extension_id
