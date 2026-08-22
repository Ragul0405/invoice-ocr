import logging
import os
import sys
import uuid

from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import InvoiceUploadSerializer

# local_invoice_extractor.py and image_ocr.py live at the project root
# (BASE_DIR), shared with standalone_api.py so there's exactly one
# extraction engine, not two.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import image_ocr  # noqa: E402
import local_invoice_extractor as extractor  # noqa: E402

logger = logging.getLogger("extraction")

_IMAGE_CONTENT_TYPES = {"image/heic", "image/heif", "image/jpeg", "image/jpg", "image/png", "image/webp"}


class HealthView(APIView):
    """Unauthenticated liveness check for load balancers / uptime monitors."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok"})


class ExtractInvoiceView(APIView):
    """
    POST /api/v1/invoices/extract/

    Open endpoint — no authentication required. Fully offline: no cloud
    API call, no API key. PDFs are read directly
    (local_invoice_extractor.py, pure stdlib). Photos (HEIC/JPG/PNG/WEBP)
    are read via local OCR (image_ocr.py — the one part of this project
    that uses third-party packages: Pillow, pillow-heif, and the EasyOCR
    engine) and then fed through the exact same field-extraction rules as
    PDF text.

    multipart/form-data with a single field `invoice`.

    Response shape (always flat — no nested `data`/`meta`):
        {"status": 1, "product_name": ..., "model_number": ...,
         "other_brand_name": ..., "purchase_date": ...,
         "warranty_start_date": ..., "warranty_end_date": ..., "notes": ...}
    or on failure:
        {"status": 0, "message": "..."}
    """

    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        request_id = uuid.uuid4().hex[:12]

        # Accept a couple of common field-name aliases so integration is
        # forgiving of whatever the calling app happens to name the field.
        upload = request.FILES.get("invoice")
        if upload is None:
            for alias in ("invoice_file", "file", "invoice_image", "document"):
                if alias in request.FILES:
                    upload = request.FILES[alias]
                    break

        serializer = InvoiceUploadSerializer(data={"invoice": upload})
        if not serializer.is_valid():
            logger.info("[%s] validation failed: %s", request_id, serializer.errors)
            first_error = next(iter(serializer.errors.values()))[0] if serializer.errors else "Invalid upload."
            return Response(extractor.build_error_response(str(first_error)), status=400)

        uploaded_file = serializer.validated_data["invoice"]
        content_type = getattr(uploaded_file, "_resolved_content_type", uploaded_file.content_type)
        file_bytes = uploaded_file.read()

        logger.info(
            "[%s] extracting: name=%r size=%dKB type=%s",
            request_id, uploaded_file.name, len(file_bytes) // 1024, content_type,
        )

        if content_type == "application/pdf":
            try:
                text = extractor.extract_pdf_text(file_bytes)
            except extractor.PdfExtractionError as exc:
                logger.warning("[%s] PDF extraction failed: [%s] %s", request_id, exc.code, exc.message)
                return Response(extractor.build_error_response(exc.message), status=422)
        elif content_type in _IMAGE_CONTENT_TYPES:
            try:
                text = image_ocr.extract_text_from_image(file_bytes, content_type)
                extractor.check_invoice_signals(text)
            except image_ocr.OcrError as exc:
                logger.warning("[%s] OCR failed: [%s] %s", request_id, exc.code, exc.message)
                status_code = 503 if exc.code == "ocr_engine_not_installed" else 422
                return Response(extractor.build_error_response(exc.message), status=status_code)
            except extractor.NotAnInvoiceError as exc:
                logger.warning("[%s] OCR'd image not invoice-shaped: %s", request_id, exc.message)
                return Response(extractor.build_error_response(exc.message), status=422)
        else:
            return Response(
                extractor.build_error_response(f"Unsupported content type '{content_type}'."),
                status=415,
            )

        fields = extractor.extract_fields(text)
        logger.info(
            "[%s] extraction ok (confidence=%s, warnings=%d)",
            request_id, fields.get("confidence"), len(fields.get("warnings") or []),
        )

        return Response(extractor.build_success_response(fields), status=200)
