from django.conf import settings
from django.contrib.auth import login
from django.http import Http404
from django.shortcuts import redirect
from django.views.generic import CreateView

from .forms import SignUpForm


class SignUpView(CreateView):
    form_class = SignUpForm
    template_name = "accounts/signup.html"

    def dispatch(self, request, *args, **kwargs):
        if not settings.SIGNUP_ENABLED:
            raise Http404("Account registration is disabled.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        return redirect("movies:dashboard")
