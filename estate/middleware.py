from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin

class ForcePasswordChangeMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if not request.user.is_authenticated:
            return None

        # Allow these URLs even if password not changed
        allowed_paths = [
            reverse("password_change"),
            reverse("password_change_done"),
            reverse("logout"),
        ]

        if request.path in allowed_paths:
            return None

        # Never block admin access (safety)
        if request.path.startswith("/admin/"):
            return None

        profile = getattr(request.user, "userprofile", None)
        if profile and profile.must_change_password:
            return redirect("password_change")

        return None