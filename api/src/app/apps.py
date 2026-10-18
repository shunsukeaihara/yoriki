from django.contrib.admin.apps import AdminConfig


class YorikiAdminConfig(AdminConfig):
    default_site = "app.admin.YorikiAdminSite"
