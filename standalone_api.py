"""
Invoice Warranty Extraction API — pure standard library server, plus one
scoped exception for photos.

PDFs are read by a from-scratch text reader (local_invoice_extractor.py)
— pure stdlib, no third-party packages, no cloud calls. Photos
(HEIC/JPG/PNG/WEBP) are read via image_ocr.py — the ONE part of this
project that uses third-party packages (Pillow, pillow-heif, easyocr),
because there is no way to decode a camera image format or recognize
text in a photo using only the Python standard library. OCR itself runs
fully locally (no cloud account, no API key) via EasyOCR — see
image_ocr.OCR_INSTALL_HELP. Requires `pip install pillow pillow-heif
easyocr` — the server still starts and PDF extraction still works with
none of that present; only image uploads need it.

Usage:
    python standalone_api.py

Endpoints:
    GET  /api/v1/health/
    POST /api/v1/invoices/extract/      multipart/form-data, field "invoice"
                                         (PDF, or HEIC/JPG/PNG/WEBP via OCR)

No authentication — open endpoint, per request.
"""

import cgi
import json
import logging
import mimetypes
import os
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

import local_invoice_extractor as extractor

try:
    import image_ocr
except ImportError:
    image_ocr = None

# --------------------------------------------------------------------------
# Config — loaded from .env (no python-dotenv; parsed by hand). Only
# server-level settings remain now that there's no API to key.
# --------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")


def load_env_file(path):
    values = {}
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


load_env_file(ENV_PATH)

HOST = os.environ.get("STANDALONE_HOST", "127.0.0.1")
PORT = int(os.environ.get("STANDALONE_PORT", "8020"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "15"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("standalone_api")

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
FIELD_ALIASES = ("invoice", "invoice_file", "file", "invoice_image", "document")
_IMAGE_CONTENT_TYPES = {"image/heic", "image/heif", "image/jpeg", "image/jpg", "image/png", "image/webp"}
# mimetypes.guess_type() doesn't know .heic/.heif (a newer format).
_EXTENSION_CONTENT_TYPE_OVERRIDES = {".heic": "image/heic", ".heif": "image/heif"}


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "InvoiceExtractionAPI/2.0-offline"

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/api/v1/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, extractor.build_error_response("Unknown endpoint."))

    def do_POST(self):
        if self.path.rstrip("/") != "/api/v1/invoices/extract":
            self._send_json(404, extractor.build_error_response("Unknown endpoint."))
            return
        self._handle_extract()

    def _handle_extract(self):
        request_id = uuid.uuid4().hex[:12]

        content_type_header = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type_header:
            self._send_json(400, extractor.build_error_response("Expected multipart/form-data with a file field."))
            return

        content_length = int(self.headers.get("Content-Length", 0))
        max_bytes = MAX_UPLOAD_MB * 1024 * 1024
        if content_length > max_bytes:
            self._send_json(400, extractor.build_error_response(f"Request body exceeds {MAX_UPLOAD_MB}MB limit."))
            return
        if content_length == 0:
            self._send_json(400, extractor.build_error_response("Empty request body."))
            return

        try:
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type_header,
                    "CONTENT_LENGTH": str(content_length),
                },
            )
        except Exception as exc:  # noqa: BLE001 - surface as a client error, not a 500
            logger.warning("[%s] multipart parse failed: %s", request_id, exc)
            self._send_json(400, extractor.build_error_response("Could not parse multipart form data."))
            return

        field_item = None
        for alias in FIELD_ALIASES:
            if alias in form and form[alias].filename:
                field_item = form[alias]
                break

        if field_item is None:
            self._send_json(400, extractor.build_error_response("No file found in field 'invoice'."))
            return

        file_bytes = field_item.file.read() if hasattr(field_item, "file") else field_item.value
        filename = field_item.filename or "upload"
        ext = os.path.splitext(filename)[1].lower()

        if ext not in ALLOWED_EXTENSIONS:
            self._send_json(400, extractor.build_error_response(
                f"Unsupported file extension '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
            ))
            return
        if not file_bytes:
            self._send_json(400, extractor.build_error_response("Uploaded file is empty."))
            return

        content_type = (getattr(field_item, "type", None) or "").lower()
        resolved_type = _EXTENSION_CONTENT_TYPE_OVERRIDES.get(ext) or content_type or (mimetypes.guess_type(filename)[0] or "").lower()

        logger.info("[%s] extracting: name=%r size=%dKB type=%s", request_id, filename, len(file_bytes) // 1024, resolved_type)

        if ext == ".pdf":
            try:
                text = extractor.extract_pdf_text(file_bytes)
            except extractor.PdfExtractionError as exc:
                logger.warning("[%s] PDF extraction failed: [%s] %s", request_id, exc.code, exc.message)
                self._send_json(422, extractor.build_error_response(exc.message))
                return
        else:
            if image_ocr is None:
                self._send_json(503, extractor.build_error_response(
                    "OCR support isn't installed on this server. Run: "
                    "pip install pillow pillow-heif easyocr"
                ))
                return
            try:
                text = image_ocr.extract_text_from_image(file_bytes, resolved_type)
                extractor.check_invoice_signals(text)
            except image_ocr.OcrError as exc:
                logger.warning("[%s] OCR failed: [%s] %s", request_id, exc.code, exc.message)
                status_code = 503 if exc.code == "ocr_engine_not_installed" else 422
                self._send_json(status_code, extractor.build_error_response(exc.message))
                return
            except extractor.NotAnInvoiceError as exc:
                logger.warning("[%s] OCR'd image not invoice-shaped: %s", request_id, exc.message)
                self._send_json(422, extractor.build_error_response(exc.message))
                return

        fields = extractor.extract_fields(text)
        logger.info(
            "[%s] extraction ok (confidence=%s, warnings=%d)",
            request_id, fields.get("confidence"), len(fields.get("warnings") or []),
        )

        self._send_json(200, extractor.build_success_response(fields))


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    logger.info("Listening on http://%s:%d (no AI, no API key)", HOST, PORT)
    if image_ocr is None:
        logger.warning(
            "OCR packages not installed — PDF uploads work, but "
            "HEIC/JPG/PNG/WEBP uploads will return 503. Run: "
            "pip install pillow pillow-heif easyocr"
        )
    logger.info("  GET  /api/v1/health/")
    logger.info("  POST /api/v1/invoices/extract/  (multipart field: invoice; PDF, or HEIC/JPG/PNG/WEBP via OCR)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
