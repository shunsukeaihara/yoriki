from rest_framework.routers import DefaultRouter

from .views import (
    AppSkillChangeViewSet,
    AppSkillDraftAssetViewSet,
    AppSkillDraftViewSet,
    AppSkillEvaluationCaseViewSet,
    AppSkillEvaluationViewSet,
    AppSkillFileViewSet,
    AppSkillVersionViewSet,
    AppSkillViewSet,
)

router = DefaultRouter()

router.register("app/v1/skills", AppSkillViewSet, basename="app_skill_viewset")
router.register("app/v1/skills/(?P<skill>[^/]+)/versions", AppSkillVersionViewSet, basename="app_skill_version_viewset")
router.register("app/v1/skills/(?P<skill>[^/]+)/changes", AppSkillChangeViewSet, basename="app_skill_change_viewset")
router.register("app/v1/skills/(?P<skill>[^/]+)/drafts", AppSkillDraftViewSet, basename="app_skill_draft_viewset")
router.register(
    "app/v1/skills/(?P<skill>[^/]+)/drafts/(?P<draft>[^/]+)/assets",
    AppSkillDraftAssetViewSet,
    basename="app_skill_draft_asset_viewset",
)
router.register(
    "app/v1/skills/(?P<skill>[^/]+)/evaluation_cases",
    AppSkillEvaluationCaseViewSet,
    basename="app_skill_evaluation_case_viewset",
)
router.register(
    "app/v1/skills/(?P<skill>[^/]+)/evaluations", AppSkillEvaluationViewSet, basename="app_skill_evaluation_viewset"
)
router.register("app/v1/skills/(?P<skill>[^/]+)/files", AppSkillFileViewSet, basename="app_skill_file_viewset")

urlpatterns = router.urls
