from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .models import Artifact, ArtifactKind


class ArtifactSerializer(serializers.ModelSerializer):
    """会話の添付と成果物。添付は `file` を送って作る（multipart/form-data）。メッセージに付けて送るまでは、
    一覧に出ない"""

    id = serializers.CharField(source="uid", read_only=True)
    kind = serializers.ChoiceField(choices=ArtifactKind.choices, read_only=True)
    size = serializers.IntegerField(source="blob.size", read_only=True)
    run: "serializers.SlugRelatedField[Artifact]" = serializers.SlugRelatedField(
        slug_field="uid", read_only=True, allow_null=True
    )
    file = serializers.FileField(write_only=True, help_text=_("添付するファイル"))

    class Meta:
        model = Artifact
        fields = ["id", "kind", "name", "path", "content_type", "size", "run", "file", "created"]
        read_only_fields = ["name", "path", "content_type", "created"]

    def validate_file(self, value):
        limit = settings.YORIKI_UPLOAD_MAX_MB
        if value.size > limit * 1024 * 1024:
            raise serializers.ValidationError(_("ファイルが大きすぎます（%(limit)sMBまで）") % {"limit": limit})
        return value

    def create(self, validated_data) -> Artifact:
        file = validated_data["file"]
        return Artifact.upload(
            file, file.name or "", owner=validated_data["owner"], conversation=validated_data["conversation"]
        )
