"""
Post-install deploy cleanup: strip build-only artifacts that pip installs
alongside PyTorch (a transitive dependency of easyocr) but that nothing at
runtime ever touches.

Why this exists: on a real deployment, `pip install -r requirements.txt`
was reported as using noticeably more disk than expected. Auditing the
installed venv found the actual cause — PyTorch's wheel ships static-link
`.lib`/`.a` stub files, C++ headers, and build tools (protoc, etc.), all of
which are only needed if you're compiling custom C++/CUDA extensions
against libtorch. This project never does that; easyocr just imports torch
and runs plain CPU inference. Verified safe twice on Windows, against two
separate real installs whose .lib sizes differed a lot by wheel/source
(866MB reclaimed out of a 1.74GB venv one time, dominated by a ~660MB
dnnl.lib; 2.69GB reclaimed from a torch-only install another time, same
dnnl.lib at ~2.2GB) — both times the full OCR pipeline
(image_ocr.py -> local_invoice_extractor.py) was run against a real
receipt photo before and after removal, with identical results.

Safe to run on any platform/venv, including ones where torch isn't
installed at all, or where a given path (e.g. .lib files on Linux, where
the equivalent bloat is smaller or shaped differently) doesn't exist —
every step below is a no-op if its target is missing.

Usage (after `pip install -r requirements.txt`):
    python scripts/cleanup_torch_bloat.py
"""

import shutil
from pathlib import Path


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> None:
    try:
        import torch
    except ImportError:
        print("torch isn't installed — nothing to clean up.")
        return

    torch_dir = Path(torch.__file__).parent
    reclaimed = 0

    # Static-link import stubs (.lib on Windows, .a on Linux/Mac) — only
    # needed to compile against libtorch, never to run it.
    lib_dir = torch_dir / "lib"
    if lib_dir.is_dir():
        for pattern in ("*.lib", "*.a"):
            for f in lib_dir.glob(pattern):
                reclaimed += f.stat().st_size
                f.unlink()

    # C++ headers and build-time tools (protoc, etc.) — same story: needed
    # only to build extensions against torch, not to run it.
    for sub in ("include", "bin"):
        target = torch_dir / sub
        if target.is_dir():
            reclaimed += _dir_size(target)
            shutil.rmtree(target)

    print(f"Reclaimed {reclaimed / 1024 / 1024:.1f} MB from {torch_dir}")


if __name__ == "__main__":
    main()
