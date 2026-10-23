import factory
from factory.django import DjangoModelFactory

from user.factories import UserFactory

from .models import Skill, SkillDraft, SkillScope, SkillVersion


class SkillFactory(DjangoModelFactory[Skill]):
    """個人の範囲のSkill（持ち主は作成者）。公開した版は SkillVersionFactory で作る"""

    class Meta:
        model = Skill

    scope = SkillScope.PERSONAL
    name = factory.Sequence(lambda n: f"Skill{n}")
    created_by = factory.SubFactory(UserFactory)
    owner = factory.LazyAttribute(lambda o: o.created_by if o.scope == SkillScope.PERSONAL else None)


class SkillVersionFactory(DjangoModelFactory[SkillVersion]):
    """版を作り、Skillの公開中の版にする"""

    class Meta:
        model = SkillVersion
        skip_postgeneration_save = True  # make_current() で Skill を保存する

    skill = factory.SubFactory(SkillFactory)
    number = factory.LazyAttribute(lambda o: o.skill.versions.count() + 1)
    description = "請求書を確認する"
    instructions = "1. 請求書を読む"
    created_by = factory.SelfAttribute("skill.created_by")

    @factory.post_generation
    def make_current(obj: SkillVersion, create, extracted, **kwargs):
        if create and extracted is not False:
            obj.skill.current_version = obj
            obj.skill.save(update_fields=["current_version", "updated"])


class SkillDraftFactory(DjangoModelFactory[SkillDraft]):
    class Meta:
        model = SkillDraft

    skill = factory.SubFactory(SkillFactory)
    base_version = factory.LazyAttribute(lambda o: o.skill.current_version)
    description = "請求書を確認する"
    instructions = "1. 請求書を読む"
    created_by = factory.SelfAttribute("skill.created_by")
    updated_by = factory.SelfAttribute("skill.created_by")
