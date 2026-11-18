from drf_spectacular.openapi import AutoSchema


class DomainTagAutoSchema(AutoSchema):
    """Tags every operation with its /api/v1/<domain>/ segment.

    Swagger then shows one collapsed group per domain (auth, bookings, wallet,
    payments, ...) instead of dumping everything into a single "v1" group.
    """

    def get_tags(self):
        tokens = self._tokenize_path()
        if "v1" in tokens:
            idx = tokens.index("v1")
            if idx + 1 < len(tokens):
                return [tokens[idx + 1]]
        return tokens[:1] or ["api"]
