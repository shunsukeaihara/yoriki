from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import get_object_or_404
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ReadOnlyModelViewSet

from artifact.download import file_response
from artifact.views import FILE_RESPONSE
from resources.models import ResourceVersion
from utils.exceptions import Conflict
from utils.permissions import AppUser
from utils.views import SoftDeleteMixin

from .errors import VersionConflict
from .models import (
    CONVERSATION_SCOPES,
    EvaluationStatus,
    Skill,
    SkillAction,
    SkillAssetBinding,
    SkillChange,
    SkillDraft,
    SkillDraftAsset,
    SkillEvaluation,
    SkillEvaluationAsset,
    SkillEvaluationCase,
    SkillEvaluationResult,
    SkillScope,
    SkillVersion,
)
from .serializers import (
    MinimumSkillEvaluationSerializer,
    MinimumSkillVersionSerializer,
    ResourceVersionSerializer,
    SkillChangeSerializer,
    SkillCreateSerializer,
    SkillDraftAssetChecksSerializer,
    SkillDraftAssetSerializer,
    SkillDraftAssetUpdateSerializer,
    SkillDraftOpenSerializer,
    SkillDraftRevisionSerializer,
    SkillDraftSerializer,
    SkillEvaluationCaseSerializer,
    SkillEvaluationCreateSerializer,
    SkillEvaluationSerializer,
    SkillPublishSerializer,
    SkillRollbackSerializer,
    SkillSerializer,
    SkillSuspendSerializer,
    SkillVersionSerializer,
)

CONFLICT = OpenApiResponse(description="他の変更と競合した（編集を始めた後に、Draftか公開中の版が変わった）")


