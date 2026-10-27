"""タスクの入力の項目（authorization.md §5.4の入力の制限、design.md §4.2）。

入力は、タスクの定義で型と取りうる値を決めた項目だけを受け取る（自由な依頼文は受けない。authorization.md §6）。
値はプロンプトのテンプレートの `{{ inputs.名前 }}` に文字として埋め込むだけで、式やコードとして評価しない。
型がファイルの項目は、受付時にファイルを /workspace/input に置き、値をそのパスにする（task/dispatch.py）。
"""

import datetime
import hashlib
import io
import math
from dataclasses import dataclass
from functools import cached_property
from typing import IO, Any

from django.utils.translation import gettext as _

from harness.untrusted import untrusted

from .checks import PLACEHOLDER_RE
from .models import TaskInputType

DEFAULT_MAX_LENGTH = 1000
# 数値の入力の絶対値の上限
MAX_NUMBER = 10**15


@dataclass(eq=False)
class InputFile:
    """送られたファイル（手動実行・Webhookのmultipart）。`name` は送り元が付けた名前。中身は `content`（Webhook。
    本文は入口の上限までなのでメモリに持つ）か `upload`（手動実行。大きなファイルは一時ファイルのまま保存先へ流す）。
    `path` は /workspace/input の中の名前（Webhookでpayloadのファイルに書くために、受付時に先に決める）"""

    name: str
    content: bytes = b""
    upload: IO[bytes] | None = None
    path: str = ""

    @cached_property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def open(self) -> IO[bytes]:
        if self.upload is not None:
            self.upload.seek(0)
            return self.upload
        return io.BytesIO(self.content)

    def describe(self, *, with_path: bool = True) -> dict[str, Any]:
        """payloadのファイル（JSON）に書く形。重複の判定では、パートの順番で変わるパスを除く"""
        data: dict[str, Any] = {"file": self.name, "size": len(self.content), "sha256": self.sha256}
        if with_path:
            data["path"] = f"/workspace/input/{self.path}"
        return data


def clean_inputs(definitions: list[dict[str, Any]], values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """送られた値を項目の定義で確かめる。（確かめた値、項目ごとの誤り）。定義にない項目は誤りにする"""
    cleaned: dict[str, Any] = {}
    errors: dict[str, str] = {}
    names = {d["name"] for d in definitions}
    for name in values:
        if name not in names:
            errors[name] = _("この項目はありません")
    for definition in definitions:
        name = definition["name"]
        value = values.get(name)
        if value is None or value == "":
            if definition.get("required"):
                errors[name] = _("入力してください")
            continue
        try:
            cleaned[name] = _clean(definition, value)
        except ValueError as exc:
            errors[name] = str(exc)
    return cleaned, errors


def _clean_string(definition: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, str):
        raise ValueError(_("文字を入力してください"))
    limit = definition.get("max_length") or DEFAULT_MAX_LENGTH
    if len(value) > limit:
        raise ValueError(_("%(limit)s文字以内にしてください") % {"limit": limit})
    return value


def _clean_number(definition: dict[str, Any], value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(_("数値を入力してください"))
    return _in_range(value)


def _clean_integer(definition: dict[str, Any], value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(_("整数を入力してください"))
    return _in_range(value)


def _in_range(value: int | float) -> int | float:
    """無限大・NaN（JSONの 1e999 等）と、桁の多すぎる数を断る（整数は浮動小数点数へ変換せずに比べる）"""
    if (isinstance(value, float) and not math.isfinite(value)) or abs(value) > MAX_NUMBER:
        raise ValueError(_("%(limit)s以下の数値を入力してください") % {"limit": f"{MAX_NUMBER:.0e}"})
    return value


def _clean_boolean(definition: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, bool):
        raise ValueError(_("true か false を入力してください"))
    return value


def _clean_date(definition: dict[str, Any], value: Any) -> Any:
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError(_("日付をYYYY-MM-DDの形で入力してください")) from None


def _clean_choice(definition: dict[str, Any], value: Any) -> Any:
    if value not in definition.get("choices", []):
        raise ValueError(_("選択肢から選んでください"))
    return value


def _clean_file(definition: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, InputFile):
        raise ValueError(_("ファイルを送ってください"))
    return value


CLEANERS = {
    TaskInputType.STRING: _clean_string,
    TaskInputType.NUMBER: _clean_number,
    TaskInputType.INTEGER: _clean_integer,
    TaskInputType.BOOLEAN: _clean_boolean,
    TaskInputType.DATE: _clean_date,
    TaskInputType.CHOICE: _clean_choice,
    TaskInputType.FILE: _clean_file,
}


def _clean(definition: dict[str, Any], value: Any) -> Any:
    cleaner = CLEANERS.get(definition["type"])
    if cleaner is None:
        raise ValueError(_("この項目の型は扱えません"))
    return cleaner(definition, value)


def text_of(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render(template: str, values: dict[str, Any]) -> str:
    """プロンプトのテンプレートに入力の値を埋め込む。値は利用者の指示ではないデータなので、信頼しない内容として
    囲んで埋め込む（harness.md §6.3）。値のない項目は空にする"""

    def value(match) -> str:
        name = match.group(1)
        return untrusted(text_of(values[name]), f"input:{name}") if name in values else ""

    return PLACEHOLDER_RE.sub(value, template)
