"""Нормализованная модель товара.

Один слой между любым источником (WooCommerce, Shopify, Хорошоп, XML) и
генератором фида. Источники обязаны отдавать именно эту модель — тогда
добавление новой платформы не трогает ни фид, ни валидатор.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

Availability = Literal["in_stock", "out_of_stock", "backorder"]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(raw: str | None) -> str:
    """HTML из описаний WooCommerce в фид отдавать нельзя."""
    if not raw:
        return ""
    text = raw.replace("</p>", "\n").replace("<br>", "\n").replace("<br/>", "\n")
    text = text.replace("</li>", "\n").replace("<li>", "• ")
    text = _TAG_RE.sub(" ", text)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#8217;", "'"), ("&#8211;", "-"), ("&ldquo;", '"'),
        ("&rdquo;", '"'), ("&#8372;", "₴"),
    ):
        text = text.replace(entity, char)
    lines = [_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


@dataclass
class Product:
    """Товар в том виде, в каком его понимает Merchant Center.

    Для вариативных товаров каждый вариант — отдельный Product,
    объединённый с остальными через item_group_id.
    """

    # идентификация
    offer_id: str                      # g:id — стабильный, не меняется между выгрузками
    item_group_id: str | None = None   # g:item_group_id — родитель для вариантов
    source_id: int | None = None       # исходный ID в CMS, для отладки
    sku: str | None = None             # как есть из CMS, может быть неуникальным

    # содержание
    title: str = ""
    description: str = ""
    link: str = ""
    image_link: str = ""
    additional_image_links: list[str] = field(default_factory=list)

    # деньги
    price: Decimal | None = None
    sale_price: Decimal | None = None
    currency: str = "UAH"

    # наличие
    availability: Availability = "in_stock"

    # идентификаторы производителя
    brand: str | None = None
    gtin: str | None = None
    mpn: str | None = None
    identifier_exists: bool = True

    # категоризация
    google_product_category: str | None = None
    product_type: str | None = None     # своя категория, путь через ">"

    # характеристики: [(название, значение)]
    product_details: list[tuple[str, str]] = field(default_factory=list)
    variant_label: str | None = None    # "Кольори: Білий + Білий"

    # служебное
    condition: str = "new"
    source: str = "woocommerce"

    @property
    def is_variant(self) -> bool:
        return self.item_group_id is not None

    def price_str(self) -> str | None:
        if self.price is None:
            return None
        return f"{self.price:.2f} {self.currency}"

    def sale_price_str(self) -> str | None:
        if self.sale_price is None:
            return None
        return f"{self.sale_price:.2f} {self.currency}"
