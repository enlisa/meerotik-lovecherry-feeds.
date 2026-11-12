import csv, io, sys, urllib.request
from pathlib import Path

PRODUCT_URL = "https://www.lovecherry.es/modules/imaxexportcsv/export/product_gb_Njc=.csv"
STOCK_URL = "https://www.lovecherry.es/modules/imaxexportcsv/export/stock.csv"
OUT = Path("public/feeds")
MAX_IMAGES = 10

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "MeerotikFeed/1.0"})
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read()

def rows_from(raw):
    text = raw.decode("cp1252", errors="replace").lstrip("\\ufeff")
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";")
    if not reader.fieldnames:
        raise RuntimeError("CSV has no header")
    return list(reader), list(reader.fieldnames)

def write_feed(path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def main():
    products, headers = rows_from(fetch(PRODUCT_URL))
    required = {"nombre", "reference", "stock", "PVPR Externo",
                "precioDescuento", "Descripcion Larga", "imagenes"}
    missing = required - set(headers)
    if missing:
        raise RuntimeError("Product schema changed; missing: " + ", ".join(sorted(missing)))
    image_cols = [f"image_{i}" for i in range(1, MAX_IMAGES + 1)]
    out_headers = headers + [c for c in image_cols if c not in headers]
    out_rows, image_count = [], 0
    for row in products:
        urls = [x.strip() for x in (row.get("imagenes") or "").split(",") if x.strip()]
        image_count += len(urls)
        item = dict(row)
        for i, col in enumerate(image_cols):
            item[col] = urls[i] if i < len(urls) else ""
        out_rows.append(item)
    if not out_rows:
        raise RuntimeError("Product feed is empty; refusing to publish")
    write_feed(OUT / "products.csv", out_headers, out_rows)

    stock, stock_headers = rows_from(fetch(STOCK_URL))
    if not {"Referencia", "stock"}.issubset(stock_headers) or not stock:
        raise RuntimeError("Stock feed schema changed or feed is empty")
    write_feed(OUT / "stock.csv", stock_headers, stock)
    print(f"OK: {len(out_rows)} products; {image_count} image URLs; {len(stock)} stock rows")

if __name__ == "__main__":
    main()
