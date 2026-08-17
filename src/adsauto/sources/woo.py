"""Экстрактор WooCommerce Store API.

Store API (/wp-json/wc/store/v1/) публичный и не требует ключей — этого
достаточно для сбора фида. Аутентифицированный wc/v3 нужен только если
магазин закрыт или требуются складские остатки по локациям.

Три вещи, которые здесь решаются и на которых ломаются штатные плагины фидов:

1. У варианта в Store API пустые categories, brands и attributes —
   их приходится наследовать от родителя.
2. Варианты одного товара часто имеют ОДИН И ТОТ ЖЕ sku (проверено на
   ESTACA: 132-60 у всех трёх цветов). Использовать sku как g:id нельзя.
3. У родителя вариативного товара prices.price_range != null. Такую
   запись в фид отдавать нельзя — только конкретные варианты.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Iterable, Iterator

import httpx

from ..models import Product, strip_html

log = logging.getLogger(__name__)

# Атрибуты WooCommerce, которые несут производителя. Порядок = приоритет.
BRAND_ATTRIBUTE_SLUGS = ("pa_vyrobnyk", "pa_brand", "pa_brend")

# Атрибуты-мусор, которые не нужно тащить в product_details фида.
SKIP_DETAIL_SLUGS = {"pa_kolory", "pa_color"}


def money(prices: dict[str, Any], key: str = "price") -> Decimal | None:
    """WooCommerce отдаёт цену в минорных единицах строкой.

    {"price": "430000", "currency_minor_unit": 2} — это 4300.00 ₴,
    а вовсе не 430 000. Прямое использование поля price — самая
    дорогая ошибка в фиде, которую можно допустить.
    """
    raw = prices.get(key)
    if raw in (None, ""):
        return None
    minor = int(prices.get("currency_minor_unit", 2))
    try:
        return Decimal(str(raw)) / (Decimal(10) ** minor)
    except (ArithmeticError, ValueError):
        log.warning("нераспознанная цена: %r", raw)
        return None


def best_image(images: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Первая картинка — основная, остальные дополнительные (Google берёт до 10)."""
    urls = [img.get("src", "") for img in images if img.get("src")]
    if not urls:
        return "", []
    return urls[0], urls[1:11]


def category_path(categories: list[dict[str, Any]]) -> str | None:
    """product_type: своя категория. Берём самую глубокую по длине ссылки."""
    if not categories:
        return None
    deepest = max(categories, key=lambda c: len(c.get("link", "")))
    return deepest.get("name")


def extract_brand(raw: dict[str, Any]) -> str | None:
    brands = raw.get("brands") or []
    if brands and brands[0].get("name"):
        return brands[0]["name"]
    for attr in raw.get("attributes") or []:
        if attr.get("taxonomy") in BRAND_ATTRIBUTE_SLUGS:
            terms = attr.get("terms") or []
            if terms:
                return terms[0].get("name")
    return None


def extract_details(raw: dict[str, Any]) -> list[tuple[str, str]]:
    """Атрибуты вида «Ширина, мм: 1000» — и в фид, и потом в кластеры ключей."""
    details: list[tuple[str, str]] = []
    for attr in raw.get("attributes") or []:
        if attr.get("taxonomy") in SKIP_DETAIL_SLUGS:
            continue
        name = attr.get("name")
        terms = attr.get("terms") or []
        if not name or not terms:
            continue
        value = ", ".join(t.get("name", "") for t in terms if t.get("name"))
        if value:
            details.append((name, value))
    return details


def availability_of(raw: dict[str, Any]) -> str:
    if raw.get("is_on_backorder"):
        return "backorder"
    return "in_stock" if raw.get("is_in_stock") else "out_of_stock"


