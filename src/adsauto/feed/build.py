"""Генератор XML-фида для Merchant Center.

Формат: RSS 2.0 с пространством имён http://base.google.com/ns/1.0.
Этот путь выбран сознательно вместо Merchant API: scheduled fetch не
затронут отключением Content API (18.08.2026), не требует OAuth на
стороне клиента и легко проверяется глазами.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..models import Product

NS = "http://base.google.com/ns/1.0"
ET.register_namespace("g", NS)


def _g(parent: ET.Element, tag: str, text: str | None) -> ET.Element | None:
    if text is None or text == "":
        return None
    el = ET.SubElement(parent, f"{{{NS}}}{tag}")
    el.text = text
    return el


def _item(product: Product) -> ET.Element:
    item = ET.Element("item")

    _g(item, "id", product.offer_id)
    _g(item, "title", product.title[:150])
    _g(item, "description", product.description[:5000])
    _g(item, "link", product.link)
    _g(item, "image_link", product.image_link)
    for extra in product.additional_image_links[:10]:
        _g(item, "additional_image_link", extra)

    _g(item, "availability", product.availability)
    _g(item, "price", product.price_str())
    if product.sale_price is not None:
        _g(item, "sale_price", product.sale_price_str())

    _g(item, "condition", product.condition)
    _g(item, "brand", product.brand)
    _g(item, "gtin", product.gtin)
    _g(item, "mpn", product.mpn)
    if not product.identifier_exists:
        _g(item, "identifier_exists", "false")

    _g(item, "google_product_category", product.google_product_category)
    _g(item, "product_type", product.product_type)
    _g(item, "item_group_id", product.item_group_id)

    for name, value in product.product_details[:20]:
        detail = ET.SubElement(item, f"{{{NS}}}product_detail")
        _g(detail, "attribute_name", name)
        _g(detail, "attribute_value", value)

    return item


def build(
    products: Sequence[Product],
    title: str,
    link: str,
    description: str = "",
) -> ET.ElementTree:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = link
    ET.SubElement(channel, "description").text = (
        description or f"Сформировано {datetime.now(timezone.utc).isoformat()}"
    )
    for product in products:
        channel.append(_item(product))
    return ET.ElementTree(rss)


def write(
    products: Sequence[Product],
    path: str | Path,
    title: str,
    link: str,
) -> Path:
    tree = build(products, title=title, link=link)
    ET.indent(tree, space="  ")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out, encoding="utf-8", xml_declaration=True)
    return out


def apply_identifier_policy(products: Sequence[Product]) -> None:
    """Собственное производство без штрихкодов.

    Если у товара нет GTIN и нет собственного MPN, отличного от
    родительского, Google обязан быть уведомлён явно — иначе он ждёт
    идентификатор и отклоняет товар.
    """
    for product in products:
        if not product.gtin and not (product.mpn and product.brand):
            product.identifier_exists = False
