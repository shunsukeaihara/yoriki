"""送り先のURLとアドレスの確認（SSRF対策。design.md §4.4）。

送り先はhttpsだけ（開発時（DEBUG）はhttpも）。名前を引いた先がプライベート・ループバック・リンクローカル等の
アドレスなら断る。社内のシステムへ送りたい導入先のために、基盤管理者が `YORIKI_DELIVERY_ALLOWED_NETWORKS` で許す
ネットワークを指定できる。送るときは、確かめたアドレスへそのまま接続する（名前を引き直して別のアドレスへ向けられ
ないように）。リダイレクトは辿らない。
"""

import ipaddress
import socket
from urllib.parse import SplitResult, urlsplit

from django.conf import settings
from django.utils.translation import gettext as _

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class BlockedAddress(Exception):
    """送ってはいけない宛先（理由は利用者に見せてよい文）"""


class ResolutionFailed(BlockedAddress):
    """名前を引けなかった。登録では誤りにし、送るときは一時的な障害として送り直す（DNSの一時的な障害がある）"""


def allowed_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    return [ipaddress.ip_network(n, strict=False) for n in settings.YORIKI_DELIVERY_ALLOWED_NETWORKS]


def check_url(url: str) -> SplitResult:
    """URLの形を確かめる（名前は引かない）"""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise BlockedAddress(_("送り先のURLの形が正しくありません")) from exc
    schemes = ("https", "http") if settings.DEBUG else ("https",)
    if parts.scheme not in schemes:
        raise BlockedAddress(_("送り先のURLは https:// で始めてください"))
    if not parts.hostname:
        raise BlockedAddress(_("送り先のURLにホスト名がありません"))
    if parts.username or parts.password:
        raise BlockedAddress(_("送り先のURLに利用者名・パスワードを含めないでください"))
    try:
        parts.port  # noqa: B018  範囲の外のポートは ValueError
    except ValueError as exc:
        raise BlockedAddress(_("送り先のURLのポートが正しくありません")) from exc
    return parts


def is_public(address: IPAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def resolve(parts: SplitResult) -> IPAddress:
    """名前を引き、送ってよいアドレスを1つ返す。引いた先に1つでも送れないアドレスがあれば断る
    （名前を引くたびに違うアドレスを返す送り先で、確かめた後に別の先へ向けられないように）"""
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ResolutionFailed(_("送り先のホスト名を解決できません")) from exc
    except UnicodeError as exc:
        raise BlockedAddress(_("送り先のホスト名を解決できません")) from exc
    addresses = [ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]) for info in infos]
    if not addresses:
        raise ResolutionFailed(_("送り先のホスト名を解決できません"))
    networks = allowed_networks()
    for address in addresses:
        if not is_public(address) and not any(address in network for network in networks):
            raise BlockedAddress(_("送り先が社内のアドレスです。基盤管理者が許したネットワークだけに送れます"))
    return addresses[0]


def check_destination(url: str) -> SplitResult:
    """登録するときの確認（形と、名前を引いた先）"""
    parts = check_url(url)
    resolve(parts)
    return parts