class SkillFilter(filters.FilterSet):
    name = filters.CharFilter(label=_("名前"), lookup_expr="icontains")
    scope = filters.ChoiceFilter(label=_("公開範囲"), choices=SkillScope.choices)
    department = filters.CharFilter(label=_("部門"), field_name="department__uid")
    published = filters.BooleanFilter(
        label=_("公開済み"), field_name="current_version", lookup_expr="isnull", exclude=True
    )
    editable = filters.BooleanFilter(label=_("編集できるものだけ"), method="filter_editable")

    class Meta:
        model = Skill
        fields = ["name", "scope", "department", "suspended", "published", "editable"]

    def filter_editable(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(pk__in=Skill.objects.editable_by(self.request.user).values("pk"))


class CanOperateSkill(BasePermission):
    """操作に要る権限（ViewSetの `required_actions`。書いていない操作は公開の権限）を、利用者がこのSkillに持つか"""

    def has_object_permission(self, request, view, obj) -> bool:
        return obj.can(request.user, view.required_actions.get(view.action, SkillAction.PUBLISH))


@extend_schema(description="Skillの一覧・作成・公開・切戻し・停止", tags=["app/skill"])
@extend_schema_view(
    create=extend_schema(request=SkillCreateSerializer, responses={201: SkillSerializer}),
    update=extend_schema(description="名前を変える（公開の権限が要る）"),
    partial_update=extend_schema(description="名前を変える（公開の権限が要る）"),
    destroy=extend_schema(description="Skillを削除する（公開の権限が要る）"),
)
class AppSkillViewSet(
    SoftDeleteMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, ReadOnlyModelViewSet
):
    """利用者に見えるSkill（authorization.md §5.1）。一覧は個人・部門・全社で、システムのSkillは `scope` で
    指定したときだけ返す（管理画面の一覧。業務画面には出さない）。

    作るとDraftが開く。中身の編集はDraft（drafts）で行い、公開（publish）で版になる
    """

    permission_classes = [AppUser, CanOperateSkill]
    required_actions = {"retrieve": SkillAction.VIEW}
    lookup_field = "uid"
    queryset = (
        Skill.objects.filter(deleted=False)
        .select_related("owner", "department", "current_version")
        .prefetch_related(Prefetch("drafts", queryset=SkillDraft.objects.only("uid", "skill"), to_attr="open_drafts"))
    )
    serializer_class = SkillSerializer
    filterset_class = SkillFilter
    search_fields = ("name",)
    ordering = ("name", "pk")

    def get_queryset(self):
        qs = super().get_queryset().filter(pk__in=Skill.objects.visible_to(self.request.user).values("pk"))
        if self.action == "list" and self.request.query_params.get("scope") != str(SkillScope.SYSTEM):
            qs = qs.filter(scope__in=CONVERSATION_SCOPES)
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return SkillCreateSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        # Skillと最初のDraftは Skill.create_with_draft() の transaction で作る
        serializer.save(actor=self.request.user)

    @extend_schema(
        description="Draftを公開し、新しい版を公開中の版にする（公開の権限が要る）。Draftは閉じる",
        request=SkillPublishSerializer,
        responses={201: SkillVersionSerializer, 409: CONFLICT},
    )
    @action(detail=True, methods=["post"], serializer_class=SkillPublishSerializer)
    def publish(self, request, *args, **kwargs):
        skill = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(skill=skill, actor=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        description="選んだ過去の版と同じ中身・資料の版を新しく作り、公開中の版にする（公開の権限が要る）",
        request=SkillRollbackSerializer,
        responses={201: SkillVersionSerializer, 409: CONFLICT},
    )
    @action(detail=True, methods=["post"], serializer_class=SkillRollbackSerializer)
    def rollback(self, request, *args, **kwargs):
        skill = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(skill=skill, actor=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        description="緊急停止する。新しいRunでも進行中のRunでも使わせない（公開の権限が要る）",
        request=SkillSuspendSerializer,
        responses=SkillSerializer,
    )
    @action(detail=True, methods=["post"], serializer_class=SkillSuspendSerializer)
    def suspend(self, request, *args, **kwargs):
        return self._set_suspended(request, suspended=True)

    @extend_schema(
        description="停止を解く（公開の権限が要る）", request=SkillSuspendSerializer, responses=SkillSerializer
    )
    @action(detail=True, methods=["post"], serializer_class=SkillSuspendSerializer)
    def resume(self, request, *args, **kwargs):
        return self._set_suspended(request, suspended=False)

    def _set_suspended(self, request, *, suspended: bool) -> Response:
        serializer = self.get_serializer(instance=self.get_object(), data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(actor=request.user, suspended=suspended)
        return Response(serializer.data)


class SkillMixin:
    """URLのskillを、利用者が見えるSkillに限定して取得する。操作に要る権限（`skill_action`）がなければ403"""

    kwargs: dict[str, str]
    skill_action = SkillAction.VIEW
    skill_lookup = "skill"  # 一覧の行からSkillへの参照

    @property
    def skill(self) -> Skill:
        if not hasattr(self, "_skill"):
            user = self.request.user  # type: ignore[attr-defined]
            skill = get_object_or_404(
                Skill.objects.visible_to(user).select_related("department"), uid=self.kwargs["skill"]
            )
            if not skill.can(user, self.skill_action):
                raise PermissionDenied(_("このSkillを操作する権限がありません"))
            self._skill = skill
        return self._skill

    def get_queryset(self):
        return super().get_queryset().filter(**{self.skill_lookup: self.skill})  # type: ignore[misc]

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "skill": self.skill}  # type: ignore[misc]


@extend_schema(description="Skillの公開した版", tags=["app/skill/version"])
class AppSkillVersionViewSet(SkillMixin, ReadOnlyModelViewSet):
    permission_classes = [AppUser]
    lookup_field = "number"  # 版番号で識別する子リソースの例外（backend-conventions.md §4.1）
    queryset = SkillVersion.objects.select_related("created_by", "rolled_back_from").prefetch_related(
        "assets__resource_version__resource", "assets__resource_version__blob", "assets__resource_version__created_by"
    )
    serializer_class = SkillVersionSerializer
    ordering = "-number"  # Skillの中で一意

    def get_serializer_class(self):
        if self.action == "list":
            return MinimumSkillVersionSerializer
        return super().get_serializer_class()


@extend_schema(description="Skillの公開・切戻し・停止の記録", tags=["app/skill/change"])
class AppSkillChangeViewSet(SkillMixin, mixins.ListModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    queryset = SkillChange.objects.select_related("version", "previous_version", "actor")
    serializer_class = SkillChangeSerializer
    ordering = ("-created", "-pk")


@extend_schema(description="SkillのDraft（編集中の中身）。Skillごとに1つ", tags=["app/skill/draft"])
@extend_schema_view(
    create=extend_schema(request=SkillDraftOpenSerializer, responses={201: SkillDraftSerializer, 409: CONFLICT}),
    update=extend_schema(responses={200: SkillDraftSerializer, 409: CONFLICT}),
    partial_update=extend_schema(responses={200: SkillDraftSerializer, 409: CONFLICT}),
    destroy=extend_schema(
        description="Draftを破棄する", parameters=[SkillDraftRevisionSerializer], responses={204: None, 409: CONFLICT}
    ),
)
class AppSkillDraftViewSet(
    SkillMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    permission_classes = [AppUser]
    skill_action = SkillAction.EDIT
    lookup_field = "uid"
    queryset = SkillDraft.objects.select_related("skill", "base_version", "updated_by").prefetch_related(
        "assets__resource_version__resource", "assets__resource_version__blob", "assets__resource_version__created_by"
    )
    serializer_class = SkillDraftSerializer
    pagination_class = None  # Skillごとに1つ
    ordering = "pk"

    def get_serializer_class(self):
        if self.action == "create":
            return SkillDraftOpenSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        serializer.save(actor=self.request.user)

    def perform_update(self, serializer):
        serializer.save(actor=self.request.user)

    def destroy(self, request, *args, **kwargs):
        draft = self.get_object()
        serializer = SkillDraftRevisionSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        try:
            draft.discard(expected_revision=serializer.validated_data["revision"])
        except VersionConflict as exc:
            raise Conflict() from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


class SkillDraftMixin(SkillMixin):
    """URLのdraftを、URLのSkillのDraftに限定して取得する（Draftを編集できる人だけ）"""

    skill_action = SkillAction.EDIT
    skill_lookup = "draft__skill"

    @property
    def draft(self) -> SkillDraft:
        if not hasattr(self, "_draft"):
            self._draft = get_object_or_404(SkillDraft.objects.all(), uid=self.kwargs["draft"], skill=self.skill)
        return self._draft

    def get_queryset(self):
        return super().get_queryset().filter(draft=self.draft)


@extend_schema(
    description="SkillのDraftの資料（参照資料・マスター・テンプレート・スクリプト）", tags=["app/skill/draft/asset"]
)
@extend_schema_view(
    create=extend_schema(responses={201: SkillDraftAssetSerializer, 409: CONFLICT}),
    update=extend_schema(
        request=SkillDraftAssetUpdateSerializer, responses={200: SkillDraftAssetSerializer, 409: CONFLICT}
    ),
    partial_update=extend_schema(
        request=SkillDraftAssetUpdateSerializer, responses={200: SkillDraftAssetSerializer, 409: CONFLICT}
    ),
)
class AppSkillDraftAssetViewSet(
    SkillDraftMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    """資料の原本は版付きで保存し、置き換えても前の版を残す（公開済みの版は前の原本を使い続ける。design.md §3.10）"""

    permission_classes = [AppUser]
    lookup_field = "key"  # Skillの中の資料の名前で識別する
    queryset = SkillDraftAsset.objects.select_related(
        "draft", "resource_version__resource", "resource_version__blob", "resource_version__created_by"
    )
    serializer_class = SkillDraftAssetSerializer
    parser_classes = [MultiPartParser]
    pagination_class = None  # Skillの資料は少ない
    ordering = ("key", "pk")

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return SkillDraftAssetUpdateSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        # 原本の保存と資料の追加は SkillDraft.add_asset() の transaction で行う
        serializer.save(draft=self.draft, actor=self.request.user)

    def perform_update(self, serializer):
        serializer.save(actor=self.request.user)

    @extend_schema(
        description="資料の検証の条件（CSVの必須列・キー列・型等）を変え、検証し直す（公開の権限が要る）",
        request=SkillDraftAssetChecksSerializer,
        responses={200: SkillDraftAssetSerializer, 409: CONFLICT},
    )
    @action(detail=True, methods=["put"], parser_classes=[JSONParser], serializer_class=SkillDraftAssetChecksSerializer)
    def checks(self, request, *args, **kwargs):
        serializer = self.get_serializer(instance=self.get_object(), data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(actor=request.user)
        return Response(serializer.data)

    @extend_schema(
        description="資料をDraftから外す（原本は残る）",
        parameters=[SkillDraftRevisionSerializer],
        responses={204: None, 409: CONFLICT},
    )
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = SkillDraftRevisionSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        try:
            self.draft.remove_asset(
                instance, actor=request.user, expected_revision=serializer.validated_data["revision"]
            )
        except VersionConflict as exc:
            raise Conflict() from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(description="Skillの資料のファイル（版とDraftが使う原本）", tags=["app/skill/file"])
class AppSkillFileViewSet(SkillMixin, mixins.RetrieveModelMixin, GenericViewSet):
    permission_classes = [AppUser]
    lookup_field = "uid"
    queryset = ResourceVersion.objects.select_related("resource", "blob", "created_by")
    serializer_class = ResourceVersionSerializer
    ordering = "pk"

    def get_queryset(self):
        # このSkillの版が使う原本。Draftを編集できる人には、Draftの原本も
        used = Q(pk__in=SkillAssetBinding.objects.filter(version__skill=self.skill).values("resource_version"))
        if self.skill.can(self.request.user, SkillAction.EDIT):
            used |= Q(pk__in=SkillDraftAsset.objects.filter(draft__skill=self.skill).values("resource_version"))
            # 評価に写したDraftの資料と、評価ケースの添付
            used |= Q(
                pk__in=SkillEvaluationAsset.objects.filter(evaluation__skill=self.skill).values("resource_version")
            )
            used |= Q(
                pk__in=SkillEvaluationCase.files.through.objects.filter(skillevaluationcase__skill=self.skill).values(
                    "resourceversion"
                )
            )
        return ResourceVersion.objects.filter(used).select_related("resource", "blob", "created_by")

    @extend_schema(description="ファイルを取得する（ダウンロードとして返す）", request=None, responses=FILE_RESPONSE)
    @action(detail=True, methods=["get"], serializer_class=None)
    def download(self, request, *args, **kwargs):
        return file_response(self.get_object())


@extend_schema(description="Skillの評価ケース（design.md §3.7.1）", tags=["app/skill/evaluation_case"])
class AppSkillEvaluationCaseViewSet(
    SkillMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    """作成・変更・削除はDraftを編集できる人。公開の前に必須のケースは、公開の権限が要る"""

    permission_classes = [AppUser]
    skill_action = SkillAction.EDIT
    lookup_field = "uid"
    queryset = SkillEvaluationCase.objects.select_related("updated_by").prefetch_related(
        "files__resource", "files__blob", "files__created_by"
    )
    serializer_class = SkillEvaluationCaseSerializer
    parser_classes = [MultiPartParser, JSONParser]
    pagination_class = None  # 1つのSkillに20件まで
    ordering = ("-required", "name", "pk")

    def perform_create(self, serializer):
        serializer.save(actor=self.request.user)

    def perform_update(self, serializer):
        serializer.save(actor=self.request.user)

    @transaction.atomic
    def perform_destroy(self, instance: SkillEvaluationCase):
        # 公開の判定（Skillの行をロックする）と重ならないよう、Skill → ケースの順にロックしてから消す
        Skill.objects.select_for_update().filter(pk=instance.skill_id).first()
        instance = SkillEvaluationCase.objects.select_for_update().get(pk=instance.pk)
        if instance.required and not self.skill.can(self.request.user, SkillAction.PUBLISH):
            raise PermissionDenied(_("公開の前に必須の評価ケースを消すには、この範囲の公開の権限が要ります"))
        instance.delete()


@extend_schema(description="SkillのDraftの評価（design.md §3.7.1）", tags=["app/skill/evaluation"])
@extend_schema_view(
    create=extend_schema(
        description="今のDraftを、選んだケースで評価し始める",
        request=SkillEvaluationCreateSerializer,
        responses={201: SkillEvaluationSerializer},
    ),
)
class AppSkillEvaluationViewSet(
    SkillMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, GenericViewSet
):
    permission_classes = [AppUser]
    skill_action = SkillAction.EDIT
    lookup_field = "uid"
    queryset = SkillEvaluation.objects.select_related(
        "base_version", "model_version__profile", "requested_by"
    ).prefetch_related(
        Prefetch(
            "results",
            queryset=SkillEvaluationResult.objects.select_related("case", "base_run", "draft_run").order_by("order"),
        )
    )
    serializer_class = SkillEvaluationSerializer
    ordering = ("-created", "-pk")

    def get_serializer_class(self):
        if self.action == "list":
            return MinimumSkillEvaluationSerializer
        if self.action == "create":
            return SkillEvaluationCreateSerializer
        return super().get_serializer_class()

    def get_serializer_context(self):
        draft = SkillDraft.objects.filter(skill=self.skill).only("uid", "revision").first()
        return {**super().get_serializer_context(), "current_draft": draft}

    def perform_create(self, serializer):
        # 評価のRunは start_evaluation() の transaction で作る
        serializer.save(actor=self.request.user)

    @extend_schema(
        description="評価を止める（動いている評価のRunを止める）", request=None, responses=SkillEvaluationSerializer
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        from conversation.evaluation import cancel_evaluation  # noqa: PLC0415  conversationがskillをimportするため

        evaluation = self.get_object()
        if evaluation.status == EvaluationStatus.RUNNING:
            cancel_evaluation(evaluation.pk, actor=request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=evaluation.pk)).data)
