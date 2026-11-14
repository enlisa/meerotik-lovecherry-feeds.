#!/usr/bin/env python3
"""READ-ONLY analysis: skipped products vs. stock.csv, and Stockeo duplicate clusters.
Never writes to Shopify. Outputs public/reports/catalog_analysis.json and
public/reports/skipped_products.csv / duplicate_clusters.csv."""
import csv, json, os, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sync_shopify_images as s

def stock_by_ref(path):
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            ref = (r.get("Referencia") or "").strip()
            try: out[ref] = out.get(ref, 0) + int(float(r.get("stock") or 0))
            except ValueError: pass
    return out

def ts(v):
    return datetime.fromisoformat(v.replace("Z", "+00:00"))

def main():
    shop = s.Shopify(os.environ["SHOPIFY_STORE_DOMAIN"], None, None,
                     os.environ["SHOPIFY_CLIENT_ID"], os.environ["SHOPIFY_CLIENT_SECRET"])
    groups, ambiguous = s.load_feed("public/feeds/products.csv")
    stock = stock_by_ref("public/feeds/stock.csv")
    products, sku_index = s.export_products(shop)
    out = Path("public/reports"); out.mkdir(parents=True, exist_ok=True)
    skipped, clusters, sizes, gaps = [], [], Counter(), []
    reasons, diffs = Counter(), Counter()
    for g in groups.values():
        prods, reason = s.match(g, products, sku_index, ambiguous)
        total = sum(stock.get(r, 0) for r in g["refs"])
        if reason:
            found = sorted({i for r in g["refs"] for i in sku_index.get(r, set())})
            ps = [products[i] for i in found]
            why = reason
            if reason == "duplicate_products_not_identical":
                d = []
                if len({p["title"].strip().lower() for p in ps}) > 1: d.append("title")
                if len({p["vendor"].strip().lower() for p in ps}) > 1: d.append("vendor")
                if len({frozenset(p["skus"]) for p in ps}) > 1: d.append("sku_set")
                why = "not_identical:" + "+".join(d or ["?"])
                diffs[why] += 1
            reasons[reason] += 1
            skipped.append({"idProducto": g["id"], "refs": " | ".join(sorted(g["refs"])),
                            "reason": why, "stock_total": total,
                            "stock_status": "in_stock" if total > 0 else "zero_stock",
                            "shopify_products": len(ps),
                            "shopify_ids": " | ".join(p["id"].rsplit("/", 1)[-1] for p in ps),
                            "titles": " | ".join(sorted({p["title"] for p in ps}))[:300]})
        elif len(prods) > 1:
            sizes[len(prods)] += 1
            times = sorted(ts(p["created"]) for p in prods if p.get("created"))
            gap = (times[-1] - times[0]).total_seconds() if len(times) > 1 else None
            if gap is not None: gaps.append(gap)
            clusters.append({"idProducto": g["id"], "refs": " | ".join(sorted(g["refs"])),
                             "copies": len(prods), "title": prods[0]["title"][:120],
                             "shopify_ids": " | ".join(p["id"].rsplit("/", 1)[-1] for p in prods),
                             "created_gap_seconds": gap, "stock_total": total,
                             "all_draft": all(p["status"] == "DRAFT" for p in prods)})
    def dump(name, rows):
        with open(out / name, "w", newline="", encoding="utf-8-sig") as f:
            if rows:
                w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    dump("skipped_products.csv", skipped); dump("duplicate_clusters.csv", clusters)
    gaps.sort()
    def pct(q): return round(gaps[int(q * (len(gaps) - 1))], 1) if gaps else None
    skipped_stock = Counter(r["stock_status"] for r in skipped)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "shopify_products": len(products), "feed_products": len(groups),
        "skipped_total": len(skipped), "skipped_reasons": dict(reasons),
        "skipped_not_identical_breakdown": dict(diffs),
        "skipped_zero_stock": skipped_stock["zero_stock"],
        "skipped_in_stock": skipped_stock["in_stock"],
        "skipped_in_stock_units": sum(r["stock_total"] for r in skipped),
        "skipped_shopify_copy_counts": dict(Counter(r["shopify_products"] for r in skipped)),
        "exact_duplicate_clusters": len(clusters),
        "exact_duplicate_cluster_sizes": dict(sizes),
        "exact_duplicate_extra_products": sum(c["copies"] - 1 for c in clusters),
        "exact_duplicate_all_draft": sum(c["all_draft"] for c in clusters),
        "exact_duplicate_created_gap_seconds": {"min": pct(0), "median": pct(.5), "p95": pct(.95), "max": pct(1)},
        "exact_duplicate_in_stock_clusters": sum(c["stock_total"] > 0 for c in clusters),
        "stock_csv_rows": len(stock)}
    (out / "catalog_analysis.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=1))

if __name__ == "__main__":
    main()
