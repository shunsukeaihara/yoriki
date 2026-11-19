"""本体のPluginのIDと対応能力の名前（design.md §3.11.1）。

各アプリのModelが使うので、ここには定数だけを置く（宣言は plugin/builtin_plugins.py）。
"""

# 対応能力（種別ごとの意味は CONTRACT.md）
# 出口：宛先のURLを持つ（外へ送る）
URL = "url"
# 出口：個人のタスクの出口に使える（Yorikiが作る共通の画面の通知）
PERSONAL = "personal"
# 出口：渡したファイルの取得に、出口の署名の鍵を要る
FILE_FETCH_AUTH = "file_fetch_auth"
# 入口：新しいRunを起動する
START = "start"
# Connector：表の名前にスキーマがある（MySQLはDB名がスキーマなので持たない）
SCHEMAS = "schemas"
# 対話の出口：常時接続で受ける（channel-listenerが listen を呼ぶ）
LISTEN = "listen"

WEBHOOK_TRIGGER = "yoriki.trigger.webhook"
WEBHOOK = "yoriki.notification.webhook"
ZAPIER = "yoriki.notification.zapier"
IN_APP = "yoriki.notification.in_app"
LOCAL_STORAGE = "yoriki.storage.local"
S3_STORAGE = "yoriki.storage.s3"
POSTGRESQL = "yoriki.connector.postgresql"
MYSQL = "yoriki.connector.mysql"
SQLSERVER = "yoriki.connector.sqlserver"
SLACK = "yoriki.channel.slack"
DISCORD = "yoriki.channel.discord"
