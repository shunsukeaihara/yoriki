"""入口・出口・Connectorの接続が使うAdapterの選択（design.md §3.11.1）"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from . import registry
from .spec import ExtensionType


class ExtensionField(serializers.CharField):
    """Adapterのextension_id。新しく作るときは、使えてdeprecatedでないPluginのものだけを受け付ける"""

    def __init__(self, *extension_types: ExtensionType, **kwargs):
        self.extension_types = extension_types
        kwargs.setdefault("max_length", 200)
        kwargs.setdefault("help_text", _("種別（PluginのAdapterのID）。作成した後は変えられない"))
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not any(value in registry.available_ids(t, for_new=True) for t in self.extension_types):
            raise serializers.ValidationError(_("この種別は選べません（停止中・廃止予定等）"))
        return value
