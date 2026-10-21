"""SSEのストリームのendpointをOpenAPIのスキーマに加える（realtime.md §4）。

ストリームはDjangoの非同期viewでDRFを通らないので、drf-spectacularの後処理で `text/event-stream` の応答として
加える。Runのイベントの型（RunStreamEvent）は、RunEventの一覧のAPIが出力する同じcomponentを参照する。
"""

from typing import Any

USER_STREAM_EVENT = {
    "type": "object",
    "description": "ログインユーザー宛ての通知：自分のRunの状態の変更",
    "properties": {
        "type": {"type": "string", "enum": ["run.status"]},
        "run": {"type": "string"},
        "conversation": {"type": "string"},
        "status": {"$ref": "#/components/schemas/RunStatusEnum"},
        "sequence": {"type": "integer"},
    },
    "required": ["type", "run", "conversation", "status", "sequence"],
}


def _stream(description: str, tag: str, schema_ref: str, parameters: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "get": {
            "operationId": f"{tag.replace('/', '_')}_stream",
            "description": description,
            "tags": [tag],
            "parameters": parameters,
            "security": [{"cookieAuth": []}],
            "responses": {
                "200": {
                    "content": {"text/event-stream": {"schema": {"$ref": schema_ref}}},
                    "description": "SSE。`id:` はRunEventのsequence（Last-Event-IDで続きから受け取る）",
                },
                "401": {"description": "未ログイン"},
                "404": {"description": "自分のRunではない"},
            },
        }
    }


def add_stream_endpoints(result: dict[str, Any], generator, request, public) -> dict[str, Any]:
    result["components"]["schemas"]["UserStreamEvent"] = USER_STREAM_EVENT
    paths = result["paths"]
    paths["/api/app/v1/stream/"] = _stream(
        "ログインユーザー宛ての通知のストリーム", "app/stream", "#/components/schemas/UserStreamEvent", []
    )
    paths["/api/app/v1/runs/{run}/stream/"] = _stream(
        "1つのRunの詳細イベントのストリーム",
        "app/run/stream",
        "#/components/schemas/RunStreamEvent",
        [{"name": "run", "in": "path", "required": True, "schema": {"type": "string"}}],
    )
    return result
