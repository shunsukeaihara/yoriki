"""外部へのHTTPの要求の補助"""

from collections.abc import Callable

import httpx


class PinnedTransport(httpx.BaseTransport):
    """要求ごとに（リダイレクトの先も）接続先を確かめ、確かめたIPアドレスに接続する。

    名前解決の確認と接続の間にDNSの応答が内部のアドレスへ変わっても（DNS rebinding）、そこへはつながない。
    URLのホストはIPアドレスに替えるが、Hostヘッダーと、TLSのSNI・証明書の確認は元のホスト名で行う
    """

    def __init__(self, inner: httpx.BaseTransport, choose: Callable[[httpx.Request], str | None]):
        self.inner = inner
        self.choose = choose

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        address = self.choose(request)
        if address is None:
            return self.inner.handle_request(request)
        host = request.url.host
        pinned = request.url.copy_with(host=address)
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode()
        forwarded = httpx.Request(
            request.method,
            pinned,
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        return self.inner.handle_request(forwarded)

    def close(self) -> None:
        self.inner.close()
