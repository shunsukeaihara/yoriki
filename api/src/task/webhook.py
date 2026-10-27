"""Webhookの入口のpayloadの受け取り方（design.md §4.2）。

payloadの形は送り元ごとに違うので、送り元ごとの処理は作らない。受け取ったpayloadはいつもそのままファイルとして
Runの作業場所に置き（取り出さなかった項目の読み方はSkillに書く）、入口の版で指定した項目だけをJSONのpathで取り出して
タスクの入力の値にする。型と必須かどうかはタスクの版の入力の項目で決め、合わない要求はRunを作らずに400で断る。

送る形を決められる送り元（Zapier等）向けの推奨の形式（`event_id`、`message`、`inputs`、`files`）で送れば、
重複のキーと項目の取り出しは入口の設定なしで行う（`files` のファイルの取り込みはまだない）。`message` は他の値と
同じくデータとしてpayloadのファイルに残すだけで、依頼文としては扱わない（入口は自由な依頼文を受けない。
authorization.md §6）。
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

from django.conf import settings
from django.utils.translation import gettext as _

from .models import TaskInputType

MAX_DEDUP_KEY_LENGTH = 200
# JSONのpathの1つの区切り：`.名前`、`["名前"]`・`['名前']`、`[番号]`
PATH_TOKEN_RE = re.compile(r"""\.([^.\[\]"'\s]+)|\[(\d+)\]|\["((?:[^"\\]|\\.)*)"\]|\['((?:[^'\\]|\\.)*)'\]""")
MAX_PATH_DEPTH = 20


class PayloadError(Exception):
    """要求を受け付けない（400・415）。`code` は送り元に返す決まった文字列"""

    def __init__(self, code: str, detail: str, *, status: int = 400, errors: dict[str, str] | None = None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.errors = errors or {}


def parse_path(path: str) -> list[str | int]:
    """`$.data.items[0]["vendor name"]` の形のpathを区切りの並びにする。形が違えば ValueError"""
    if not path.startswith("$"):
        raise ValueError(_("$ から始めてください（例：$.data.vendor）"))
    tokens: list[str | int] = []
    pos = 1
    while pos < len(path):
        match = PATH_TOKEN_RE.match(path, pos)
        if match is None:
            raise ValueError(_("%(pos)s文字目の書き方が正しくありません") % {"pos": pos + 1})
        name, index, double, single = match.groups()
        if index is not None:
            tokens.append(int(index))
        else:
            tokens.append(name if name is not None else _unquote(double, single))
        pos = match.end()
    if not tokens:
        raise ValueError(_("取り出す場所を書いてください（例：$.data.vendor）"))
    if len(tokens) > MAX_PATH_DEPTH:
        raise ValueError(_("深さは%(limit)sまでです") % {"limit": MAX_PATH_DEPTH})
    return tokens


def _unquote(double: str | None, single: str | None) -> str:
    """`["..."]` の中はJSONの文字列として読む（`\\u0020`・`\\"` 等）。`['...']` の中は `\\'`・`\\\\` だけを戻す"""
    if double is not None:
        try:
            return json.loads(f'"{double}"')
        except ValueError:
            raise ValueError(_("引用符の中の書き方が正しくありません")) from None
    assert single is not None
    return re.sub(r"\\(.)", r"\1", single)


def extract(data: Any, path: str) -> tuple[bool, Any]:
    """pathの値。（見つかったか、値）。値がnullなら見つからなかったものとする"""
    value = data
    for token in parse_path(path):
        if isinstance(value, list) and isinstance(token, int):
            if token >= len(value):
                return False, None
            value = value[token]
        elif isinstance(value, dict) and isinstance(token, str):
            if token not in value:
                return False, None
            value = value[token]
        else:
            return False, None
    return value is not None, value


@dataclass
class Payload:
    """受け取ったpayload。`data` はJSON・フォームの値（本文のテキストでは None）。`file_*` は作業場所に置くファイル"""

    data: Any
    file_name: str
    file_bytes: bytes
    sha256: str
    content_type: str
    size: int = field(init=False)

    def __post_init__(self):
        self.size = len(self.file_bytes)


def _refuse_constant(name: str) -> Any:
    raise ValueError(name)  # NaN・Infinity は標準のJSONではない


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(text)  # 1e999 等。保存するファイルも標準のJSONで読めるように断る
    return value


def _canonical(data: Any) -> bytes:
    """並び順と空白によらない形。対になっていないサロゲート（JSONの "\\ud800" 等）はUTF-8にできないので断る
    （DBの入力の値やファイルにも書けないため）"""
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    except UnicodeEncodeError:
        raise PayloadError("invalid_json", _("本文に文字として正しくない値があります")) from None


def parse_payload(body: bytes, content_type: str) -> Payload:
    """本文を形式ごとに読む。JSON・フォームの値・本文のテキストを受け付ける（ファイルの取り込みはまだない）。

    重複の判定の「同じ入力」には、JSONとフォームの値は並び順と空白によらない形のハッシュを使う"""
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "application/json" or media_type.endswith("+json"):
        try:
            data = json.loads(body.decode("utf-8"), parse_constant=_refuse_constant, parse_float=_finite_float)
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise PayloadError("invalid_json", _("本文をJSONとして読めません（UTF-8のJSONを送ってください）")) from None
        return Payload(
            data=data,
            file_name="webhook_payload.json",
            file_bytes=body,
            sha256=hashlib.sha256(_canonical(data)).hexdigest(),
            content_type="application/json",
        )
    if media_type == "application/x-www-form-urlencoded":
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise PayloadError("invalid_form", _("本文をフォームの値として読めません")) from None
        # 短い項目を大量に詰めた本文で資源を使わせないよう、項目数をDjangoのフォームと同じ上限に限る
        limit = settings.DATA_UPLOAD_MAX_NUMBER_FIELDS
        try:
            # 不正なUTF-8を置換文字にすると、違う値が同じになる（重複のキーが重なる）ので断る
            parsed = parse_qs(text, keep_blank_values=True, max_num_fields=limit, errors="strict")
        except UnicodeDecodeError:
            raise PayloadError("invalid_form", _("本文をフォームの値として読めません")) from None
        except ValueError:
            raise PayloadError("too_many_fields", _("フォームの項目は%(limit)s個までです") % {"limit": limit}) from None
        # 同じ名前が1つなら値、複数なら並びにする。Skillが読みやすいよう、ファイルはJSONにして置く
        data = {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}
        canonical = _canonical(data)
        return Payload(
            data=data,
            file_name="webhook_payload.json",
            file_bytes=json.dumps(data, ensure_ascii=False, indent=2).encode(),
            sha256=hashlib.sha256(canonical).hexdigest(),
            content_type=media_type,
        )
    if media_type == "text/plain":
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            raise PayloadError("invalid_text", _("本文をUTF-8のテキストとして読めません")) from None
        return Payload(
            data=None,
            file_name="webhook_payload.txt",
            file_bytes=body,
            sha256=hashlib.sha256(body).hexdigest(),
            content_type=media_type,
        )
    raise PayloadError(
        "unsupported_media_type",
        _("JSON・フォームの値・テキストだけを受け付けます（Content-Type を確かめてください）"),
        status=415,
    )


def dedup_key(payload: Payload, event_id_path: str, idempotency_key: str | None) -> str | None:
    """重複の判定のキー（design.md §4.2）。入口で指定した送り元のイベントID、`Idempotency-Key` ヘッダー、推奨の形式の
    `event_id` の順に使う（推奨の形式は、入口でイベントIDの場所を指定していないときだけ）。どれもなければ None。
    イベントIDと `Idempotency-Key` は出所の違う値なので、`event:`・`idempotency:` を付けて分ける（イベントID「x」と
    別の要求の `Idempotency-Key`「x」を同じ要求とみなさない）"""
    if event_id_path and payload.data is not None:
        found, value = extract(payload.data, event_id_path)
        if found:
            return "event:" + _key_text(value, _("イベントID"))
    if idempotency_key is not None and idempotency_key.strip():
        return "idempotency:" + _key_text(idempotency_key.strip(), "Idempotency-Key")
    if not event_id_path and isinstance(payload.data, dict) and payload.data.get("event_id") is not None:
        return "event:" + _key_text(payload.data["event_id"], "event_id")
    return None


def _key_text(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise PayloadError("invalid_event_id", _("%(label)sは文字か整数で送ってください") % {"label": label})
    text = str(value)
    if not text or len(text) > MAX_DEDUP_KEY_LENGTH:
        raise PayloadError(
            "invalid_event_id",
            _("%(label)sは1〜%(limit)s文字にしてください") % {"label": label, "limit": MAX_DEDUP_KEY_LENGTH},
        )
    return text


def input_values(payload: Payload, mappings: list[dict[str, str]], definitions: list[dict[str, Any]]) -> dict[str, Any]:
    """タスクの入力の値を取り出す。入口で項目の取り出しを指定していれば、そのpathの値（タスクの版にない項目は使わない）。
    指定していなければ推奨の形式の `inputs`。確かめるのは呼び出し元（task.inputs.clean_inputs()）"""
    types = {d["name"]: d["type"] for d in definitions}
    values: dict[str, Any] = {}
    if mappings:
        if payload.data is None:
            return values
        for mapping in mappings:
            if mapping["name"] not in types:
                continue
            found, value = extract(payload.data, mapping["path"])
            if found:
                values[mapping["name"]] = value
    elif isinstance(payload.data, dict) and isinstance(payload.data.get("inputs"), dict):
        values = dict(payload.data["inputs"])
    return {name: _coerce(types.get(name), value) for name, value in values.items()}


def _coerce(input_type: str | None, value: Any) -> Any:
    """送り元の型の揺れを吸収する。フォームの値はいつも文字で、Zapier等も数値を文字で送ることがあるため。
    数値・整数・真偽の項目に来た文字は読み替え、文字の項目に来た数値は文字にする。読めなければそのまま返し、
    型の確かめ（clean_inputs()）で断る"""
    if input_type == TaskInputType.STRING and isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    if not isinstance(value, str):
        return value
    text = value.strip()
    if input_type in (TaskInputType.INTEGER, TaskInputType.NUMBER) and re.fullmatch(r"[+-]?\d{1,20}", text):
        return int(text)
    if input_type == TaskInputType.NUMBER and re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", text):
        return float(text)
    if input_type == TaskInputType.BOOLEAN and text.lower() in ("true", "false"):
        return text.lower() == "true"
    return value
