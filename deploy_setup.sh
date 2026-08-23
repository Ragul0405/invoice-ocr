#!/usr/bin/env bash
# Installs project dependencies for deployment (e.g. on an AWS server),
# minimizing disk use in two ways:
#
#   1. Installs torch AND torchvision (easyocr's dependencies) together
#      from PyTorch's dedicated CPU-only wheel index instead of plain
#      PyPI, since PyPI's default torch build on many platforms bundles
#      CUDA runtime libraries this project never uses (OCR runs on CPU —
#      see image_ocr.py). Installing both from the same index in the same
#      command matters: a real deploy that installed torch from this index
#      but let torchvision come from plain PyPI (via requirements.txt)
#      got a version/ABI mismatch —
#      `RuntimeError: operator torchvision::nms does not exist` — because
#      the two weren't built as a matched pair. This step runs BEFORE
#      `pip install -r requirements.txt` so that when pip resolves
#      easyocr's torch/torchvision dependencies, this matched CPU pair
#      already satisfies them.
#   2. Runs scripts/cleanup_torch_bloat.py afterward, which strips
#      build-only artifacts (static-link .lib/.a stubs, C++ headers) that
#      pip installs but nothing at runtime touches. This is the step that
#      actually guarantees a small footprint: verified on Windows across
#      two separate real installs to reclaim 866MB and 2.69GB respectively
#      (installed .lib size varies by wheel/platform) with torch and the
#      full OCR pipeline confirmed working identically before and after
#      — see that script's docstring for details.
#
# Usage:
#   ./deploy_setup.sh

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

python3 -m venv venv
source venv/bin/activate

python -m pip install --upgrade pip
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt

python scripts/cleanup_torch_bloat.py

echo "Setup complete. Activate with: source venv/bin/activate"
