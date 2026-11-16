from rest_framework.routers import DefaultRouter

from .views import AppPluginExtensionViewSet, PluginViewSet

router = DefaultRouter()

router.register("v1/plugins", PluginViewSet, basename="plugin_viewset")
router.register("app/v1/plugin_extensions", AppPluginExtensionViewSet, basename="app_plugin_extension_viewset")

urlpatterns = router.urls
