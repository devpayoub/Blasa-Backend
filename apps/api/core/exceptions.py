from rest_framework.views import exception_handler

# Stable machine-readable codes for the client error catalog. The frontend maps
# these to user-facing copy instead of rendering DRF's developer messages.
CODE_BY_STATUS = {
    400: "validation_error",
    401: "authentication_required",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    415: "unsupported_media_type",
    429: "throttled",
    500: "server_error",
    503: "unavailable",
}


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        code = getattr(exc, "default_code", None) or CODE_BY_STATUS.get(response.status_code, "error")
        response.data = {
            "error": {
                "status": response.status_code,
                "code": str(code),
                "detail": response.data,
            }
        }
    return response
