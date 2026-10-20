import os

from django.core.checks import Warning, register


@register()
def check_removed_sso_env(app_configs, **kwargs):
    """廃止した環境変数 YORIKI_SSO_* が残っていたら知らせる。

    SSOの設定は管理画面（/manage/sso_providers）で登録する方式に変えたので、環境変数は読まない。
    残ったまま更新すると、気づかないうちにSSOでログインできなくなるため（design.md §10.0）
    """
    names = sorted(name for name in os.environ if name.startswith("YORIKI_SSO_"))
    if not names:
        return []
    return [
        Warning(
            f"環境変数 {', '.join(names)} は使われなくなりました。",
            hint=(
                "基盤管理者でログインし（/api/admin/login/）、"
                "管理画面の /manage/sso_providers でSSOの設定を登録してください。"
            ),
            id="user.W001",
        )
    ]
