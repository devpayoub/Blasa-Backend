from rest_framework.routers import DefaultRouter

from .views import InterestViewSet

router = DefaultRouter()
router.register("", InterestViewSet, basename="interest")

urlpatterns = router.urls
