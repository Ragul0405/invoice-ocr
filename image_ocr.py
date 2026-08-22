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
    easyocr       - self-contained OCR engine (bundles its own text
                    detection/recognition models — no separate system
                    binary, no cloud account, no API key)

OCR runs fully locally via EasyOCR — no Tesseract-style system binary to
install (that was the original deployment problem: a separately installed
native program, off PATH by default, different per OS), and no cloud
service/AWS account either (no credentials to provision, no per-request
network dependency, no bill). The one thing EasyOCR needs that isn't
fully "offline": the first time it runs on a machine, it downloads its
recognition model weights (~65MB) to a local cache directory
(~/.EasyOCR by default) — after that first download, every OCR call is
local CPU inference with no network involved at all. To pre-populate
that cache during deployment instead of on first request, run once
during your build/setup step:
    python -c "import easyocr; easyocr.Reader(['en'])"

Output feeds into the exact same text-based field-extraction pipeline
used for PDFs (local_invoice_extractor.extract_fields) — OCR's only job
is to turn a photo into the same kind of plain text a PDF's text layer
already provides.
"""

import io
import logging

logger = logging.getLogger("image_ocr")

OCR_INSTALL_HELP = (
    "OCR requires the easyocr package (and its dependencies, including "
    "PyTorch). Install it with:\n"
    "  pip install easyocr\n"
    "The first OCR call after installing will download ~65MB of model "
    "weights to a local cache (~/.EasyOCR) — that needs one-time internet "
    "access; every call after that runs fully offline."
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


class OcrEngineNotInstalledError(OcrError):
    """Raised when easyocr (or a dependency, e.g. torch) isn't installed.
    Kept as code 'ocr_engine_not_installed' — callers (the Django view and
    standalone_api.py) key off that exact string to return HTTP 503
    instead of 422, the same behavior as the old
    TesseractNotInstalledError this replaces."""

    code = "ocr_engine_not_installed"


class ImageDecodeError(OcrError):
    code = "image_decode_failed"


_heif_registered = False
_reader = None


def _ensure_heif_support():
    global _heif_registered
    if _heif_registered:
        return
    import pillow_heif

    pillow_heif.register_heif_opener()
    _heif_registered = True


def _get_reader():
    """Lazily create (and cache) the EasyOCR Reader — it loads model
    weights into memory, which is too expensive to redo per request."""
    global _reader
    if _reader is not None:
        return _reader

    try:
        import easyocr
    except ImportError as exc:
        raise OcrEngineNotInstalledError(OCR_INSTALL_HELP) from exc

    try:
        _reader = easyocr.Reader(["en"], gpu=False)
    except Exception as exc:  # noqa: BLE001 - e.g. no internet for first-run model download
        raise OcrEngineNotInstalledError(
            f"Could not initialize the EasyOCR engine: {exc}\n\n{OCR_INSTALL_HELP}"
        ) from exc
    return _reader


def _decode_to_image_array(file_bytes: bytes, kind: str):
    """Decode the uploaded image (HEIC/JPG/PNG/WEBP) with Pillow into an
    RGB numpy array — EasyOCR's reader accepts either a file path or a
    numpy array directly."""
    if kind == "heic":
        try:
            _ensure_heif_support()
        except Exception as exc:  # noqa: BLE001
            raise ImageDecodeError(f"Could not load HEIC decoder: {exc}") from exc

    from PIL import Image
    import numpy as np

    try:
        image = Image.open(io.BytesIO(file_bytes))
        image.load()
        image = image.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise ImageDecodeError(f"Could not decode image file: {exc}") from exc

    return np.array(image)


def extract_text_from_image(file_bytes: bytes, content_type: str) -> str:
    """OCR an image (HEIC/JPG/PNG/WEBP) into plain text via a local
    EasyOCR engine. Raises an OcrError subclass on failure; never returns
    garbage silently."""
    kind = SUPPORTED_IMAGE_TYPES.get((content_type or "").lower())
    if kind is None:
        raise ImageDecodeError(f"Unsupported image type for OCR: '{content_type}'.")

    image_array = _decode_to_image_array(file_bytes, kind)
    reader = _get_reader()

    try:
        # paragraph=False (the default) keeps each detected text line as its
        # own list entry — local_invoice_extractor.py's field-extraction
        # heuristics (e.g. _find_next_line_total, _find_product_code_pair)
        # depend on real line breaks between adjacent fields, which
        # paragraph=True would merge away into single space-joined blobs.
        lines = reader.readtext(image_array, detail=0)
    except Exception as exc:  # noqa: BLE001
        raise OcrError(f"OCR failed: {exc}") from exc

    return "\n".join(lines).strip()
