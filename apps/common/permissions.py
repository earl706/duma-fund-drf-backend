from rest_framework import permissions

# -----------------------------------------------------------------------------
# Object-level ownership permission
# -----------------------------------------------------------------------------


class IsOwner(permissions.BasePermission):

    message = "You do not have permission to access this resource."

    def has_object_permission(self, request, view, obj):
        owner = getattr(obj, "owner", None)
        return owner is not None and owner == request.user
