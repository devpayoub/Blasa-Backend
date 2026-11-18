from rest_framework.routers import DefaultRouter

from .views import VerificationDocumentViewSet

router = DefaultRouter()
router.register("documents", VerificationDocumentViewSet, basename="verification-document")

urlpatterns = router.urls
