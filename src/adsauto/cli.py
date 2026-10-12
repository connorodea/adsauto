"""Командная строка.

  python -m adsauto.cli feed --site https://www.estaca.com.ua --prefix estaca
  python -m adsauto.cli feed --site ... --prefix ... --out feeds/estaca.xml --json report.json
  python -m adsauto.cli feed --site https://medicalrxlab.com --connector medicalrxlab \
      --prefix medicalrxlab --out feeds/medicalrxlab.xml --json feeds/medicalrxlab-report.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .feed import build as feed_build
from .feed import policy as feed_policy
from .feed.validate import validate
from .sources.medicalrxlab import MedicalRxLabExtractor
from .sources.woo import WooExtractor

CONNECTORS = {
    "woocommerce": WooExtractor,
    "medicalrxlab": MedicalRxLabExtractor,
}

# Соответствие категорий магазина таксономии Google.
# Заполняется под клиента; без него Google угадывает категорию сам.
GOOGLE_CATEGORY_MAP: dict[str, str] = {
    "Письмові столи": "Furniture > Office Furniture > Desks",
    "Столи керівника": "Furniture > Office Furniture > Desks",
    "Розкладні мобільні столи": "Furniture > Tables",
    "Столи для переговорів": "Furniture > Tables",
    "Стелажі": "Furniture > Shelving",
    "Тумби": "Furniture > Cabinets & Storage",
    "Шафи": "Furniture > Cabinets & Storage",
}


def apply_category_map(products, mapping: dict[str, str]) -> int:
    hits = 0
    for product in products:
        target = mapping.get(product.product_type or "")
        if target:
            product.google_product_category = target
            hits += 1
    return hits


def cmd_feed(args: argparse.Namespace) -> int:
    extractor = CONNECTORS[args.connector](args.site, offer_prefix=args.prefix)
    products = extractor.run()
    if not products:
        print("Каталог пуст — проверьте, открыт ли Store API", file=sys.stderr)
        return 2

    mapped = apply_category_map(products, GOOGLE_CATEGORY_MAP)
    feed_build.apply_identifier_policy(products)
    report = validate(products)
    policy = feed_policy.check(products)

    out = Path(args.out)
    feed_build.write(products, out, title=args.title or args.prefix, link=args.site)

    # Every row cites its raw response: connectors that keep the whole
    # payload write it next to the feed with its sha256 and fetch time.
    raw_path = None
    provenance = None
    if hasattr(extractor, "save_raw"):
        raw_path = extractor.save_raw(out.with_name(f"{out.stem}-raw.json"))
        provenance = extractor.provenance()
    skipped = getattr(extractor, "skipped", [])

    print(f"Фид: {out}")
    if raw_path:
        print(f"Raw response: {raw_path} (sha256 {provenance['sha256'][:12]})")
    with_category = sum(1 for p in products if p.google_product_category)
    print(f"Категории Google проставлены: {with_category} из {len(products)} (по карте: {mapped})")
    if skipped:
        print(f"Skipped by connector: {len(skipped)}")
    notes = getattr(extractor, "notes", [])
    if notes:
        print(f"Connector notes: {len(notes)} (see report JSON)")
    print()
    print(report.summary())
    print()
    print(policy.summary())
    if policy.issues:
        print("\nPolicy warnings (first few):")
        for issue in policy.issues[: args.limit]:
            print(f"  {issue}")

    if report.errors:
        print("\nОшибки, из-за которых товары будут отклонены:")
        for issue in report.errors[: args.limit]:
            print(f"  {issue}")
            if issue.fix:
                print(f"      → {issue.fix}")
        if len(report.errors) > args.limit:
            print(f"  ... ещё {len(report.errors) - args.limit}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "total": report.total,
                    "rejection_rate": report.rejection_rate,
                    "by_code": report.by_code(),
                    "connector": args.connector,
                    "provenance": provenance,
                    "skipped": skipped,
                    "notes": notes,
                    "policy": {
                        "total_warnings": len(policy.issues),
                        "flagged_items": len(policy.flagged_offer_ids),
                        "by_code": policy.by_code(),
                        "issues": [
                            {
                                "code": i.code,
                                "offer_id": i.offer_id,
                                "message": i.message,
                                "fix": i.fix,
                            }
                            for i in policy.issues
                        ],
                    },
                    "issues": [
                        {
                            "code": i.code,
                            "severity": i.severity,
                            "offer_id": i.offer_id,
                            "message": i.message,
                            "fix": i.fix,
                        }
                        for i in report.issues
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nОтчёт: {args.json}")

    return 1 if report.errors and args.strict else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adsauto")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    feed = sub.add_parser("feed", help="собрать и проверить товарный фид")
    feed.add_argument("--site", required=True, help="https://example.com")
    feed.add_argument("--prefix", required=True, help="префикс идентификаторов, напр. estaca")
    feed.add_argument(
        "--connector", choices=sorted(CONNECTORS), default="woocommerce",
        help="источник каталога (default: woocommerce)",
    )
    feed.add_argument("--out", default="feeds/feed.xml")
    feed.add_argument("--title", default=None)
    feed.add_argument("--json", default=None, help="куда сохранить отчёт валидатора")
    feed.add_argument("--limit", type=int, default=30, help="сколько ошибок печатать")
    feed.add_argument("--strict", action="store_true", help="ненулевой код возврата при ошибках")
    feed.set_defaults(func=cmd_feed)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
