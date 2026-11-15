#!/usr/bin/env python3
"""Activate (status ACTIVE) and publish to the Online Store ONLY the products that are members of the
five main collections (= clearly assigned canonical copies with Lovecherry stock), after safety checks.
Never touches price, images, variants, SKUs, stock, theme or the store password. Default = dry run.
Skipped (stay DRAFT): price <= 0 / missing, Shopify stock <= 0, no image.
Also publishes the sub-collections to the Online Store with --publish-collections."""
import argparse, csv, json, os, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sync_shopify_images as s
from categorize_collections import COLLECTIONS
from subcollections import SUBS

MAIN = ["toys", "lingerie", "bdsm", "fuer-paare", "wellness"]


def members(shop, cid):
    out, after = [], None
    while True:
        d = shop.gql("query($id:ID!,$a:String){collection(id:$id){products(first:250,after:$a){nodes{id} pageInfo{hasNextPage endCursor}}}}",
                     {"id": cid, "a": after})["collection"]["products"]
        out += [n["id"] for n in d["nodes"]]
        if not d["pageInfo"]["hasNextPage"]:
            return out
        after = d["pageInfo"]["endCursor"]


ONLINE_STORE_PUBLICATION = os.environ.get("ONLINE_STORE_PUBLICATION_ID") or "gid://shopify/Publication/365875659092"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--publish-collections", action="store_true")
    ap.add_argument("--no-publish", action="store_true", help="only set status (app lacks write_publications)")
    args = ap.parse_args()
    shop = s.Shopify(os.environ["SHOPIFY_STORE_DOMAIN"], None, None,
                     os.environ["SHOPIFY_CLIENT_ID"], os.environ["SHOPIFY_CLIENT_SECRET"])
    pub = ONLINE_STORE_PUBLICATION
    cids = {}
    for h in list(COLLECTIONS) + list(SUBS):
        d = shop.gql('query($q:String!){collections(first:1,query:$q){nodes{id handle}}}', {"q": f"handle:{h}"})["collections"]["nodes"]
        if d and d[0]["handle"] == h:
            cids[h] = d[0]["id"]
    ids = []
    seen = set()
    for h in MAIN:
        for pid in members(shop, cids[h]):
            if pid not in seen:
                seen.add(pid); ids.append(pid)
    print(f"candidate products (members of the 5 main collections): {len(ids)}")
    ok, skipped, reasons = [], [], Counter()
    for i in range(0, len(ids), 100):
        nodes = shop.gql("""query($ids:[ID!]!){nodes(ids:$ids){... on Product{id title status
            mediaCount{count} variants(first:100){nodes{sku price inventoryQuantity}}}}}""",
                         {"ids": ids[i:i + 100]})["nodes"]
        for n in nodes:
            if not n:
                continue
            vs = n["variants"]["nodes"]
            prices = [float(v["price"] or 0) for v in vs]
            stock = sum(max(v["inventoryQuantity"] or 0, 0) for v in vs)
            why = None
            if not vs or min(prices) <= 0:
                why = "price_missing_or_zero"
            elif stock <= 0:
                why = "no_shopify_stock"
            elif (n["mediaCount"] or {}).get("count", 0) < 1:
                why = "no_image"
            if why:
                reasons[why] += 1
                skipped.append({"id": n["id"], "title": n["title"][:100], "reason": why, "status": n["status"]})
            else:
                ok.append({"id": n["id"], "status": n["status"], "published": n["status"] == "ACTIVE"})
    todo = [p for p in ok if p["status"] != "ACTIVE" or not p["published"]]
    if args.limit:
        todo = todo[:args.limit]
    done_status = done_pub = 0
    errors = []
    if args.apply:
        for i in range(0, len(todo), 10):
            chunk = todo[i:i + 10]
            m1 = " ".join(f'a{j}:productChangeStatus(productId:"{p["id"]}",status:ACTIVE){{userErrors{{message}}}}'
                           for j, p in enumerate(chunk) if p["status"] != "ACTIVE")
            if m1:
                d = shop.gql("mutation{" + m1 + "}")
                for k, v in d.items():
                    if v["userErrors"]:
                        errors.append(("status", v["userErrors"][0]["message"]))
                    else:
                        done_status += 1
            m2 = " ".join(f'b{j}:publishablePublish(id:"{p["id"]}",input:[{{publicationId:"{pub}"}}]){{userErrors{{message}}}}'
                           for j, p in enumerate(chunk) if not p["published"])
            if m2 and not args.no_publish:
                d = shop.gql("mutation{" + m2 + "}")
                for k, v in d.items():
                    if v["userErrors"]:
                        errors.append(("publish", v["userErrors"][0]["message"]))
                    else:
                        done_pub += 1
            time.sleep(0.5)
    coll_pub = {}
    if args.apply and args.publish_collections:
        for h in SUBS:
            d = shop.gql('mutation($id:ID!,$p:ID!){publishablePublish(id:$id,input:[{publicationId:$p}]){userErrors{message}}}',
                         {"id": cids[h], "p": pub})["publishablePublish"]
            coll_pub[h] = d["userErrors"][:1] or "published"
    out = Path("public/reports"); out.mkdir(parents=True, exist_ok=True)
    (out / "activation_ids.txt").write_text("\n".join(p["id"].rsplit("/", 1)[-1] for p in ok) + "\n")
    report = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "mode": "apply" if args.apply else "dry_run", "limit": args.limit,
              "candidates": len(ids), "eligible": len(ok), "skipped": dict(reasons),
              "still_to_activate_or_publish": len([p for p in ok if p["status"] != "ACTIVE" or not p["published"]]),
              "processed_now": len(todo), "status_set_active": done_status, "published_online_store": done_pub,
              "errors": Counter(e[1] for e in errors).most_common(5),
              "collections_published": len([v for v in coll_pub.values() if v == "published"]) if coll_pub else 0,
              "collection_publish_errors": {k: v for k, v in coll_pub.items() if v != "published"}}
    (out / "activation_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    with open(out / "activation_skipped.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", "title", "reason", "status"]); w.writeheader(); w.writerows(skipped)
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
