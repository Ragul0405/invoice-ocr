import mimetypes
import os

from django.conf import settings
from rest_framework import serializers

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}
# Python's stdlib `mimetypes` module doesn't know about .heic/.heif (a
# newer format) — this covers what mimetypes.guess_type() can't.
_EXTENSION_CONTENT_TYPE_OVERRIDES = {
    ".heic": "image/heic",
    ".heif": "image/heif",
}


class InvoiceUploadSerializer(serializers.Serializer):
    invoice = serializers.FileField()

    def validate_invoice(self, uploaded_file):
        max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
        if uploaded_file.size > max_bytes:
            raise serializers.ValidationError(
                f"File is too large ({uploaded_file.size / 1024 / 1024:.1f}MB). "
                f"Max allowed is {settings.MAX_UPLOAD_MB}MB."
            )
        if uploaded_file.size == 0:
            raise serializers.ValidationError("Uploaded file is empty.")

        ext = os.path.splitext(uploaded_file.name or "")[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise serializers.ValidationError(
                f"Unsupported file extension '{ext}'. Allowed: "
                f"{', '.join(sorted(ALLOWED_EXTENSIONS))}."
            )

        content_type = (uploaded_file.content_type or "").lower()
        if content_type not in ALLOWED_CONTENT_TYPES:
            # Some clients (curl, some mobile HTTP libs) don't set a
            # trustworthy content-type — fall back to guessing from the
            # filename rather than rejecting a legitimate upload.
            guessed = _EXTENSION_CONTENT_TYPE_OVERRIDES.get(ext) or mimetypes.guess_type(uploaded_file.name or "")[0]
            if guessed in ALLOWED_CONTENT_TYPES:
                content_type = guessed
            else:
                raise serializers.ValidationError(
                    f"Unsupported content type '{uploaded_file.content_type}'."
                )

        uploaded_file._resolved_content_type = content_type
        return uploaded_file
