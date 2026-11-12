#!/usr/bin/env python3
"""Fetch the Lovecherry supplier CSVs and publish them as feeds.

Writes public/feeds/products.csv and public/feeds/stock.csv.

- Supplier CSVs: semicolon delimiter, Windows-1252 encoding.
- All original columns and values are preserved.
- The comma-separated `imagenes` field is split into image_1, image_2, ...
  (the original `imagenes` column is kept unchanged).
- Both feeds are downloaded and validated before anything is written, so a
  failure never leaves a new product feed next to an old stock feed.
"""
import csv
import io
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

PRODUCT_URL = "https://www.lovecherry.es/modules/imaxexportcsv/export/product_gb_Njc=.csv"
STOCK_URL = "https://www.lovecherry.es/modules/imaxexportcsv/export/stock.csv"
OUT = Path("public/feeds")

DELIMITER = ";"
MAX_IMAGE_COLUMNS = 20
USER_AGENT = "Mozilla/5.0 (compatible; MeerotikFeed/1.1; +https://github.com/enlisa)"
ATTEMPTS = 5
BACKOFF_SECONDS = 5
TIMEOUT_SECONDS = 120

# Required columns, compared after normalisation (case, accents, spaces).
PRODUCT_REQUIRED = ["nombre", "reference", "stock", "PVPR Externo",
                    "precioDescuento", "Descripcion Larga", "imagenes"]
STOCK_REQUIRED = ["Referencia", "stock"]


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


# --------------------------------------------------------------------------
# Transformation
# --------------------------------------------------------------------------
def split_images(value):
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def build_products(headers, rows, images_header):
    """Add image_1..image_N columns; never overwrite an existing column."""
    split = [split_images(row.get(images_header, "")) for row in rows]
    # Only the extra image_N columns are capped; the original `imagenes`
    # column keeps the complete, unchanged list of URLs.
    # The column count is fixed so the Stockeo field mapping never breaks when
    # the supplier data changes.
    width = MAX_IMAGE_COLUMNS
    prefix = "image_"
    if any(f"{prefix}{i}" in headers for i in range(1, width + 1)):
        prefix = "imagen_"                           # avoid clobbering supplier columns
        if any(f"{prefix}{i}" in headers for i in range(1, width + 1)):
            raise FeedError("Supplier already has image_N and imagen_N columns")
    image_cols = [f"{prefix}{i}" for i in range(1, width + 1)]
    out_rows = []
    for row, urls in zip(rows, split):
        item = dict(row)
        for i, col in enumerate(image_cols):
            item[col] = urls[i] if i < len(urls) else ""
        out_rows.append(item)
    total = sum(len(u) for u in split)
    beyond = sum(max(len(u) - MAX_IMAGE_COLUMNS, 0) for u in split)
    if beyond:
        print(f"[images] {beyond} URLs beyond image_{MAX_IMAGE_COLUMNS} are only kept in "
              f"the original 'imagenes' column")
    return headers + image_cols, out_rows, total


def find_column(headers, name):
    """Actual header matching name (case/accent/space tolerant) or None."""
    wanted = normalize(name)
    for header in headers:
        if normalize(header) == wanted:
            return header
    return None


def add_stockeo_columns(headers, rows, images_header):
    """Add helper columns for Stockeo; no existing column or value is changed.

    gallery_1..gallery_20: the combined, de-duplicated images of ALL rows that
        share the same idProducto (one row per size/variant in the supplier
        feed, and the images are often only on some of those rows).
    variant_option_name / variant_option_value: `combinacion` split at the
        first ':' (for example "Size (interno):M/L" -> "Size (interno)", "M/L").
    """
    id_header = find_column(headers, "idProducto")
    combo_header = find_column(headers, "combinacion")
    gallery_cols = [f"gallery_{i}" for i in range(1, MAX_IMAGE_COLUMNS + 1)]
    new_cols = gallery_cols + ["variant_option_name", "variant_option_value"]
    clash = [c for c in new_cols if c in headers]
    if clash:
        raise FeedError("Supplier feed already has columns named: " + ", ".join(clash))

    galleries = {}
    for i, row in enumerate(rows):
        key = row[id_header] if id_header else f"row-{i}"
        seen = galleries.setdefault(key, [])
        for url in split_images(row.get(images_header, "")):
            if url not in seen:
                seen.append(url)

    improved = 0
    for i, row in enumerate(rows):
        key = row[id_header] if id_header else f"row-{i}"
        urls = galleries[key]
        if len(urls) > len(split_images(row.get(images_header, ""))):
            improved += 1
        for n, col in enumerate(gallery_cols):
            row[col] = urls[n] if n < len(urls) else ""
        combo = (row.get(combo_header, "") if combo_header else "").strip()
        name, sep, value = combo.partition(":")
        row["variant_option_name"] = name.strip() if sep else ""
        row["variant_option_value"] = value.strip() if sep else ""
    print(f"[gallery] {improved} rows received additional images from other rows "
          f"of the same product")
    return headers + new_cols, rows


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


def write_debug_sample(product_raw, stock_raw):
    """TEMPORARY: keep the first bytes of the untouched supplier files so the
    raw values can be compared with the generated feeds. Enabled only via the
    FEEDS_DEBUG_RAW_BYTES environment variable."""
    n = int(os.environ.get("FEEDS_DEBUG_RAW_BYTES", "0") or 0)
    if n <= 0:
        return
    debug = OUT.parent / "debug"
    debug.mkdir(parents=True, exist_ok=True)
    (debug / "products_raw_head.csv").write_bytes(product_raw[:n])
    (debug / "stock_raw_head.csv").write_bytes(stock_raw[:n])


def main():
    product_raw = fetch(PRODUCT_URL)
    stock_raw = fetch(STOCK_URL)
    write_debug_sample(product_raw, stock_raw)

    p_headers, p_rows = parse_csv(product_raw, "products")
    if not p_rows:
        raise FeedError("products: feed is empty; refusing to publish")
    p_cols = resolve_columns(p_headers, PRODUCT_REQUIRED, "products")
    out_headers, out_rows, image_count = build_products(p_headers, p_rows, p_cols["imagenes"])
    out_headers, out_rows = add_stockeo_columns(out_headers, out_rows, p_cols["imagenes"])

    s_headers, s_rows = parse_csv(stock_raw, "stock")
    if not s_rows:
        raise FeedError("stock: feed is empty; refusing to publish")
    resolve_columns(s_headers, STOCK_REQUIRED, "stock")

    write_feed(OUT / "products.csv", out_headers, out_rows)
    write_feed(OUT / "stock.csv", s_headers, s_rows)

    n_products = verify_output(OUT / "products.csv")
    n_stock = verify_output(OUT / "stock.csv")
    print(f"OK: {n_products} products; {image_count} image URLs "
          f"({len(out_headers) - len(p_headers)} added columns); {n_stock} stock rows")


if __name__ == "__main__":
    try:
        main()
    except FeedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
