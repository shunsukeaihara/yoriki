from rest_framework import serializers


class ReadOnlyModelSerializer(serializers.ModelSerializer):
    """表示専用のModelSerializer。すべての項目を読み取り専用にする"""

    def get_fields(self):
        fields = super().get_fields()
        for field in fields.values():
            field.read_only = True
        return fields

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class CountSerializer(serializers.Serializer):
    count = serializers.IntegerField(read_only=True)


class PingSerializer(serializers.Serializer):
    status = serializers.CharField(read_only=True)
