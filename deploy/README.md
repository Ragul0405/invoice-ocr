# Deploying to an EC2 instance

Steps to stand this API up on a fresh Amazon Linux 2023 EC2 instance,
written down after a real deploy hit two gotchas worth avoiding next time
(both explained inline below).

## 1. Launch the instance

- AMI: Amazon Linux 2023 (x86_64)
- **Instance size: t3.small (2GB RAM) minimum.** A t2/t3.micro (1GB) is
  not enough — see the OOM note in step 5.
- Security group: allow inbound TCP 22 (SSH) and 80 (HTTP)
- Attach an SSH key pair

## 2. Install the app

```bash
sudo dnf install -y git python3.11 python3.11-pip nginx
git clone https://github.com/Ragul0405/invoice-ocr.git app
cd app
./deploy_setup.sh
```

`deploy_setup.sh` creates the venv, installs a matched CPU-only
torch+torchvision pair, installs the rest of `requirements.txt`, and
strips torch's unused build artifacts (see
`scripts/cleanup_torch_bloat.py`).

**Gotcha #1 — torch/torchvision must come from the same index, together.**
Installing `torch` alone from PyTorch's CPU wheel index and then letting
`torchvision` resolve from plain PyPI (e.g. via `requirements.txt`) gives
two builds that aren't ABI-compatible:
`RuntimeError: operator torchvision::nms does not exist`.
`deploy_setup.sh` already installs both together in one command — don't
split that into two separate `pip install` calls.

## 3. Pre-download the OCR model weights

```bash
source venv/bin/activate
python -c "import easyocr; easyocr.Reader(['en'])"
```

Do this once during setup, not on the first real request — otherwise
that first request stalls for however long the ~65MB download takes,
which can exceed a reverse proxy's default timeout.

## 4. Configure the app

```bash
cat > .env <<EOF
DJANGO_SECRET_KEY=$(openssl rand -hex 32)
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=*
MAX_UPLOAD_MB=15
EOF
```

Narrow `DJANGO_ALLOWED_HOSTS` to your actual domain/IP once you have one
instead of leaving it as `*`.

## 5. Add swap, then start gunicorn + nginx

**Gotcha #2 — one gunicorn worker per this instance size.** Each gunicorn
worker process loads its *own* independent copy of torch + EasyOCR's
models (nothing is shared/forked from a common preloaded state). Two
workers on a t3.small's 2GB RAM got a worker OOM-killed mid-request
during a real deploy. Two mitigations, both worth doing:

```bash
# Swap as a safety margin against memory spikes
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab
```

```bash
sudo cp deploy/invoice-ocr.service /etc/systemd/system/invoice-ocr.service
sudo systemctl daemon-reload
sudo systemctl enable --now invoice-ocr

sudo cp deploy/nginx.conf /etc/nginx/conf.d/invoice-ocr.conf
sudo nginx -t
sudo systemctl enable --now nginx
```

`deploy/invoice-ocr.service` is already set to `--workers 1` for exactly
this reason — see the comment in that file before raising it, and only
raise it alongside a bigger instance type (t3.medium/4GB+ gives real
headroom for 2+ workers).

## 6. Verify

```bash
curl http://<public-ip>/api/v1/health/
curl -X POST http://<public-ip>/api/v1/invoices/extract/ -F "invoice=@/path/to/receipt.jpg"
```
