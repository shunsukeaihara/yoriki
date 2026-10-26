from enum import StrEnum


class VersionConflict(Exception):
    """版を作る間に、他の人が先に新しい版を作った"""


class ConnectorErrorKind(StrEnum):
    """操作の失敗の種類。エージェントへの結果と記録に使う"""

    CONNECTION = "connection"  # 接続できない、認証に失敗した
    REJECTED = "rejected"  # 問い合わせを受け付けない（SELECT文でない、公開していない表等）
    TIMEOUT = "timeout"  # 実行時間の上限を超えた
    QUERY = "query"  # 接続先がエラーを返した（構文、列名の誤り等）
    UNAVAILABLE = "unavailable"  # ドライバ（ODBC Driver等）がない


class ConnectorError(Exception):
    """Connectorの操作の失敗。メッセージは利用者・エージェントに見せてよい内容だけにする（接続文字列やパスワードを含めない）"""

    def __init__(self, kind: ConnectorErrorKind, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message
