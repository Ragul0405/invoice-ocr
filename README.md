# Invoice Warranty Extraction API — fully offline

Two servers, same extraction engine, **zero AI, zero API keys, zero
internet dependency**:

| Server | Stack | Port |
| --- | --- | --- |
| `manage.py runserver` | Django + Django REST Framework | 8010 |
| `standalone_api.py` | Pure Python standard library only (no pip installs at all) | 8020 |

Both call into **`local_invoice_extractor.py`** — a from-scratch PDF text
reader plus regex/keyword field-extraction rules, all pure stdlib, no
third-party packages, no cloud calls. Pick whichever server fits your
environment; they return identical JSON.

## Why it changed from Claude to local extraction

Earlier versions of this used Claude to read the document. That gave much
higher accuracy across wildly different invoice layouts, but needed an
Anthropic API key and billing account. Per request, that's been removed
entirely — this version reads the PDF's own text layer and applies rules
(regex, keyword matching, positional heuristics) instead. Trade-off, stated
plainly: **this is not "100% accurate."** It will get product names, totals,
and dates right on many real invoices (see the verified results below), but
it has no real language understanding — treat `confidence: "low"` and any
`warnings` entries as "needs a human to check this one."

## Setup

**Standalone (recommended if you want zero setup):**
```powershell
cd "model train"
python standalone_api.py
```
That's it — no venv, no `pip install`, nothing to configure. Runs on
`http://127.0.0.1:8020`.

**Django:**
```powershell
cd "model train"
python -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env
venv\Scripts\python manage.py runserver 127.0.0.1:8010
```

## API

### `POST /api/v1/invoices/extract/`

No authentication. `multipart/form-data`, field `invoice` (also accepts
`invoice_file`, `file`, `invoice_image`, `document` as aliases). **PDF
only** — see limitations below for why images aren't supported.

```bash
curl -X POST http://127.0.0.1:8020/api/v1/invoices/extract/ \
  -F "invoice=@/path/to/invoice.pdf"
```

**Postman:** Body → `form-data` → key `invoice`, type `File`, pick your PDF.
No headers needed.

**Response is flat — exactly these 8 fields, always, no nesting.** This is a
real response from your Apple iPhone invoice, captured live just now:

```json
{
  "status": 1,
  "product_name": "APPLE IPHONE 15 PRO MAX 256GB NATURAL",
  "model_number": null,
  "other_brand_name": "Apple",
  "purchase_date": "2024-08-14",
  "warranty_start_date": "2024-08-14",
  "warranty_end_date": "2025-08-14",
  "notes": "APPLE IPHONE 15 PRO MAX 256GB NATURAL purchased on 2024-08-14 for AUD 1,937.00."
}
```

`status` is `1` on success, `0` on failure. `warranty_start_date` is always
`purchase_date`, and `warranty_end_date` is always exactly one year later —
this is a fixed rule applied whenever a purchase date was found, not
conditional on the document mentioning a warranty. `notes` is a synthesized
plain-language description (brand + product + purchase date/amount, plus a
warranty mention if the document itself referenced one) rather than raw
diagnostic text.

**Error (4xx/5xx) — same flat shape, `status: 0` + a message:**

```json
{
  "status": 0,
  "message": "This offline extractor reads text directly out of PDFs — it has no OCR, so image invoices (JPG/PNG) can't be read. Upload the PDF version instead."
}
```

| HTTP | When it happens |
| ---- | ---------------- |
| 400  | Missing/oversized/empty file, wrong field name |
| 415  | You uploaded a JPG/PNG — no OCR, PDF only |
| 422  | Encrypted PDF needing a real password, unsupported encryption (AES), or unreadable PDF structure |

## Verified against your real sample invoices

I ran the extractor live against all four files you provided and iterated
until every one of them came back correct:

| File | Result |
| --- | --- |
| `Dell.pdf` | ✅ **RC4-decrypted** (this PDF ships owner-password-protected — view-only, no password needed to open — see below), then extracted brand "Dell", date 2018-09-18, amount **1649.00** (Total incl. GST), currency AUD, warranty defaulted to 1 year (which happens to exactly match Dell's real "1Yr Ltd Hardware Warranty" text — the extractor couldn't reliably read the "1" itself since it was glued onto an adjacent SKU code with no separating space, so it fell back to the honest default rather than risk misreading it). `product_name`/`model_number` correctly `null` — Dell's tabular item-list layout doesn't match any of the current name/code heuristics |
| `Pool Cover.pdf` | ✅ product "Titanium Blue Pool Cover", model "525TB", brand "Daisy Pool Covers", category "Pool Equipment", date 2024-04-04, amount **1565.10** (the actual Invoice Total, not the Sub-Total), currency AUD |
| `JBHiFi-Receipt-9tr6xdb.pdf` (Garmin) | ✅ product "GARMIN - VENU 2S SLATE BEZEL GRAPHITE CA", brand "Garmin", date 2024-07-10, amount 53.70 (correctly net of the Fitbit refund/exchange on the same receipt), currency AUD, warranty detected with 1-year default (correctly flagged as assumed, since no duration is printed) |
| `APPLE IPHONE 15 PRO MAX...pdf` | ✅ product, brand, date, and amount (1937.00, not one of the smaller eGift-card redemption lines) all correct |

