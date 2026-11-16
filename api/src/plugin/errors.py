"""Pluginの契約の例外（design.md §3.11.2）。

Adapterが投げてよい例外は、この3つの型（とその子）だけ。それ以外の例外は、Yorikiが呼ぶ境界で「予期しない失敗」として
数え（plugin/observe.py）、失敗にする（再試行しない）。メッセージは利用者に見せてよい内容だけにする。
"""


class PluginError(Exception):
    """契約の例外の共通の親"""

    # 時間切れか（実行時の観察で数える）
    is_timeout = False


class PluginConfigError(PluginError):
    """設定の誤り（接続先・資格情報・宛先等）。設定を直すまで成功しない"""


class PluginTemporaryError(PluginError):
    """一時的な障害。時間をおいて再試行してよい"""


class PluginTimeoutError(PluginTemporaryError):
    is_timeout = True


class PluginPermanentError(PluginError):
    """恒久的な失敗（相手が断った等）。同じ要求を再試行しない"""


class PluginUnavailable(Exception):
    """Pluginが停止中・配備されていない・登録の確かめに通らなかった"""

    def __init__(self, extension_id: str):
        super().__init__(extension_id)
        self.extension_id = extension_id
