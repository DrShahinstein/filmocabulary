from django.conf import settings


def account_access(request):
    return {"signup_enabled": settings.SIGNUP_ENABLED}
