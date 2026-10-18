from rest_framework.pagination import CursorPagination


class CursorPaginationWithPageSizeQuery(CursorPagination):
    """カーソル方式のページング。並び順はViewSetの `ordering` を使う（backend-conventions.md §4.1）"""

    page_size_query_param = "page_size"
    max_page_size = 200
