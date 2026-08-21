"""Consistent {"success": false, "error": {...}} envelope for every DRF error."""

from rest_framework.views import exception_handler as drf_exception_handler


def custom_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(response.data, dict) and "detail" in response.data:
        message = str(response.data["detail"])
        extra = {k: v for k, v in response.data.items() if k != "detail"}
    else:
        message = str(response.data)
        extra = {}

    response.data = {
        "success": False,
        "error": {
            "code": getattr(exc, "default_code", exc.__class__.__name__),
            "message": message,
            **({"details": extra} if extra else {}),
        },
    }
    return response
