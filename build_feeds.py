#!/usr/bin/env python3
"""Fetch the Lovecherry supplier CSVs and publish them as feeds.

Writes public/feeds/products.csv, public/feeds/stock.csv and a statistics
report public/reports/feed_report.json.

- Supplier CSVs: semicolon delimiter, Windows-1252 encoding.
- All original columns and values are preserved.
- The comma-separated `imagenes` field is split into image_1..image_20
  (the original `imagenes` column is kept unchanged and complete).
- gallery_1..gallery_8: the first 8 VALID, UNIQUE image URLs of the whole
  product (all supplier rows sharing the same idProducto, i.e. all variants).
  Empty, malformed and duplicate URLs are skipped; unused slots stay empty.
- Both feeds are downloaded and validated before anything is written, so a
  failure never leaves a new product feed next to an old stock feed.
"""
import csv
import io
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PRODUCT_URL = "https://www.lovecherry.es/modules/imaxexportcsv/export/product_gb_Njc=.csv"
STOCK_URL = "https://www.lovecherry.es/modules/imaxexportcsv/export/stock.csv"
OUT = Path("public/feeds")
REPORT = Path("public/reports/feed_report.json")

DELIMITER = ";"
MAX_IMAGE_COLUMNS = 20      # image_N: raw split of `imagenes`, kept as before
MAX_GALLERY = 8             # gallery_N: valid, unique, per product (idProducto)
USER_AGENT = "Mozilla/5.0 (compatible; MeerotikFeed/1.1; +https://github.com/enlisa)"
ATTEMPTS = 5
BACKOFF_SECONDS = 5
TIMEOUT_SECONDS = 120
URL_CHECK_BUDGET_SECONDS = 300

# Required columns, compared after normalisation (case, accents, spaces).
PRODUCT_REQUIRED = ["nombre", "reference", "stock", "PVPR Externo",
                    "precioDescuento", "Descripcion Larga", "imagenes"]
STOCK_REQUIRED = ["Referencia", "stock"]

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
_BAD_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f<>\"\\]")