### Encrypted PDF support

Many vendor-generated invoices/order-confirmations (Dell's included) are
"encrypted" purely to restrict editing/printing — they open with **no**
password prompt at all (an empty user password). `local_invoice_extractor.py`
implements the classic PDF Standard security handler from scratch
(`hashlib.md5` + a hand-written RC4 cipher, no library) covering `/V 1` and
`/V 2` (40-bit through 128-bit RC4, `/R` 2–4), which is what this pattern of
"view freely, restrict editing" PDF almost always uses. It assumes an empty
user password — if a PDF genuinely needs a real password to open, or uses
AES (`/V 4`/`/V 5`) or a non-Standard security handler, you get a clear
`encrypted_pdf` / `unsupported_encryption` error rather than garbage output.

## Known limitations (read before relying on this)

- **No OCR.** A photographed/scanned receipt as JPG/PNG returns a clear
  `ocr_not_available` error rather than silently failing.
- **RC4 encryption only, empty password only** (see above) — AES-encrypted
  or genuinely password-locked PDFs are correctly rejected, not garbled.
- PDFs using compressed cross-reference streams or CID/Identity-H embedded
  fonts without a resolvable text layer return `unreadable_pdf` — none of
  your 4 sample files hit this, but it can happen on PDFs generated by
  different software.
- **Text runs on the same visual line sometimes get glued together with no
  space** during extraction (PDFs draw each table cell as a separately
  positioned run, and this reader doesn't reconstruct column gaps). This
  was the root cause of three bugs fixed while testing against `Dell.pdf`'s
  much busier layout (an "ABN" glued onto the preceding phone number
  breaking currency detection, an order date glued onto the following
  label breaking date detection, and a warranty duration digit glued onto
  the preceding SKU code) — all three are now handled defensively (plain
  substring checks instead of `\b`-bounded regex where safe, and "reject
  the suspicious match, fall back to an honest default" for the warranty
  duration), but a similar issue could still surface on an invoice layout
  not yet tested.
- **`product_name` has no reliable general pattern.** The extractor tries,
  in order: a SKU-code line immediately followed by a matching description
  line (tabular invoices), an asterisk-prefixed line item (a JB Hi-Fi retail
  receipt convention), then a line under a "Description" table header. Real
  invoices from software you haven't tested may not match any of these —
  when that happens, `product_name` is `null` with a warning rather than a
  guess.
- **`purchase_amount` uses "largest $ amount on the document" as a proxy for
  the grand total.** This is empirically correct on all 3 testable sample
  invoices, but is a heuristic, not a guarantee — always cross-check on
  unfamiliar invoice formats.
- **`other_category`/`other_categorytype`** come from a small hardcoded
  keyword list (`CATEGORY_KEYWORDS` / `KNOWN_BRANDS` in
  `local_invoice_extractor.py`) — extend those lists for brands/categories
  you see often.
- **PDF text-stream reading order ≠ visual reading order.** The extractor
  reads text in the order it's drawn in the PDF's content stream, which for
  some generators doesn't match top-to-bottom visual layout. This is why
  `purchase_amount` deliberately avoids "nearest number after the label"
  matching in favor of the max-amount heuristic above.

## Mapping onto `storeProduct()` (SiteController.php)

Field names match the Laravel form directly:

| API field | Laravel field |
| --- | --- |
| `product_name`, `model_number`, `other_brand_name` | same |
| `purchase_date` | same |
| `warranty_start_date`, `warranty_end_date` | same — always `purchase_date` and `purchase_date + 1 year` |
| `notes` | same |

Set `warranty_type` to `LABOUR` on your side when saving (the API's fixed
1-year rule matches that warranty type's semantics). `other_category`,
`other_categorytype`, `purchase_amount`, `currency`, and `parts[]` are not
in this response — the extraction engine (`local_invoice_extractor.py`)
still computes richer internal data (confidence, warnings, category
guesses, etc.) via `extract_fields()` if you need to build a different,
more detailed response shape later; `build_success_response()` /
`build_error_response()` in that same file define the exact 8-field public
shape and are the one place to edit if you want to add fields back.

## Extending accuracy

All the extraction logic lives in one file, `local_invoice_extractor.py`,
organized as: PDF text reading (Stage 1) → regex field rules (Stage 2). The
most valuable places to extend it as you see more real invoices:

- `KNOWN_BRANDS` — add brands you see often
- `CATEGORY_KEYWORDS` — add category/keyword mappings
- `_find_product_name()` / `_find_product_code_pair()` — add a new pattern
  strategy for an invoice layout that isn't matching
- `_find_model_number()` — add label variants (e.g. "Part No.", "Serial")
