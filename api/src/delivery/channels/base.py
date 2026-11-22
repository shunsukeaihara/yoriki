"""対話の連携（Channel Plugin）のAdapterの契約（design.md §3.11・§4.6・§4.7。書き方は plugin/CONTRACT.md）。

Adapterは、チャットツールのAPIとの通信と、メッセージの見せ方（ボタン等）だけを持つ。スレッドとRunの対応、回答の確定、
配信の再試行・記録は共通の処理（delivery/channels/outbound.py・inbound.py）が持つ。資格情報（`secrets`）は接続
（ChannelConnection）が暗号化して持ち、呼ぶたびに渡す。

送信の結果は通知のAdapterと同じ `Outcome`（受付確認・一時的な失敗・失敗・届いたか分からない）で返す。投稿の結果が
分からない（送った後に応答が失われた等）ときは「届いたか分からない」にする。共通の処理は自動では送り直さない。

HTTPでは受けられない出来事（DiscordのGatewayの返信）は、capability `listen` を宣言し、`listen`・`fetch_replies` を
実装する。channel-listener（delivery/channels/listener.py）が接続ごとに担当を決めて `listen` を呼ぶ。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from ..adapters import Outcome


@dataclass(frozen=True)
class ConnectionInfo:
    """接続を確かめた結果（Slackでは `auth.test`）。受信の照合とbot自身の投稿の除外に使う"""

    workspace_id: str
    workspace_name: str
    bot_user_id: str


@dataclass(frozen=True)
class ChannelInfo:
    """チャネルを確かめた結果。`name` は選ぶための表示名で、実行時はIDを使う"""

    channel_id: str
    name: str


@dataclass(frozen=True)
class ChannelChoice:
    """出口の登録の画面で選ぶチャネル（capability list_channels）。登録では、選んだIDを `check_channel` で確かめ直す"""

    channel_id: str
    name: str
    # 選べない理由（botが参加していない等）。空なら選べる
    unavailable: str = ""


@dataclass(frozen=True)
class ChannelList:
    channels: list[ChannelChoice]
    # 上限で読むのをやめた（一覧に出ないチャネルはIDで指定する）
    truncated: bool = False


@dataclass(frozen=True)
class QuestionAnswer:
    """確定した回答（質問のメッセージを書き換えるときに見せる）"""

    # 回答者の表示名（SlackのユーザーかYorikiのユーザー）
    name: str
    # 回答した場所（"slack"・"web" 等。Adapterが見せ方を決める）
    via: str
    # 質問ごとの選んだラベル（質問と同じ順）
    selected: list[list[str]]
    # 自由記述（スレッドへの返信の文、Webの補足）
    text: str = ""


@dataclass(frozen=True)
class QuestionView:
    """ask_user の質問。ボタンの値には確認のIDと、質問・選択肢の番号を入れる"""

    clarification: str
    # [{"header", "question", "options": [{"label", "description"}], "multi_select"}]
    questions: list[dict[str, Any]]
    # 今選んでいるラベル（質問と同じ順。まだ確定していない）
    selections: list[list[str]] = field(default_factory=list)
    # 確定した回答。あればボタンを出さない
    answer: QuestionAnswer | None = None
    # 回答のしかたを閉じた理由（期限切れ・Runの終了等）。あればボタンを出さない
    closed: str = ""

    @property
    def confirms_on_select(self) -> bool:
        """1問で単一選択なら、ボタンを押すとその選択で確定する（「この内容で回答」のボタンを出さない）"""
        return len(self.questions) == 1 and not self.questions[0].get("multi_select")


class MessageKind(StrEnum):
    PARENT = "parent"  # スレッドの親メッセージ（タスクの名前とRunの画面へのリンク）
    QUESTION = "question"
    REPORT = "report"  # 完了・失敗・停止の報告
    NOTICE = "notice"  # 受け付けなかった返信への知らせ等


@dataclass(frozen=True)
class ThreadMessage:
    """スレッドへ投稿するメッセージ。文はYorikiが作り（利用者に見せてよい内容だけ）、見せ方はAdapterが決める"""

    kind: MessageKind
    # 本文（プレーンテキスト。Adapterがエスケープする）
    text: str
    # 見出し（親メッセージのタスクの名前、報告の状態等）
    title: str = ""
    # Runの画面へのリンク（社内の認証が要る）。なければ空
    link: str = ""
    question: QuestionView | None = None
    # 親メッセージに「停止」のボタンを出す（Runが動いている間だけ。design.md §4.6）。押されたら、確かめてから
    # InboundKind.STOP の出来事にする（Slackは確認のダイアログ等。確かめのしかたはAdapterが決める）
    stoppable: bool = False


class InboundKind(StrEnum):
    REPLY = "reply"  # スレッドへの普通の返信
    SELECT = "select"  # 選択肢のボタン
    SUBMIT = "submit"  # 「この内容で回答」のボタン
    STOP = "stop"  # 親メッセージの「停止」のボタン（確かめた後のもの）


@dataclass(frozen=True)
class InboundEvent:
    """受信した1つの出来事。照合（接続・Workspace・チャネル・スレッド）と適用は共通の処理が行う"""

    kind: InboundKind
    # 重複の判定のキー（Slackでは Events API の event_id、ボタンでは action_ts と投稿者）
    dedup_key: str
    workspace_id: str
    channel_id: str
    # スレッドの親メッセージの参照（Slackでは thread_ts。停止のボタンでは押した親メッセージ）
    thread_ref: str
    # この出来事のメッセージの参照（返信のts、ボタンを押したメッセージのts）
    message_ref: str
    user_id: str
    user_name: str = ""
    # botの投稿か（bot自身の投稿は、接続のbotのuser IDでも除く）
    is_bot: bool = False
    # 返信の文
    text: str = ""
    # ボタンの確認のIDと、質問・選択肢の番号
    clarification: str = ""
    question: int = -1
    option: int = -1
    # 受け付けなかったときに、本人だけへ知らせるのに使う値（Discordのinteractionのtoken等）。処理の後に消す
    reply_token: str = ""


@dataclass
class InboundRequest:
    """受信した要求を読んだ結果。`response` はその場で返す本文（SlackのURLの確認の challenge 等）"""

    events: list[InboundEvent] = field(default_factory=list)
    response: dict[str, Any] | None = None


class ChannelAdapter(ABC):
    """対話の連携のAdapter（拡張種別 channel）。契約の例外は plugin/errors.py の3つの型（接続・チャネルの確かめは
    PluginConfigError で理由を返す）"""

    @abstractmethod
    def check_connection(self, secrets: dict[str, str]) -> ConnectionInfo:
        """接続（資格情報）を確かめる。作るときと資格情報を変えるときに呼ぶ"""

    @abstractmethod
    def check_channel(self, secrets: dict[str, str], channel_id: str) -> ChannelInfo:
        """botがそのチャネルに参加していて投稿できるかを確かめる。対話の出口を登録・変更するときに呼ぶ"""

    def list_channels(self, secrets: dict[str, str]) -> ChannelList:
        """出口に登録できそうなチャネルの一覧（capability list_channels を宣言するものだけ）。名前の順。取れない
        ときは確かめと同じく PluginConfigError・PluginTemporaryError"""
        raise NotImplementedError

    @abstractmethod
    def post(
        self, secrets: dict[str, str], channel_id: str, message: ThreadMessage, *, thread_ref: str = ""
    ) -> Outcome:
        """投稿する。`thread_ref` が空なら新しいメッセージ（スレッドの親）にする。受け付けられたら
        `Outcome.message_ref` に投稿したメッセージの参照を入れる"""

    @abstractmethod
    def update(self, secrets: dict[str, str], channel_id: str, message_ref: str, message: ThreadMessage) -> Outcome:
        """投稿したメッセージを書き換える（質問のボタンの選んだ状態、確定した回答）"""

    @abstractmethod
    def notify_user(
        self,
        secrets: dict[str, str],
        channel_id: str,
        thread_ref: str,
        user_id: str,
        text: str,
        *,
        reply_token: str = "",
    ) -> Outcome:
        """その人だけに見える知らせ（受け付けなかった返信・ボタン）。できなければスレッドへの投稿でよい。
        `reply_token` は、受信のときに読んだ値（InboundEvent.reply_token）"""

    @abstractmethod
    def verify(self, secrets: dict[str, str], headers: dict[str, str], body: bytes) -> bool:
        """受信した要求の認証（署名・時刻）を確かめる"""

    @abstractmethod
    def parse(self, headers: dict[str, str], body: bytes) -> InboundRequest:
        """認証を確かめた要求を読む。形が合わなければ PluginPermanentError"""

    def user_name(self, secrets: dict[str, str], user_id: str) -> str:
        """表示名（任意）。分からなければ空"""
        return ""

    def thread_url(self, secrets: dict[str, str], workspace_id: str, channel_id: str, thread_ref: str) -> str:
        """スレッドを開くURL（任意）。Runの画面のリンクに使う。分からなければ空"""
        return ""

    # --- 常時接続で受ける（capability listen を宣言するものだけ） ---

    async def listen(self, secrets: dict[str, str], sink: "ListenSink") -> None:
        """つないで受け続ける（取り消されるまで返さない）。資格情報・設定の誤りは PluginConfigError、それ以外の
        例外では、channel-listenerが少し待ってつなぎ直す"""
        raise NotImplementedError

    def fetch_replies(
        self, secrets: dict[str, str], channel_id: str, thread_ref: str, after_ref: str
    ) -> list[InboundEvent]:
        """スレッドの返信のうち、`after_ref`（メッセージの参照）より後のもの（古い順）。つなぎ直した後の読み直しに使う"""
        raise NotImplementedError


class ListenSink(Protocol):
    """`listen` が受けたものを渡す先（channel-listener）"""

    async def events(self, events: list[InboundEvent]) -> None:
        """受けた出来事を記録する"""

    async def connected(self, resumed: bool) -> None:
        """つないだ。新しいセッション（`resumed=False`）なら、確認待ちのスレッドを読み直す"""
