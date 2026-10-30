import factory
from factory.django import DjangoModelFactory

from llm.factories import ModelProfileFactory
from user.factories import UserFactory

from .models import TaskDefinition, TaskScope


class TaskDefinitionFactory(DjangoModelFactory[TaskDefinition]):
    """個人の範囲のタスク（持ち主は作成者）と最初の版。版の項目は `revision__skills=[...]` のように指定する"""

    class Meta:
        model = TaskDefinition
        skip_postgeneration_save = True  # revision() の revise() で保存する

    scope = TaskScope.PERSONAL  # 部門の範囲にするときは department も渡す
    name = factory.Sequence(lambda n: f"タスク{n}")
    created_by = factory.SubFactory(UserFactory)
    owner = factory.LazyAttribute(lambda o: o.created_by if o.scope == TaskScope.PERSONAL else None)

    @factory.post_generation
    def revision(obj: TaskDefinition, create, extracted, **kwargs):
        if not create:
            return
        kwargs.setdefault("description", "")
        kwargs.setdefault("prompt_template", "請求書を確認してください。")
        kwargs.setdefault("skills", [])
        kwargs.setdefault("connectors", [])
        kwargs.setdefault("tools", [])
        if "model_profile" not in kwargs:
            kwargs["model_profile"] = ModelProfileFactory.create(version__supports_tools=True)
        kwargs.setdefault("effort", "")
        kwargs.setdefault("handles_sensitive_data", False)
        kwargs.setdefault("inputs", [])
        kwargs.setdefault("output_schema", None)
        kwargs.setdefault("destination", None)
        kwargs.setdefault("notify_events", [])
        kwargs.setdefault("notify_content", "status_and_result")
        kwargs.setdefault("notification_skill", None)
        obj.revise(actor=obj.created_by, expected_version=0, **kwargs)
