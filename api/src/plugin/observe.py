"""実行時の観察（design.md §3.11.4）。Yorikiが Adapterを呼ぶ境界を `observed()` で囲み、契約にない例外（予期しない
失敗）と時間切れを、AdapterとPluginの版、日ごとに数える。例外はそのまま投げ直す（扱いは呼ぶ側が決める）"""

import logging
import traceback
from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection
from django.db.models import F
from django.utils import timezone

from .errors import PluginError
from .models import PluginExtension, PluginFailure
from .registry import declared

logger = logging.getLogger(__name__)


@contextmanager
def observed(extension_id: str, *, expected: tuple[type[BaseException], ...] = ()) -> Iterator[None]:
    """`expected` は、契約の例外のほかに、その呼出しで起こりうると決めている例外"""
    try:
        yield
    except PluginError as exc:
        if exc.is_timeout:
            record(extension_id, exc, timeout=True)
        raise
    except expected:
        raise
    except Exception as exc:
        record(extension_id, exc, timeout=False)
        raise


def describe(exc: BaseException) -> str:
    """記録と画面に出す失敗の説明。契約の例外は利用者に見せてよい文なので本文も、それ以外は本文にSecret（接続文字列
    等）が入りうるので、例外の型と起きた場所（最後のフレームのモジュールと行）だけにする"""
    if isinstance(exc, PluginError):
        return f"{type(exc).__name__}: {exc}"[:500]
    frames = traceback.extract_tb(exc.__traceback__)
    where = f" ({frames[-1].filename.rsplit('/', 1)[-1]}:{frames[-1].lineno})" if frames else ""
    return f"{type(exc).__module__}.{type(exc).__qualname__}{where}"[:500]


def record(extension_id: str, exc: BaseException, *, timeout: bool) -> None:
    found = declared(extension_id)
    version = found[1].version if found else ""
    error = describe(exc)
    if connection.in_atomic_block and connection.needs_rollback:
        # 壊れたtransactionの中では書けない。ログだけに残す
        logger.warning("plugin %s failure not recorded: %s", extension_id, error)
        return
    try:
        extension = PluginExtension.objects.filter(extension_id=extension_id).first()
        if extension is None:
            return
        now = timezone.now()
        row, _created = PluginFailure.objects.get_or_create(
            extension=extension, plugin_version=version[:100], day=timezone.localdate(now)
        )
        counter = "timeouts" if timeout else "unexpected"
        PluginFailure.objects.filter(pk=row.pk).update(**{counter: F(counter) + 1}, last_error=error, last_at=now)
    except Exception:
        logger.exception("plugin %s failure not recorded", extension_id)
