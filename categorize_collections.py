#!/usr/bin/env python3
"""Assign existing Shopify products to the seven MEEROTIK collections.

Classification uses product name + manufacturer from the Lovecherry feed (only clear matches).
Stock comes from the Lovecherry stock.csv. Only collection MEMBERSHIP is written; products,
prices, images, variants, SKUs, stock and status are never touched. Default = dry run.

Exclusions (never added to a collection, listed in the report instead):
  no_stock, unclassified, duplicate_extra (older/other copies of exact duplicates; the oldest
  copy is the canonical one), ambiguous_reference / mixed_product / no_match /
  duplicate_products_not_identical / too_many_duplicates (unclear match to Shopify).
Neuheiten and Bestseller cannot be derived from the feed (no date or sales data): left empty.
"""
import argparse, csv, json, os, re, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sync_shopify_images as s
from analyze_catalog import stock_by_ref

COLLECTIONS = {"toys": "Toys", "lingerie": "Lingerie", "bdsm": "BDSM",
               "fuer-paare": "Für Paare", "wellness": "Wellness",
               "neuheiten": "Neuheiten", "bestseller": "Bestseller"}


def rx(*words):
    return re.compile("|".join(words), re.I)


BDSM = rx(r"hand ?cuffs?", r"\bcuffs?\b", r"ankle cuffs", r"\bwhips?\b", r"flogger", r"paddle", r"\bcollar", r"leash",
          r"gag\b", r"bondage", r"blindfold", r"restraint", r"spreader", r"nipple clamp", r"\bclamps?\b",
          r"hog ?tie", r"electro", r"\bshock", r"bdsm", r"fetish", r"spank", r"feather", r"chastity",
          r"cock cage", r"shibari", r"bind me", r"\bcrop\b", r"\bhood\b", r"muzzle")
BDSM_BRANDS = {"BONDAGE PLAY", "LATETOBED BDSM LINE"}
LINGERIE = rx(r"\bbras?\b", r"thongs?", r"panties", r"panty", r"\bbriefs?\b", r"teddy", r"babydoll", r"chemise",
              r"bodystocking", r"\bbody\b", r"stockings?", r"garter", r"suspender", r"corset", r"basque",
              r"bustier", r"negligee", r"nightie", r"\brobe\b", r"g-string", r"\bjock\b", r"lingerie",
              r"costume", r"bikini", r"nipple covers?", r"pasties", r"hold-up", r"\bdress\b", r"\bskirt\b",
              r"\bgown\b", r"catsuit", r"romper", r"\bboxers?\b", r"pantyhose", r"\btights\b", r"\bshirt\b")
LINGERIE_BRANDS = {"OBSESSIVE", "ADALET LINGERIE", "AMORABLE", "CUT4MEN", "PENTHOUSE"}
LINGERIE_NOT = rx(r"massage", r"\boil\b", r"lotion", r"\bwash\b", r"paint", r"cream", r"lubric", r"spray",
                  r"scrub", r"bath", r"soap", r"cleaner")
TOYS = rx(r"dildo", r"vibrat", r"\bvibes?\b", r"masturbat", r"\bplugs?\b", r"bullet", r"\beggs?\b", r"rabbit",
          r"stimulat", r"cock ?rings?", r"penis rings?", r"c-ring", r"kegel", r"ben wa", r"love balls",
          r"\bwand\b", r"sleeve", r"\bpump\b", r"strap-?on", r"clitor", r"sucker", r"torso", r"fleshlight",
          r"tenga", r"pussy", r"vagina", r"\bdong\b", r"g-spot", r"prostate", r"\bcock\b", r"massager",
          r"air pulse", r"thrusting", r"\bbeads\b", r"\btoys\b", r"\bprobe\b", r"nipple suck")
TOY_BRANDS = {"SATISFYER", "LELO", "FUN FACTORY", "KING COCK", "XISE", "BASIX RUBBER WORKS", "SUMMUM",
              "FLESHLIGHT", "TENGA", "CRAZY BULL", "URBAN SKY", "LOVETOY", "PRETTYLOVE", "BAILE", "AFTERDARK"}
TOY_EXPLICIT = rx(r"dildo", r"vibrator", r"\bvibe\b", r"masturbator", r"\bplugs?\b")
COUPLES = rx(r"couples?", r"\bgames?\b", r"\bdice\b", r"roulette", r"coupons?", r"kamasutra", r"\bduo\b",
             r"for two", r"truth or dare", r"checkers", r"\bquiz\b", r"his (and|&) hers?", r"two-?player")
WELLNESS = rx(r"massage (oil|gel|candle|cream|lotion|balm)", r"\bmassage\b", r"\boils?\b", r"\bbath\b",
              r"candles?", r"perfume", r"pheromone", r"\bcream\b", r"lotion", r"lubric", r"\blube\b", r"\bglide\b",
              r"aroma", r"cleaner", r"cleaning", r"\bspray\b", r"essential", r"scrub", r"\bgel\b", r"\bbalm\b",
              r"serum", r"\bsoap\b", r"arousal", r"\bdelay\b", r"desensiti", r"lip gloss", r"\bfoam\b",
              r"\bwipes?\b", r"moisturi", r"\bpowder\b", r"body paint", r"edible", r"kegel", r"relax")
