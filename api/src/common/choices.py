"""複数アプリで使うChoices"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class DataClassification(models.IntegerChoices):
    """データ区分。「社内のみ」の業務には、社外へ送信しないモデル・接続だけを選べるようにする（design.md §4.8）"""

    EXTERNAL_ALLOWED = 1, _("社外送信可")
    INTERNAL_ONLY = 2, _("社内のみ")
