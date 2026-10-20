from django.contrib import admin


class ReadOnlyAdmin(admin.ModelAdmin):
    """Django管理画面では見るだけにする。登録・変更・削除はYorikiの管理画面（API）で行う（backend-conventions.md §7）"""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
