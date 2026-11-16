"""インターフェースの形の確かめ（design.md §3.11.2）。

拡張種別ごとのインターフェースは、本体の基底クラス（ABC）とその型ヒントで表す。登録の時に、基底クラスを継承しているか、
抽象メソッドが残っていないか、Yorikiが呼ぶ形の引数を受け取れるか（`inspect.signature`）だけを確かめる。型ヒントどうしの
比較や、実行時の引数・戻り値の型の確かめはしない。契約の版の番号は持たず、破壊的な変更は型に表れる形で行う。
"""

import inspect
from typing import Any

from django.utils.module_loading import import_string

from .spec import BUILTIN_PREFIX, Extension, ExtensionType, PluginSpec

# 種別ごとの基底クラス（読み込みの順の都合で、使うときにimportする）
BASES: dict[ExtensionType, str] = {
    ExtensionType.TRIGGER: "task.triggers.TriggerAdapter",
    ExtensionType.NOTIFICATION: "delivery.adapters.NotificationAdapter",
    ExtensionType.STORAGE: "artifact.storage.StorageBackend",
    ExtensionType.CONNECTOR: "connector.adapters.base.DatasourceAdapter",
}


def base_class(extension_type: ExtensionType) -> type:
    return import_string(BASES[extension_type])


def check_plugin(spec: Any, *, entry_point: str, builtin: bool) -> list[str]:
    """Pluginの宣言の誤りと、各Adapterの形の誤り。なければ空"""
    if not isinstance(spec, PluginSpec):
        return [f"entry point {entry_point!r} does not point to a PluginSpec"]
    problems: list[str] = []
    if spec.id != entry_point:
        problems.append(f"plugin id {spec.id!r} differs from the entry point name {entry_point!r}")
    if spec.id.startswith(BUILTIN_PREFIX) != builtin:
        problems.append(f"only built-in plugins may use the {BUILTIN_PREFIX!r} namespace")
    if not spec.version:
        problems.append("version is empty")
    extensions = spec.extensions if isinstance(spec.extensions, tuple | list) else ()
    if not extensions:
        problems.append("no extensions")
    for extension in extensions:
        label = extension.id if isinstance(extension, Extension) else repr(extension)[:100]
        problems.extend(f"{label}: {p}" for p in check_extension(spec, extension))
    return problems


def check_extension(spec: PluginSpec, extension: Extension) -> list[str]:
    if not isinstance(extension, Extension):
        return ["not an Extension"]
    if not isinstance(extension.id, str):
        return ["extension id is not a string"]
    if extension.id != spec.id and not extension.id.startswith(spec.id + "."):
        return [f"extension id must start with the plugin id {spec.id!r}"]
    try:
        extension_type = ExtensionType(extension.type)
    except ValueError:
        return [f"unknown extension type {extension.type!r}"]
    # 設定をコンストラクタのキーワード引数で受け取るのは、settingsで設定する保存先だけ（ほかは決まった形で作る）
    return check_shape(
        extension.adapter, base_class(extension_type), config_kwargs=extension_type == ExtensionType.STORAGE
    )


def check_shape(adapter: Any, base: type, *, config_kwargs: bool = False) -> list[str]:
    """`adapter` が `base` の形に合うか。合わない理由の一覧。`config_kwargs` なら、コンストラクタで基底クラスにない
    キーワード専用の引数を設定とみなす（保存先の root・bucket 等）"""
    if not isinstance(adapter, type) or not issubclass(adapter, base):
        return [f"adapter does not inherit {base.__module__}.{base.__qualname__}"]
    if inspect.isabstract(adapter):
        names = ", ".join(sorted(getattr(adapter, "__abstractmethods__", ())))
        return [f"abstract methods are not implemented: {names}"]
    problems: list[str] = []
    for name in _interface_methods(base):
        expected, actual = getattr(base, name), getattr(adapter, name)
        if actual is expected:
            continue
        if not callable(actual):
            problems.append(f"{name} is not a method")
            continue
        if problem := _compare(expected, actual, constructor=config_kwargs and name == "__init__"):
            problems.append(f"{name}: {problem}")
    return problems


def _interface_methods(base: type) -> list[str]:
    """Yorikiが呼ぶメソッド：基底クラスの公開のメソッドと `__init__`（propertyや属性は見ない）"""
    names = []
    for name, member in inspect.getmembers(base):
        if name.startswith("_") and name != "__init__":
            continue
        if name == "__init__" and member is object.__init__:
            continue
        raw = inspect.getattr_static(base, name)
        if isinstance(raw, (staticmethod, classmethod)) or inspect.isfunction(raw):
            names.append(name)
    return names


class _Arg:
    pass


def _compare(expected: Any, actual: Any, *, constructor: bool = False) -> str:
    """基底クラスの引数の形で呼んで、実装が受け取れるか。コンストラクタは、実装が足したキーワード専用の引数を
    Adapterの設定（保存先のroot等。設定から渡す）とみなす"""
    try:
        base_sig, impl_sig = inspect.signature(expected), inspect.signature(actual)
    except (TypeError, ValueError):
        return ""
    positional: list[Any] = []
    keywords: dict[str, Any] = {}
    if constructor:
        keywords.update(
            {
                p.name: _Arg()
                for p in impl_sig.parameters.values()
                if p.kind is p.KEYWORD_ONLY and p.name not in base_sig.parameters
            }
        )
    for param in base_sig.parameters.values():
        if param.kind is param.POSITIONAL_ONLY:
            positional.append(_Arg())
        elif param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
            keywords[param.name] = _Arg()
    try:
        impl_sig.bind(*positional, **keywords)
    except TypeError as exc:
        return f"cannot be called as {base_sig} ({exc})"
    # 位置でも呼ぶ引数（POSITIONAL_OR_KEYWORD）は、位置でも受け取れること
    by_position = [p for p in base_sig.parameters.values() if p.kind is p.POSITIONAL_OR_KEYWORD]
    try:
        impl_sig.bind(
            *positional,
            *[_Arg() for _ in by_position],
            **{name: value for name, value in keywords.items() if name not in {p.name for p in by_position}},
        )
    except TypeError as exc:
        return f"cannot be called as {base_sig} ({exc})"
    return ""
