import hashlib

from .models import AuditLog


class AdminAuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if (
            request.path.startswith("/admin/")
            and user is not None
            and user.is_authenticated
            and user.is_staff
        ):
            ip = request.META.get("REMOTE_ADDR", "")
            AuditLog.objects.create(
                actor=user,
                method=request.method,
                path=request.path[:500],
                status_code=response.status_code,
                request_id=getattr(request, "request_id", ""),
                ip_hash=hashlib.sha256(ip.encode()).hexdigest(),
            )
        return response