class FeedError(RuntimeError):
    """A problem with the supplier data that should fail the run clearly."""


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------
def fetch(url, attempts=ATTEMPTS):
    """Download url with retries. Returns bytes or raises FeedError."""
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                body = response.read()
            if not body.strip():
                raise FeedError("empty response body")
            if body.lstrip()[:1] == b"<":
                snippet = body[:120].decode("latin-1", "replace").strip()
                raise FeedError("got HTML instead of CSV (blocked or error page?): " + snippet)
            return body
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code} {exc.reason}"
        except urllib.error.URLError as exc:
            last_error = f"network error: {exc.reason}"
        except (TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        except FeedError as exc:
            last_error = str(exc)
        print(f"[fetch] attempt {attempt}/{attempts} failed for {url}: {last_error}",
              file=sys.stderr)
        if attempt < attempts:
            time.sleep(BACKOFF_SECONDS * attempt)
    raise FeedError(f"Could not download {url} after {attempts} attempts: {last_error}")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def decode(raw):
    """Decode supplier bytes as Windows-1252 and drop any BOM."""
    if raw.startswith(b"\xef\xbb\xbf"):          # UTF-8 BOM bytes
        raw = raw[3:]
    try:
        text = raw.decode("cp1252")
    except UnicodeDecodeError:
        # cp1252 leaves a few bytes undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D).
        # latin-1 maps every byte, so nothing is lost or replaced.
        text = raw.decode("latin-1")
    return text.lstrip("﻿")


def normalize(name):
    """Comparison key for a header: no accents/case, single spaces."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().casefold()


def unique_headers(headers):
    """Keep every column, even if the supplier repeats or blanks a name."""
    seen, result = {}, []
    for i, name in enumerate(headers, start=1):
        base = name if name.strip() else f"column_{i}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        result.append(base if count == 1 else f"{base}_{count}")
    return result


def parse_csv(raw, label):
    """Return (headers, rows) with rows as dicts keyed by the original headers."""
    text = decode(raw)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=DELIMITER)
    try:
        header_row = next(reader)
    except StopIteration:
        raise FeedError(f"{label}: file has no header row")
    if len(header_row) < 2:
        raise FeedError(f"{label}: only one column found with delimiter {DELIMITER!r}; "
                        f"first line starts with {header_row[0][:80]!r}")
    headers = unique_headers(header_row)
    rows = []
    for record in reader:
        if not record or all(not cell.strip() for cell in record):
            continue                                  # skip blank lines
        if len(record) < len(headers):
            record = record + [""] * (len(headers) - len(record))
        elif len(record) > len(headers):
            raise FeedError(f"{label}: row {len(rows) + 2} has {len(record)} fields, "
                            f"header has {len(headers)} (delimiter inside an unquoted field?)")
        rows.append(dict(zip(headers, record)))
    return headers, rows


def resolve_columns(headers, required, label):
    """Map each required name to the actual header, tolerant of case/accents/spaces."""
    lookup = {}
    for header in headers:
        lookup.setdefault(normalize(header), header)
    found, missing = {}, []
    for name in required:
        actual = lookup.get(normalize(name))
        if actual is None:
            missing.append(name)
        else:
            found[name] = actual
    if missing:
        raise FeedError(f"{label}: missing columns: {', '.join(missing)}. "
                        f"Columns found: {', '.join(headers)}")
    return found


def find_column(headers, name):
    """Actual header matching name (case/accent/space tolerant) or None."""
    wanted = normalize(name)
    for header in headers:
        if normalize(header) == wanted:
            return header
    return None


# --------------------------------------------------------------------------
# Image handling
# --------------------------------------------------------------------------
def split_images(value):
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def is_valid_image_url(url):
    """Well-formed absolute http(s) URL with a host and a path.

    Only the URL's form is judged here; whether the file can be fetched is
    checked separately (see sample_url_check)."""
    if _BAD_URL_CHARS.search(url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    return (parts.scheme in ("http", "https") and bool(host) and "." in host
            and len(parts.path) > 1)


def build_products(headers, rows, images_header):
    """Add image_1..image_20 (raw split of `imagenes`); never overwrite a column."""
    prefix = "image_"
    if any(f"{prefix}{i}" in headers for i in range(1, MAX_IMAGE_COLUMNS + 1)):
        prefix = "imagen_"                           # avoid clobbering supplier columns
        if any(f"{prefix}{i}" in headers for i in range(1, MAX_IMAGE_COLUMNS + 1)):
            raise FeedError("Supplier already has image_N and imagen_N columns")
    image_cols = [f"{prefix}{i}" for i in range(1, MAX_IMAGE_COLUMNS + 1)]
    out_rows = []
    total = 0
    for row in rows:
        urls = split_images(row.get(images_header, ""))
        total += len(urls)
        item = dict(row)
        for i, col in enumerate(image_cols):
            item[col] = urls[i] if i < len(urls) else ""
        out_rows.append(item)
    return headers + image_cols, out_rows, total


def add_stockeo_columns(headers, rows, images_header, reference_header=None):
    """Add gallery_1..gallery_8 and variant helper columns; returns stats too.

    gallery_N: first 8 valid, unique URLs across ALL rows with the same
        idProducto (the supplier has one row per size/variant and often puts
        the images on only some of them). Every row of a product gets the same
        gallery. Existing columns and values are never changed.
    variant_option_name / variant_option_value: `combinacion` split at the
        first ':' (for example "Size (interno):M/L" -> "Size (interno)", "M/L").
    """
    id_header = find_column(headers, "idProducto")
    combo_header = find_column(headers, "combinacion")
    gallery_cols = [f"gallery_{i}" for i in range(1, MAX_GALLERY + 1)]
    new_cols = gallery_cols + ["variant_option_name", "variant_option_value"]
    clash = [c for c in new_cols if c in headers]
    if clash:
        raise FeedError("Supplier feed already has columns named: " + ", ".join(clash))

    stats = Counter()
    invalid_examples = []
    keys = []
    galleries = {}                       # product key -> ordered {url: None}
    for i, row in enumerate(rows):
        key = (row.get(id_header) or "").strip() if id_header else ""
        key = key or f"row-{i}"          # never merge rows that have no product id
        keys.append(key)
        gallery = galleries.setdefault(key, {})
        seen_in_row = set()
        for url in split_images(row.get(images_header, "")):
            stats["raw_image_urls"] += 1
            if not is_valid_image_url(url):
                stats["invalid_urls"] += 1
                if len(invalid_examples) < 20:
                    ref = row.get(reference_header, "") if reference_header else ""
                    invalid_examples.append({"reference": ref, "url": url[:200]})
                continue
            if url in seen_in_row:
                stats["duplicates_within_row"] += 1
                continue
            seen_in_row.add(url)
            if url in gallery:
                stats["duplicates_across_variant_rows"] += 1
                continue
            gallery[url] = None
            if not url.lower().split("?", 1)[0].endswith(IMAGE_EXTENSIONS):
                stats["valid_urls_without_image_extension"] += 1

    enriched = 0
    for row, key in zip(rows, keys):
        urls = list(galleries[key])[:MAX_GALLERY]
        if len(urls) > len(set(split_images(row.get(images_header, "")))):
            enriched += 1
        for n, col in enumerate(gallery_cols):
            row[col] = urls[n] if n < len(urls) else ""
        combo = (row.get(combo_header, "") if combo_header else "").strip()
        name, sep, value = combo.partition(":")
        row["variant_option_name"] = name.strip() if sep else ""
        row["variant_option_value"] = value.strip() if sep else ""

    histogram = Counter(min(len(g), MAX_GALLERY) for g in galleries.values())
    report = {
        "product_rows": len(rows),
        "distinct_products": len(galleries),
        "products_by_gallery_image_count": {str(n): histogram.get(n, 0)
                                            for n in range(0, MAX_GALLERY + 1)},
        "products_with_more_than_8_valid_unique_images":
            sum(1 for g in galleries.values() if len(g) > MAX_GALLERY),
        "raw_image_urls": stats["raw_image_urls"],
        "invalid_urls": stats["invalid_urls"],
        "invalid_url_examples": invalid_examples,
        "duplicate_urls_removed_total": (stats["duplicates_within_row"]
                                         + stats["duplicates_across_variant_rows"]),
        "duplicates_within_a_single_row": stats["duplicates_within_row"],
        "duplicates_across_variant_rows": stats["duplicates_across_variant_rows"],
        "valid_urls_without_image_extension": stats["valid_urls_without_image_extension"],
        "rows_that_gained_images_from_other_variant_rows": enriched,
    }
    return headers + new_cols, rows, report, galleries


def sample_url_check(galleries, sample_size):
    """Optional reachability check of a random sample of published URLs.

    Runs only when FEEDS_CHECK_URL_SAMPLE > 0 (set in the GitHub workflow,
    because the supplier host is not reachable from every network). Requests
    are sequential and slow to avoid hammering the supplier. Never fails the
    build; the result is written to the report."""
    pool = sorted({u for g in galleries.values() for u in list(g)[:MAX_GALLERY]})
    sample = random.Random(42).sample(pool, min(sample_size, len(pool)))
    codes, bad = Counter(), []
    deadline = time.monotonic() + URL_CHECK_BUDGET_SECONDS
    for url in sample:
        if time.monotonic() > deadline:
            codes["skipped_time_budget"] += 1
            continue
        status, ctype = None, ""
        for method in ("HEAD", "GET"):
            try:
                headers = {"User-Agent": USER_AGENT}
                if method == "GET":
                    headers["Range"] = "bytes=0-0"
                req = urllib.request.Request(url, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=20) as resp:
                    status, ctype = resp.status, resp.headers.get("Content-Type", "")
                break
            except urllib.error.HTTPError as exc:
                status = exc.code
                if method == "HEAD" and exc.code in (403, 405, 501):
                    continue
                break
            except Exception as exc:                  # network problem
                status = type(exc).__name__
                break
        codes[str(status)] += 1
        if status not in (200, 206) or not str(ctype).lower().startswith("image/"):
            if len(bad) < 20:
                bad.append({"url": url, "status": str(status), "content_type": ctype})
        time.sleep(0.25)
    return {"sampled": len(sample), "checked": len(sample) - codes.get("skipped_time_budget", 0),
            "pool_size": len(pool),
            "status_counts": dict(codes), "problems": bad}


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def write_feed(path, headers, rows):
    """Write UTF-8 (with BOM, for Excel/Shopify tools) via a temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def verify_output(path):
    if not path.is_file():
        raise FeedError(f"Output file missing: {path}")
    with path.open(encoding="utf-8-sig", newline="") as f:
        lines = sum(1 for _ in csv.reader(f))
    if lines < 2:
        raise FeedError(f"Output file has no data rows: {path}")
    return lines - 1


def write_report(report):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")


def main():
    product_raw = fetch(PRODUCT_URL)
    stock_raw = fetch(STOCK_URL)

    p_headers, p_rows = parse_csv(product_raw, "products")
    if not p_rows:
        raise FeedError("products: feed is empty; refusing to publish")
    p_cols = resolve_columns(p_headers, PRODUCT_REQUIRED, "products")
    out_headers, out_rows, image_count = build_products(p_headers, p_rows, p_cols["imagenes"])
    out_headers, out_rows, report, galleries = add_stockeo_columns(
        out_headers, out_rows, p_cols["imagenes"], p_cols["reference"])

    s_headers, s_rows = parse_csv(stock_raw, "stock")
    if not s_rows:
        raise FeedError("stock: feed is empty; refusing to publish")
    resolve_columns(s_headers, STOCK_REQUIRED, "stock")

    write_feed(OUT / "products.csv", out_headers, out_rows)
    write_feed(OUT / "stock.csv", s_headers, s_rows)

    n_products = verify_output(OUT / "products.csv")
    n_stock = verify_output(OUT / "stock.csv")

    sample = int(os.environ.get("FEEDS_CHECK_URL_SAMPLE", "0") or 0)
    if sample > 0:
        report["url_sample_check"] = sample_url_check(galleries, sample)
    report["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report["stock_rows"] = n_stock
    write_report(report)

    print(f"OK: {n_products} product rows, {report['distinct_products']} distinct products, "
          f"{image_count} raw image URLs, {n_stock} stock rows")
    print("[gallery] products by number of gallery images:",
          report["products_by_gallery_image_count"])
    print(f"[gallery] invalid URLs: {report['invalid_urls']}, duplicates removed: "
          f"{report['duplicate_urls_removed_total']}")
    if "url_sample_check" in report:
        print("[gallery] URL sample check:", report["url_sample_check"]["status_counts"])


if __name__ == "__main__":
    try:
        main()
    except FeedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
