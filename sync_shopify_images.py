#!/usr/bin/env python3
"""Add the missing Lovecherry gallery images to EXISTING Shopify products.

Only the image gallery is touched. Titles, prices, SKUs, descriptions, stock,
variants and publication status are never modified, and no working image is
ever deleted.

How it works (idempotent: running it again only does what is still missing)
1. Reads public/feeds/products.csv (gallery_1..gallery_8 per idProducto).
2. Exports all Shopify products with their variant SKUs and media through a
   bulk operation (fast, no per-product reads).
3. Matches a feed product to a Shopify product by SKU. A product is matched
   only if it is unambiguous; otherwise it is skipped and reported:
     ambiguous_reference      the SKU is used by several feed products
     no_match                 none of its SKUs exists in Shopify
     multiple_shopify_products its SKUs are spread over several Shopify products
     mixed_product            the Shopify product also holds SKUs of other feed products
4. Duplicate check: Shopify keeps the original bytes of an uploaded image, so
   the byte size of each existing READY image (originalSource.fileSize) is
   compared with the Content-Length of the supplier URL. Matching sizes mean
   the image is already there and is not uploaded again.
5. Missing images are added in small batches, verified, retried one by one,
   and failed uploads created by this run are removed again (never any image
   that was working or that existed before).

Dry run is the default; pass --apply to write.
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

API_VERSION = "2025-01"
BATCH_URLS = 4                 # images per productUpdate call
CHUNK_PRODUCTS = 10            # products submitted, then verified together
PAUSE_BETWEEN_CALLS = 0.7      # seconds, keeps the supplier host happy
POLL_SECONDS = 4
POLL_TIMEOUT = 150
USER_AGENT = "Mozilla/5.0 (compatible; MeerotikImageSync/1.0)"
GALLERY_COLUMNS = [f"gallery_{i}" for i in range(1, 9)]


class ApiError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Shopify GraphQL client
# --------------------------------------------------------------------------
class Shopify:
    def __init__(self, domain, token=None, base=None, client_id=None, client_secret=None):
        root = base or f"https://{domain}"
        self.root = root
        self.url = f"{root}/admin/api/{API_VERSION}/graphql.json"
        self.token = token
        self._cid, self._secret = client_id, client_secret
        if not self.token:
            self.refresh_token()

    def refresh_token(self):
        """Dev Dashboard apps: client-credentials grant (token valid 24 h)."""
        if not (self._cid and self._secret):
            raise ApiError("401 and no client credentials to refresh the token")
        data = urllib.parse.urlencode({"grant_type": "client_credentials",
                                       "client_id": self._cid,
                                       "client_secret": self._secret}).encode()
        req = urllib.request.Request(f"{self.root}/admin/oauth/access_token", data=data, headers={
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                self.token = json.load(resp)["access_token"]
        except urllib.error.HTTPError as exc:      # never print the body of a credentials call
            raise ApiError(f"token request failed: HTTP {exc.code} (check client id/secret, "
                           "that the app is installed on the store and in the same organization)")

    def gql(self, query, variables=None, retries=6):
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        last = "unknown"
        for attempt in range(retries):
            req = urllib.request.Request(self.url, data=body, headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": self.token})
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.load(resp)
            except urllib.error.HTTPError as exc:
                last = f"HTTP {exc.code}"
                if exc.code in (429, 500, 502, 503, 504):
                    time.sleep(min(2 ** attempt, 30))
                    continue
                if exc.code == 401 and self._cid and attempt < retries - 1:
                    self.refresh_token()
                    continue
                raise ApiError(f"HTTP {exc.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = f"network: {exc}"
                time.sleep(min(2 ** attempt, 30))
                continue
            errors = data.get("errors")
            if errors:
                if "THROTTLED" in json.dumps(errors):
                    time.sleep(2 + attempt * 2)
                    last = "throttled"
                    continue
                raise ApiError(json.dumps(errors)[:600])
            cost = (data.get("extensions") or {}).get("cost", {}).get("throttleStatus", {})
            if cost.get("currentlyAvailable", 1000) < 150:
                time.sleep(2)
            return data["data"]
        raise ApiError(f"giving up after {retries} attempts ({last})")


EXPORT_QUERY = """{ products { edges { node { id title status
  variants { edges { node { id sku } } }
  media { edges { node { id status ... on MediaImage { originalSource { fileSize } } } } } } } } }"""


def export_products(shop):
    """Bulk-export products. Returns {product_gid: {...}} and {sku: {product_gid}}."""
    while True:
        data = shop.gql("mutation($q: String!) { bulkOperationRunQuery(query: $q) "
                        "{ bulkOperation { id status } userErrors { field message } } }",
                        {"q": EXPORT_QUERY})["bulkOperationRunQuery"]
        errors = data.get("userErrors") or []
        if not errors:
            break
        if any("already in progress" in e["message"].lower() for e in errors):
            print("[export] another bulk operation is running, waiting...")
            time.sleep(20)
            continue
        raise ApiError(f"bulk export refused: {errors}")
    url = None
    for _ in range(720):                                   # up to ~1 hour
        op = shop.gql("{ currentBulkOperation(type: QUERY) { id status errorCode "
                      "objectCount url } }")["currentBulkOperation"]
        if op and op["status"] == "COMPLETED":
            url = op.get("url")
            break
        if op and op["status"] in ("FAILED", "CANCELED", "EXPIRED"):
            raise ApiError(f"bulk export {op['status']}: {op.get('errorCode')}")
        time.sleep(5)
    products, sku_index = {}, defaultdict(set)
    if url:                                                # no URL = empty shop
        with urllib.request.urlopen(url, timeout=300) as resp:
            for line in resp:
                if not line.strip():
                    continue
                node = json.loads(line)
                gid, parent = node.get("id", ""), node.get("__parentId")
                if parent is None and "/Product/" in gid:
                    products[gid] = {"id": gid, "title": node.get("title", ""),
                                     "status": node.get("status"), "skus": set(), "media": []}
                elif parent in products and "/ProductVariant/" in gid:
                    sku = (node.get("sku") or "").strip()
                    if sku:
                        products[parent]["skus"].add(sku)
                        sku_index[sku].add(parent)
                elif parent in products and "/MediaImage/" in gid:
                    products[parent]["media"].append({
                        "id": gid, "status": node.get("status"),
                        "size": (node.get("originalSource") or {}).get("fileSize")})
    return products, sku_index


# --------------------------------------------------------------------------
# Supplier image sizes (duplicate fingerprint)
# --------------------------------------------------------------------------
_size_cache = {}


def source_size(url):
    """('ok', bytes) | ('dead', http_code) | ('unknown', reason) for a supplier URL."""
    if url in _size_cache:
        return _size_cache[url]
    result = ("unknown", "no response")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                length = resp.headers.get("Content-Length")
                if length and length.isdigit():
                    result = ("ok", int(length))
                    break
            raise urllib.error.HTTPError(url, 405, "no length", {}, None)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                result = ("dead", exc.code)
                break
            if exc.code in (403, 405, 501) or exc.code == 405:
                try:                                       # fall back to a 1-byte GET
                    req = urllib.request.Request(url, headers={
                        "User-Agent": USER_AGENT, "Range": "bytes=0-0"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        crange = resp.headers.get("Content-Range", "")
                        total = crange.rsplit("/", 1)[-1]
                        if total.isdigit():
                            result = ("ok", int(total))
                            break
                        result = ("unknown", "no size in GET response")
                except urllib.error.HTTPError as exc2:
                    result = ("dead", exc2.code) if exc2.code in (404, 410) else ("unknown", f"HTTP {exc2.code}")
                    if result[0] == "dead":
                        break
                except Exception as exc2:
                    result = ("unknown", type(exc2).__name__)
            else:
                result = ("unknown", f"HTTP {exc.code}")
            time.sleep(1 + attempt)
        except Exception as exc:
            result = ("unknown", type(exc).__name__)
            time.sleep(1 + attempt)
    _size_cache[url] = result
    return result


# --------------------------------------------------------------------------
# Feed, matching, planning
# --------------------------------------------------------------------------
def load_feed(path):
    groups = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            pid = (row.get("idProducto") or "").strip() or f"row-{row['reference']}"
            g = groups.setdefault(pid, {"id": pid, "refs": set(), "gallery": []})
            ref = (row.get("reference") or "").strip()
            if ref:
                g["refs"].add(ref)
            if not g["gallery"]:
                g["gallery"] = [row[c] for c in GALLERY_COLUMNS if row.get(c)]
    ref_owners = defaultdict(set)
    for g in groups.values():
        for ref in g["refs"]:
            ref_owners[ref].add(g["id"])
    ambiguous = {r for r, owners in ref_owners.items() if len(owners) > 1}
    return groups, ambiguous


def match(group, products, sku_index, ambiguous):
    """Return (shopify_product, None) or (None, reason)."""
    if group["refs"] & ambiguous:
        return None, "ambiguous_reference"
    found = set()
    for ref in group["refs"]:
        found |= sku_index.get(ref, set())
    if not found:
        return None, "no_match"
    if len(found) > 1:
        return None, "multiple_shopify_products"
    product = products[next(iter(found))]
    if not product["skus"] <= group["refs"]:
        return None, "mixed_product"
    return product, None


def plan(group, product):
    """Decide which gallery URLs are missing. Pure except for HEAD requests."""
    ready = [m for m in product["media"] if m["status"] == "READY" and m["size"]]
    busy = [m for m in product["media"] if m["status"] in ("UPLOADED", "PROCESSING")]
    result = {"missing": [], "present": [], "dead": [], "attention": None}
    if busy:
        result["attention"] = "media_still_processing"
        return result
    remaining = Counter(m["size"] for m in ready)
    unknown = []
    for url in group["gallery"]:
        status, value = source_size(url)
        if status == "dead":
            result["dead"].append((url, value))
            continue
        if not remaining:
            result["missing"].append(url)
            continue
        if status == "unknown":
            unknown.append((url, value))
            continue
        if remaining[value] > 0:
            remaining[value] -= 1
            if not +remaining[value]:
                remaining.pop(value, None)
            result["present"].append(url)
        else:
            result["missing"].append(url)
    if ready and not result["present"]:
        result["attention"] = ("source_size_unknown" if unknown
                               else "existing_media_not_recognised")
        result["missing"] = []
    elif unknown and remaining:
        result["attention"] = "source_size_unknown"
        result["missing"] = []
    return result


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
ADD_MUTATION = """mutation($product: ProductUpdateInput, $media: [CreateMediaInput!]) {
  productUpdate(product: $product, media: $media) {
    product { id media(first: 100) { nodes { id status } } }
    userErrors { field message } } }"""
STATUS_QUERY = "query($ids: [ID!]!) { nodes(ids: $ids) { ... on MediaImage { id status } } }"


def add_images(shop, product, urls):
    """Add urls (small batch). Returns list of (url, new_media_id)."""
    before = {m["id"] for m in product["media"]}
    data = shop.gql(ADD_MUTATION, {
        "product": {"id": product["id"]},
        "media": [{"originalSource": u, "mediaContentType": "IMAGE"} for u in urls]})["productUpdate"]
    if data.get("userErrors"):
        raise ApiError(f"productUpdate: {data['userErrors']}")
    nodes = data["product"]["media"]["nodes"]
    new = [n["id"] for n in nodes if n["id"] not in before]
    for n in nodes:
        if n["id"] not in before:
            product["media"].append({"id": n["id"], "status": n["status"], "size": None})
    return list(zip(urls, new))


def wait_ready(shop, ids):
    """Poll until no id is UPLOADED/PROCESSING. Returns {id: status}."""
    statuses = {}
    deadline = time.monotonic() + POLL_TIMEOUT
    pending = list(ids)
    while pending:
        out = shop.gql(STATUS_QUERY, {"ids": pending})["nodes"]
        statuses.update({n["id"]: n["status"] for n in out if n})
        pending = [i for i in pending if statuses.get(i) in (None, "UPLOADED", "PROCESSING")]
        if not pending or time.monotonic() > deadline:
            break
        time.sleep(POLL_SECONDS)
    return statuses


def remove_failed(shop, ids):
    """Delete failed (never working) uploads created by this run."""
    if not ids:
        return
    try:
        out = shop.gql("mutation($ids: [ID!]!) { fileDelete(fileIds: $ids) "
                       "{ deletedFileIds userErrors { message } } }", {"ids": ids})
        if out["fileDelete"]["userErrors"]:
            print("[warn] fileDelete:", out["fileDelete"]["userErrors"], file=sys.stderr)
    except ApiError as exc:
        print("[warn] could not remove failed uploads:", exc, file=sys.stderr)


def apply_product(shop, product, missing, report, ref):
    """Add `missing`, verify, retry failures singly, clean up. Mutates report."""
    pairs = []
    for i in range(0, len(missing), BATCH_URLS):
        pairs += add_images(shop, product, missing[i:i + BATCH_URLS])
        time.sleep(PAUSE_BETWEEN_CALLS)
    return pairs


def finish_product(shop, product, pairs, report, ref):
    statuses = wait_ready(shop, [mid for _, mid in pairs])
    failed = [(u, m) for u, m in pairs if statuses.get(m) != "READY"]
    report["images_added"] += len(pairs) - len(failed)
    retry_failed = []
    for url, mid in failed:                                # retry one by one
        time.sleep(PAUSE_BETWEEN_CALLS)
        again = add_images(shop, product, [url])
        st = wait_ready(shop, [m for _, m in again])
        if again and st.get(again[0][1]) == "READY":
            report["images_added"] += 1
            report["images_recovered_by_retry"] += 1
        else:
            report["failed_images"].append({"reference": ref, "url": url})
            retry_failed += [m for _, m in again]
        retry_failed.append(mid)
    remove_failed(shop, retry_failed)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="public/feeds/products.csv")
    ap.add_argument("--report", default="public/reports/shopify_image_sync_report.json")
    ap.add_argument("--apply", action="store_true", help="write to Shopify (default: dry run)")
    ap.add_argument("--limit", type=int, default=0, help="max matched products to process (0 = all)")
    ap.add_argument("--budget-minutes", type=float, default=320)
    ap.add_argument("--api-base", default=None, help="testing only")
    args = ap.parse_args()

    domain = os.environ.get("SHOPIFY_STORE_DOMAIN", "").strip()
    token = os.environ.get("SHOPIFY_ADMIN_TOKEN", "").strip()
    cid = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
    secret = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
    if not domain.endswith(".myshopify.com") and not args.api_base:
        sys.exit("ERROR: SHOPIFY_STORE_DOMAIN must look like xxx.myshopify.com")
    if not (token or (cid and secret)):
        sys.exit("ERROR: set SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET (GitHub secrets)")
    try:
        shop = Shopify(domain, token or None, args.api_base, cid, secret)
    except ApiError as exc:
        sys.exit(f"ERROR: {exc}")
    started = time.monotonic()
    report = {"mode": "apply" if args.apply else "dry_run", "feed_products": 0,
              "matched_products": 0, "products_checked": 0, "products_already_complete": 0,
              "products_needing_images": 0, "products_updated": 0, "images_added": 0,
              "images_recovered_by_retry": 0, "duplicates_skipped": 0, "images_still_to_add": 0,
              "skipped_unmatched": {}, "failed_images": [], "dead_source_urls": [],
              "products_needing_attention": [], "not_processed_time_budget": 0}
    skipped = Counter()
    examples = defaultdict(list)
    try:
        groups, ambiguous = load_feed(args.feed)
        groups = {k: g for k, g in groups.items() if g["gallery"]}
        report["feed_products"] = len(groups)
        products, sku_index = export_products(shop)
        report["shopify_products_exported"] = len(products)
        todo = []
        for g in groups.values():
            product, reason = match(g, products, sku_index, ambiguous)
            if reason:
                skipped[reason] += 1
                if len(examples[reason]) < 15:
                    examples[reason].append(sorted(g["refs"])[:3])
            else:
                todo.append((g, product))
        report["matched_products"] = len(todo)
        if args.limit:
            todo = todo[:args.limit]
        deadline = started + args.budget_minutes * 60

        for start in range(0, len(todo), CHUNK_PRODUCTS):
            if time.monotonic() > deadline:
                report["not_processed_time_budget"] = len(todo) - start
                break
            chunk = todo[start:start + CHUNK_PRODUCTS]
            with ThreadPoolExecutor(max_workers=6) as pool:
                plans = list(pool.map(lambda gp: plan(*gp), chunk))
            submitted = []
            for (g, product), p in zip(chunk, plans):
                ref = sorted(g["refs"])[0]
                report["products_checked"] += 1
                report["duplicates_skipped"] += len(p["present"])
                for url, code in p["dead"]:
                    report["dead_source_urls"].append({"reference": ref, "url": url, "http": code})
                if p["attention"]:
                    report["products_needing_attention"].append(
                        {"reference": ref, "reason": p["attention"]})
                    continue
                if not p["missing"]:
                    report["products_already_complete"] += 1
                    continue
                report["products_needing_images"] += 1
                if not args.apply:
                    report["images_still_to_add"] += len(p["missing"])
                    continue
                try:
                    pairs = apply_product(shop, product, p["missing"], report, ref)
                    submitted.append((product, pairs, ref))
                    report["products_updated"] += 1
                except ApiError as exc:
                    report["products_needing_attention"].append(
                        {"reference": ref, "reason": f"api_error: {str(exc)[:160]}"})
            for product, pairs, ref in submitted:
                try:
                    finish_product(shop, product, pairs, report, ref)
                except ApiError as exc:
                    report["products_needing_attention"].append(
                        {"reference": ref, "reason": f"api_error: {str(exc)[:160]}"})
            if (start // CHUNK_PRODUCTS) % 20 == 0:
                print(f"[progress] {report['products_checked']}/{len(todo)} products, "
                      f"{report['images_added']} images added", flush=True)
    finally:
        report["skipped_unmatched"] = {k: {"count": v, "examples": examples[k]}
                                       for k, v in skipped.items()}
        report["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        report["products_needing_attention_count"] = len(report["products_needing_attention"])
        report["products_needing_attention"] = report["products_needing_attention"][:200]
        report["failed_images_count"] = len(report["failed_images"])
        report["failed_images"] = report["failed_images"][:200]
        report["dead_source_urls_count"] = len(report["dead_source_urls"])
        report["dead_source_urls"] = report["dead_source_urls"][:200]
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items()
                          if not isinstance(v, (list, dict))}, indent=1))


if __name__ == "__main__":
    main()