LUBE_LIKE = rx(r"lubric", r"\bgel\b", r"cleaner", r"cleaning", r"\boils?\b", r"cream", r"\bspray\b", r"lotion",
               r"\bglide\b", r"\bfoam\b")


def classify(name, brand):
    n, b = name or "", (brand or "").strip().upper()
    cols = set()
    if BDSM.search(n) or b in BDSM_BRANDS:
        cols.add("bdsm")
    if (LINGERIE.search(n) or b in LINGERIE_BRANDS) and not LINGERIE_NOT.search(n):
        cols.add("lingerie")
    lube = bool(LUBE_LIKE.search(n)) and not TOY_EXPLICIT.search(n)
    if (TOYS.search(n) or b in TOY_BRANDS) and not lube:
        cols.add("toys")
    if COUPLES.search(n):
        cols.add("fuer-paare")
    if WELLNESS.search(n) and not (cols == {"lingerie"} and not LUBE_LIKE.search(n)):
        cols.add("wellness")
    return cols


def load_names(path):
    names = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            pid = (row.get("idProducto") or "").strip() or f"row-{row['reference']}"
            names.setdefault(pid, (row.get("nombre") or "", row.get("fabricante") or ""))
    return names


def collection_ids(shop):
    out = {}
    for handle in COLLECTIONS:
        d = shop.gql('query($q:String!){collections(first:1,query:$q){nodes{id handle}}}',
                     {"q": f"handle:{handle}"})["collections"]["nodes"]
        if d and d[0]["handle"] == handle:
            out[handle] = d[0]["id"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    csv.field_size_limit(10 ** 9)
    shop = s.Shopify(os.environ["SHOPIFY_STORE_DOMAIN"], None, None,
                     os.environ["SHOPIFY_CLIENT_ID"], os.environ["SHOPIFY_CLIENT_SECRET"])
    groups, ambiguous = s.load_feed("public/feeds/products.csv")
    names = load_names("public/feeds/products.csv")
    stock = stock_by_ref("public/feeds/stock.csv")
    products, sku_index = s.export_products(shop)
    rows, members = [], defaultdict(list)
    excl, per_col = Counter(), Counter()
    in_stock_products = 0
    in_stock_dup_extras = 0
    for g in groups.values():
        name, brand = names.get(g["id"], ("", ""))
        total = sum(stock.get(r, 0) for r in g["refs"])
        cols = classify(name, brand)
        prods, reason = s.match(g, products, sku_index, ambiguous)
        status, use = "assigned", []
        if reason:
            status = "excluded:" + reason
        elif total <= 0:
            status = "excluded:no_stock"
        elif not cols:
            status = "excluded:unclassified"
        else:
            canon = sorted(prods, key=lambda p: (p.get("created") or "", p["id"]))
            use = canon[:1]
            if len(canon) > 1:
                in_stock_dup_extras += len(canon) - 1
        if total > 0:
            in_stock_products += 1
        excl[status] += 1
        if status == "assigned":
            for c in cols:
                per_col[c] += 1
                members[c].append(use[0]["id"])
        shopify_ids = " | ".join(p["id"].rsplit("/", 1)[-1] for p in (prods or []))
        rows.append({"idProducto": g["id"], "name": name[:120], "brand": brand, "stock": total,
                     "collections": ",".join(sorted(cols)), "status": status,
                     "shopify_ids": shopify_ids, "copies": len(prods or [])})
    out = Path("public/reports"); out.mkdir(parents=True, exist_ok=True)
    with open(out / "collection_plan.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    report = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "mode": "apply" if args.apply else "dry_run",
              "feed_products": len(groups), "shopify_products": len(products),
              "feed_products_with_stock": in_stock_products,
              "feed_products_without_stock": len(groups) - in_stock_products,
              "status_counts": dict(excl),
              "per_collection_products": {COLLECTIONS[h]: per_col.get(h, 0) for h in COLLECTIONS},
              "assigned_products": excl["assigned"],
              "duplicate_extra_copies_in_assigned_groups": in_stock_dup_extras,
              "note": "Neuheiten/Bestseller: no date/sales data in feed, not assigned."}
    added = {}
    if args.apply:
        ids = collection_ids(shop)
        for h, pids in members.items():
            if h not in ids:
                added[h] = "collection missing"; continue
            done = 0
            for i in range(0, len(pids), 250):
                chunk = pids[i:i + 250]
                d = shop.gql("mutation($id:ID!,$p:[ID!]!){collectionAddProductsV2(id:$id,productIds:$p)"
                             "{job{id} userErrors{message}}}", {"id": ids[h], "p": chunk})["collectionAddProductsV2"]
                if d.get("userErrors"):
                    added[h] = f"errors: {d['userErrors'][:2]}"; break
                done += len(chunk)
                time.sleep(1)
            else:
                added[h] = done
        report["added_to_collections"] = added
    (out / "collection_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
