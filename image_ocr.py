"""
Image OCR support (HEIC/JPG/PNG/WEBP) for the invoice extraction API.

This is a deliberate, scoped exception to the "no third-party packages"
rule the rest of this project follows — there is no way to decode HEIC
images or perform OCR using only the Python standard library. Everything
else in the project (PDF parsing, RC4 decryption, field-extraction rules)
remains pure stdlib; only this one file, and only the image-upload path,
touches third-party code.

Dependencies (installed in the project venv):
    pillow        - general image decoding (JPG/PNG/WEBP) + the base
                    Image type pillow-heif plugs into
    pillow-heif   - adds .heic/.heif decoding support to Pillow
    pytesseract   - Python wrapper that shells out to the Tesseract OCR
                    engine binary

Tesseract itself is a separate system-level program, NOT a Python
package — pytesseract just calls it as a subprocess. It must be
installed independently; see TESSERACT_INSTALL_HELP below.

Output feeds into the exact same text-based field-extraction pipeline
used for PDFs (local_invoice_extractor.extract_fields) — OCR's only job
is to turn a photo into the same kind of plain text a PDF's text layer
already provides.
"""

import io
import logging
import os
import shutil

logger = logging.getLogger("image_ocr")

TESSERACT_INSTALL_HELP = (
    "OCR requires the Tesseract engine, which is a separate program (not "
    "a Python package). Install it with:\n"
    "  winget install --id UB-Mannheim.TesseractOCR\n"
    "(run from an elevated/Administrator terminal), then restart this "
    "server. Or download the installer directly from "
    "https://github.com/UB-Mannheim/tesseract/wiki"
)

SUPPORTED_IMAGE_TYPES = {
    "image/heic": "heic",
    "image/heif": "heic",
    "image/jpeg": "raster",
    "image/jpg": "raster",
    "image/png": "raster",
    "image/webp": "raster",
}


class OcrError(Exception):
    code = "ocr_failed"

    def __init__(self, message, code=None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class TesseractNotInstalledError(OcrError):
    code = "ocr_engine_not_installed"


class ImageDecodeError(OcrError):
    code = "image_decode_failed"


_heif_registered = False


def _ensure_heif_support():
    global _heif_registered
    if _heif_registered:
        return
    import pillow_heif

    pillow_heif.register_heif_opener()
    _heif_registered = True


def _find_tesseract_binary():
    """Locate the Tesseract executable. pytesseract defaults to expecting
    it on PATH; on Windows it's commonly installed outside PATH, so also
    check the standard install locations before giving up."""
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def extract_text_from_image(file_bytes: bytes, content_type: str) -> str:
    """OCR an image (HEIC/JPG/PNG/WEBP) into plain text. Raises an
    OcrError subclass on failure; never returns garbage silently."""
    kind = SUPPORTED_IMAGE_TYPES.get((content_type or "").lower())
    if kind is None:
        raise ImageDecodeError(f"Unsupported image type for OCR: '{content_type}'.")

    if kind == "heic":
        try:
            _ensure_heif_support()
        except Exception as exc:  # noqa: BLE001
            raise ImageDecodeError(f"Could not load HEIC decoder: {exc}") from exc

    from PIL import Image

    try:
        image = Image.open(io.BytesIO(file_bytes))
        image.load()
        # pytesseract inspects PIL's `.format` tag to decide how to hand
        # the image to the Tesseract subprocess, and doesn't recognize
        # "HEIF" (what pillow-heif tags a decoded HEIC/HEIF image with)
        # even though the underlying pixel data is perfectly normal RGB —
        # it fails with a bare "Unsupported image format/type" TypeError.
        # .convert("RGB") produces a fresh Image with format=None, which
        # sidesteps that check entirely; also normalizes away any alpha
        # channel, which OCR doesn't need anyway.
        image = image.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise ImageDecodeError(f"Could not decode image file: {exc}") from exc

    tesseract_path = _find_tesseract_binary()
    if tesseract_path is None:
        raise TesseractNotInstalledError(TESSERACT_INSTALL_HELP)

    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = tesseract_path

    try:
        text = pytesseract.image_to_string(image)
    except pytesseract.TesseractNotFoundError as exc:
        raise TesseractNotInstalledError(TESSERACT_INSTALL_HELP) from exc
    except Exception as exc:  # noqa: BLE001
        raise OcrError(f"OCR failed: {exc}") from exc

    return text.strip()