class WooExtractor:
    def __init__(
        self,
        base_url: str,
        offer_prefix: str,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.prefix = offer_prefix
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "adsauto-feed/0.1"},
            follow_redirects=True,
        )

    # --- сеть ---------------------------------------------------------

    def _get(self, path: str, **params: Any) -> Any:
        url = f"{self.base}/wp-json/wc/store/v1/{path}"
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def fetch_catalog(self, per_page: int = 100) -> list[dict[str, Any]]:
        """Все товары каталога (без вариантов — их Store API в списке не отдаёт)."""
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self._get("products", per_page=per_page, page=page)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        log.info("получено товаров каталога: %d", len(out))
        return out

    def fetch_variation(self, variation_id: int) -> dict[str, Any]:
        return self._get(f"products/{variation_id}")

    # --- преобразование ----------------------------------------------

    def offer_id(self, source_id: int) -> str:
        """Стабильный g:id.

        Строится от ID в CMS, а не от sku — потому что sku у вариантов
        может совпадать. Идентификатор обязан быть постоянным между
        выгрузками, иначе Merchant Center видит новые товары каждый раз
        и обнуляет накопленную статистику.
        """
        return f"{self.prefix}-{source_id}"

    def simple_to_product(self, raw: dict[str, Any]) -> Product:
        image, extra = best_image(raw.get("images") or [])
        sku = (raw.get("sku") or "").strip() or None
        return Product(
            offer_id=self.offer_id(raw["id"]),
            source_id=raw["id"],
            sku=sku,
            title=(raw.get("name") or "").strip(),
            description=strip_html(raw.get("description") or raw.get("short_description")),
            link=raw.get("permalink", ""),
            image_link=image,
            additional_image_links=extra,
            price=money(raw.get("prices") or {}),
            sale_price=(
                money(raw.get("prices") or {}, "sale_price")
                if raw.get("on_sale") else None
            ),
            currency=(raw.get("prices") or {}).get("currency_code", "UAH"),
            availability=availability_of(raw),
            brand=extract_brand(raw),
            mpn=sku,
            product_type=category_path(raw.get("categories") or []),
            product_details=extract_details(raw),
        )

    def variation_to_product(
        self,
        var_raw: dict[str, Any],
        parent_raw: dict[str, Any],
        variant_label: str | None,
    ) -> Product:
        """Вариант наследует от родителя всё, что Store API у него обнуляет."""
        image, extra = best_image(var_raw.get("images") or [])
        if not image:
            image, extra = best_image(parent_raw.get("images") or [])

        parent_sku = (parent_raw.get("sku") or "").strip() or None
        var_sku = (var_raw.get("sku") or "").strip() or None
        # sku варианта, совпадающий с родительским, не является идентификатором
        # варианта — в mpn он попадать не должен.
        own_sku = var_sku if var_sku and var_sku != parent_sku else None

        label = variant_label or var_raw.get("variation") or None
        base_title = (parent_raw.get("name") or var_raw.get("name") or "").strip()
        title = f"{base_title} — {label}" if label else base_title

        return Product(
            offer_id=self.offer_id(var_raw["id"]),
            item_group_id=self.offer_id(parent_raw["id"]),
            source_id=var_raw["id"],
            sku=var_sku,
            title=title,
            description=strip_html(
                parent_raw.get("description") or parent_raw.get("short_description")
            ),
            link=var_raw.get("permalink") or parent_raw.get("permalink", ""),
            image_link=image,
            additional_image_links=extra,
            price=money(var_raw.get("prices") or {}),
            sale_price=(
                money(var_raw.get("prices") or {}, "sale_price")
                if var_raw.get("on_sale") else None
            ),
            currency=(var_raw.get("prices") or {}).get("currency_code", "UAH"),
            availability=availability_of(var_raw),
            brand=extract_brand(parent_raw),
            mpn=own_sku,
            product_type=category_path(parent_raw.get("categories") or []),
            product_details=extract_details(parent_raw),
            variant_label=label,
        )

    # --- сборка -------------------------------------------------------

    @staticmethod
    def _term_name_map(parent_raw: dict[str, Any]) -> dict[tuple[str, str], str]:
        """(имя атрибута, слуг) -> человекочитаемое название термина."""
        mapping: dict[tuple[str, str], str] = {}
        for attr in parent_raw.get("attributes") or []:
            attr_name = attr.get("name")
            for term in attr.get("terms") or []:
                slug, name = term.get("slug"), term.get("name")
                if attr_name and slug and name:
                    mapping[(attr_name, slug)] = name
        return mapping

    def expand(self, catalog: Iterable[dict[str, Any]]) -> Iterator[Product]:
        """Каталог -> плоский список товаров и вариантов.

        Родитель вариативного товара в фид НЕ попадает: у него диапазон
        цен, а не цена. Вместо него идут его варианты.
        """
        for raw in catalog:
            if raw.get("type") != "variable":
                yield self.simple_to_product(raw)
                continue

            # В raw["variations"] значения атрибутов — СЛУГИ ("bilyj-bilyj"),
            # а человекочитаемые названия ("Білий + Білий") лежат в
            # raw["attributes"][].terms. Без этой развёртки в заголовок
            # товара уезжает транслит.
            term_names = self._term_name_map(raw)
            labels = {
                v["id"]: ", ".join(
                    f'{a.get("name")}: '
                    f'{term_names.get((a.get("name"), a.get("value")), a.get("value"))}'
                    for a in (v.get("attributes") or [])
                )
                for v in (raw.get("variations") or [])
            }
            if not labels:
                log.warning(
                    "вариативный товар %s без вариантов — пропущен", raw.get("id")
                )
                continue

            for var_id in labels:
                try:
                    var_raw = self.fetch_variation(var_id)
                except httpx.HTTPError as exc:
                    log.error("вариант %s не получен: %s", var_id, exc)
                    continue
                yield self.variation_to_product(var_raw, raw, labels[var_id] or None)

    def run(self) -> list[Product]:
        return list(self.expand(self.fetch_catalog()))
