"""Тесты на реальных данных ESTACA.

Фикстуры сняты с живого Store API 17.08.2026 — это не выдуманные примеры.
Каждый тест закрывает конкретную ошибку, которая иначе уехала бы в
Merchant Center.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from adsauto.feed import build as feed_build
from adsauto.feed.validate import validate
from adsauto.models import strip_html
from adsauto.sources.woo import WooExtractor, money

FIXTURES = Path(__file__).parent / "fixtures"


class FakeExtractor(WooExtractor):
    """Тот же экстрактор, но вместо сети — фикстуры."""

    def __init__(self) -> None:
        self.base = "https://www.estaca.com.ua"
        self.prefix = "estaca"
        self._variations = json.loads(
            (FIXTURES / "estaca_variations.json").read_text(encoding="utf-8")
        )

    def fetch_catalog(self, per_page: int = 100):
        return json.loads((FIXTURES / "estaca_sample.json").read_text(encoding="utf-8"))

    def fetch_variation(self, variation_id: int):
        return self._variations[str(variation_id)]


@pytest.fixture
def products():
    return FakeExtractor().run()


# --- цены -------------------------------------------------------------


def test_price_converted_from_minor_units():
    """430000 при minor_unit=2 — это 4300.00, а не 430 000."""
    assert money({"price": "430000", "currency_minor_unit": 2}) == Decimal("4300.00")


def test_price_survives_pipeline(products):
    assert all(p.price == Decimal("4300.00") for p in products)
    assert all(p.price_str() == "4300.00 UAH" for p in products)


# --- варианты ---------------------------------------------------------


def test_parent_excluded_variants_present(products):
    """Родитель вариативного товара в фид не идёт — у него нет своей цены."""
    ids = {p.offer_id for p in products}
    assert ids == {"estaca-10520", "estaca-10521", "estaca-10522"}
    assert "estaca-10519" not in ids


def test_variants_share_item_group(products):
    assert {p.item_group_id for p in products} == {"estaca-10519"}


def test_offer_id_not_derived_from_sku(products):
    """У всех трёх вариантов ESTACA sku = 132-60.

    Идентификатор обязан строиться от ID в CMS, иначе три товара
    схлопнутся в один.
    """
    assert len({p.sku for p in products}) == 1
    assert len({p.offer_id for p in products}) == 3


def test_shared_parent_sku_not_used_as_mpn(products):
    """MPN, совпадающий у всех вариантов, — ложный идентификатор."""
    assert all(p.mpn is None for p in products)


def test_variant_titles_differ(products):
    titles = {p.title for p in products}
    assert len(titles) == 3
    assert any("Білий" in t for t in titles)


def test_variant_images_differ(products):
    assert len({p.image_link for p in products}) == 3


# --- наследование от родителя ----------------------------------------


def test_variant_inherits_brand_and_category(products):
    """Store API отдаёт у варианта пустые brands и categories."""
    assert all(p.brand == "ESTACA" for p in products)
    assert all(p.product_type == "Розкладні мобільні столи" for p in products)


def test_variant_inherits_description(products):
    assert all("Розкладний стіл" in p.description for p in products)


def test_dimensions_kept_as_details(products):
    details = dict(products[0].product_details)
    assert details["Ширина, мм"] == "1000"
    assert details["Глибина, мм"] == "500"
    assert "Кольори" not in details


# --- очистка HTML -----------------------------------------------------


def test_description_has_no_html(products):
    for p in products:
        assert "<" not in p.description and ">" not in p.description


def test_strip_html_keeps_list_items():
    out = strip_html("<ul><li>перший</li><li>другий</li></ul>")
    assert "перший" in out and "другий" in out and "<" not in out


# --- идентификаторы ---------------------------------------------------


def test_identifier_policy_marks_absent(products):
    """Собственное производство без GTIN → identifier_exists=false."""
    assert all(p.identifier_exists for p in products)
    feed_build.apply_identifier_policy(products)
    assert all(not p.identifier_exists for p in products)


# --- валидатор --------------------------------------------------------


def test_validator_flags_missing_identifier_before_policy(products):
    codes = {i.code for i in validate(products).issues}
    assert "F051" in codes


def test_validator_clean_after_policy(products):
    feed_build.apply_identifier_policy(products)
    report = validate(products)
    assert report.errors == [], [str(i) for i in report.errors]
    assert report.rejection_rate == 0.0


def test_validator_reports_shared_sku(products):
    codes = {i.code for i in validate(products).issues}
    assert "F081" in codes


def test_validator_catches_unconverted_price(products):
    products[0].price = Decimal("430000")
    codes = {i.code for i in validate(products).issues}
    assert "F022" in codes


def test_validator_catches_html_in_description(products):
    products[0].description = "<p>текст</p>"
    codes = {i.code for i in validate(products).issues}
    assert "F012" in codes


def test_validator_catches_duplicate_ids(products):
    products[1].offer_id = products[0].offer_id
    codes = {i.code for i in validate(products).issues}
    assert "F080" in codes


def test_validator_catches_long_title(products):
    products[0].title = "х" * 200
    codes = {i.code for i in validate(products).issues}
    assert "F003" in codes


def test_validator_catches_missing_brand(products):
    products[0].brand = None
    codes = {i.code for i in validate(products).issues}
    assert "F050" in codes


# --- XML --------------------------------------------------------------


def test_xml_structure(products, tmp_path):
    import xml.etree.ElementTree as ET

    feed_build.apply_identifier_policy(products)
    path = feed_build.write(
        products, tmp_path / "feed.xml", title="ESTACA", link="https://www.estaca.com.ua"
    )
    root = ET.parse(path).getroot()
    ns = {"g": "http://base.google.com/ns/1.0"}
    items = root.findall("./channel/item")
    assert len(items) == 3

    first = items[0]
    assert first.find("g:id", ns).text == "estaca-10520"
    assert first.find("g:item_group_id", ns).text == "estaca-10519"
    assert first.find("g:price", ns).text == "4300.00 UAH"
    assert first.find("g:availability", ns).text == "in_stock"
    assert first.find("g:identifier_exists", ns).text == "false"
    assert first.find("g:brand", ns).text == "ESTACA"
    assert first.find("g:link", ns).text.startswith("https://")
    assert first.find("g:mpn", ns) is None


def test_xml_has_product_details(products, tmp_path):
    import xml.etree.ElementTree as ET

    path = feed_build.write(
        products, tmp_path / "feed.xml", title="ESTACA", link="https://www.estaca.com.ua"
    )
    ns = {"g": "http://base.google.com/ns/1.0"}
    details = ET.parse(path).getroot().findall("./channel/item/g:product_detail", ns)
    names = {d.find("g:attribute_name", ns).text for d in details}
    assert "Ширина, мм" in names


# --- сверка габаритов -------------------------------------------------


def test_dimension_mismatch_found_in_real_data(products):
    """Реальный конфликт в каталоге ESTACA.

    Заголовок: «розкладний стіл для офісу 90×50 см» → 900×500 мм.
    Атрибуты: Ширина 1000 мм, Глибина 500 мм.
    Ширина расходится: 900 против 1000.
    """
    issues = [i for i in validate(products).issues if i.code == "F090"]
    assert issues, "расхождение габаритов должно быть найдено"
    assert "1000" in issues[0].message and "900" in issues[0].message


def test_dimension_check_silent_when_consistent(products):
    for p in products:
        p.product_details = [("Ширина, мм", "900"), ("Глибина, мм", "500")]
    assert not [i for i in validate(products).issues if i.code == "F090"]


def test_seo_tail_detected(products):
    assert [i for i in validate(products).issues if i.code == "F091"]
