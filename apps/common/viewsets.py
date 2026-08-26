from rest_framework import viewsets

from .permissions import IsOwner

# -----------------------------------------------------------------------------
# Owner-scoped viewset base
# -----------------------------------------------------------------------------


class OwnedModelViewSet(viewsets.ModelViewSet):
    permission_classes = [IsOwner]

    def get_queryset(self):

        qs = super().get_queryset()
        if self.request.user.is_authenticated:
            return qs.filter(owner=self.request.user)
        return qs.none()

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)
