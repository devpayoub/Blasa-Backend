from django.db import models


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("Ledger entries are immutable")

    def delete(self):
        raise PermissionError("Ledger entries are immutable")
