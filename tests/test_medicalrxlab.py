"""MedicalRxLab connector and Merchant Center policy pre-check.

Fixture: a trimmed copy of a live GET https://medicalrxlab.com/api/catalog
taken 2026-10-12 (seven products, every variant kept). Not invented data.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

import pytest

from adsauto.feed import build as feed_build
from adsauto.feed import policy
from adsauto.feed.validate import validate
from adsauto.models import Product
from adsauto.sources.medicalrxlab import (
    MedicalRxLabExtractor,
    describe,
    dose_token,
    money,
    pack_token,
    parse_sku,
    product_class,
)

FIXTURES = Path(__file__).parent / "fixtures"
CATALOG = FIXTURES / "medicalrxlab_catalog.json"


class OfflineExtractor(MedicalRxLabExtractor):
    """Same extractor, catalog from disk instead of the network."""

    def __init__(self) -> None:
        super().__init__("https://medicalrxlab.com", client=object())  # type: ignore[arg-type]

    def fetch_catalog(self):
        return self.load_catalog(CATALOG)


@pytest.fixture
def extractor() -> OfflineExtractor:
    return OfflineExtractor()


@pytest.fixture
def products(extractor):
    return extractor.run()


def by_id(products, offer_id: str) -> Product:
    return next(p for p in products if p.offer_id == offer_id)


# --- sku.mjs port -----------------------------------------------------


@pytest.mark.parametrize(
    "label, token",
    [
        ("5mg", "5MG"),
        ("5mg · 1 vial", "5MG"),
        ("0.1mg", "100MCG"),
        ("5,000 IU", "5000IU"),
        ("20mg total (10mg + 10mg)", "20MG"),
        ("60mg x 10 vials", "60MG"),
        ("10mL kit", "10MLKIT"),
        ("2.5mg", "2P5MG"),
        ("10 IU", "10IU"),
    ],
)
def test_dose_token_matches_sku_mjs(label, token):
    assert dose_token(label) == token


def test_dose_token_refuses_label_without_dose():
    with pytest.raises(ValueError):
        dose_token("as listed")


def test_pack_token():
    assert pack_token(1, "single") == "1V"
    assert pack_token(10, "kit") == "BX10"
    with pytest.raises(ValueError):
        pack_token(None, "mystery")


def test_parse_sku():
    assert parse_sku("MRX-BPC157-5MG-1V") == {
        "code": "BPC157", "blend": False, "dose": "5MG", "pack": "1V",
    }
    assert parse_sku("MRX-BPCTB-BLEND-10MG-BX10")["blend"] is True
    assert parse_sku("MRXW-BPC157-5MG-BX10") is None
    assert parse_sku("") is None


def test_every_fixture_sku_agrees_with_its_label(extractor, products):
    """The site's SKU and our port of sku.mjs must give the same tokens."""
    assert extractor.skipped == []
    assert len(products) == 58


def test_variant_with_disagreeing_sku_is_skipped_not_renamed(extractor):
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    product = catalog["products"][0]
    variant = dict(product["variants"][0], sku="MRX-5A1MQ-10MG-BX10")  # label says 5mg
    assert extractor.variant_to_product(product, variant) is None
    assert extractor.skipped and "disagrees" in extractor.skipped[0]["reason"]


def test_variant_without_sku_is_skipped(extractor):
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    product = catalog["products"][0]
    variant = dict(product["variants"][0], sku="")
    assert extractor.variant_to_product(product, variant) is None
    assert "no D2C SKU" in extractor.skipped[0]["reason"]


# --- field mapping ----------------------------------------------------


def test_offer_id_is_the_d2c_sku_and_group_is_product_code(products):
    p = by_id(products, "MRX-5A1MQ-5MG-1V")
    assert p.item_group_id == "5A1MQ"
    assert p.mpn == "MRX-5A1MQ-5MG-1V"
    assert p.sku == p.offer_id
    assert len({q.offer_id for q in products}) == len(products)


def test_title_shape(products):
    assert by_id(products, "MRX-5A1MQ-5MG-1V").title == "5-amino-1MQ 5mg – Single vial"
    assert by_id(products, "MRX-5A1MQ-5MG-BX10").title == "5-amino-1MQ 5mg – Box of 10"
    assert all(len(p.title) <= 150 for p in products)


def test_variant_label_distinguishes_pack_and_dose(products):
    labels = {p.variant_label for p in products if p.item_group_id == "5A1MQ"}
    assert "5mg · Single vial" in labels and "5mg · Box of 10" in labels
    assert len(labels) == 8


def test_link_is_products_handle_with_variant_query(products):
    p = by_id(products, "MRX-5A1MQ-5MG-BX10")
    assert p.link == "https://medicalrxlab.com/products/5-amino-1mq?variant=bg-001-01"


def test_image_is_absolute_box_render_per_dose(products):
    assert (
        by_id(products, "MRX-5A1MQ-5MG-1V").image_link
        == "https://medicalrxlab.com/product-images/5-amino-1mq-5-mg-box.webp"
    )
    assert (
        by_id(products, "MRX-5A1MQ-10MG-1V").image_link
        == "https://medicalrxlab.com/product-images/5-amino-1mq-10-mg-box.webp"
    )
    assert all(p.image_link.startswith("https://") for p in products)


def test_image_falls_back_to_box_then_vial():
    product = {
        "id": "x", "handle": "x", "title": "X", "description": "X is a peptide.",
        "category": "Compounds",
        "image": {"url": "/product-images/x.webp", "box": "", "boxByDose": {}},
    }
    variant = {"id": "x-01-1v", "sku": "MRX-X1-5MG-1V", "title": "5mg · 1 vial",
               "amount": 10, "vials": 1, "pack": "single", "dose": "x-01",
               "availabilityStatus": "in_stock"}
    p = MedicalRxLabExtractor(client=object()).variant_to_product(product, variant)  # type: ignore[arg-type]
    assert p.image_link == "https://medicalrxlab.com/product-images/x.webp"


def test_price_is_single_unit_in_usd(products):
    assert by_id(products, "MRX-5A1MQ-5MG-1V").price == Decimal("20.99")
    assert by_id(products, "MRX-5A1MQ-5MG-1V").price_str() == "20.99 USD"
    assert by_id(products, "MRX-5A1MQ-5MG-BX10").price == Decimal("167.99")


def test_money_prefers_cents_field_when_present():
    assert money({"unitAmount": 2099, "amount": 999}) == Decimal("20.99")
    assert money({"amount": 230}) == Decimal("230.00")
    assert money({}) is None


def test_availability_from_stock(products):
    assert by_id(products, "MRX-5A1MQ-5MG-1V").availability == "in_stock"
    assert by_id(products, "MRX-5A1MQ-10MG-1V").availability == "out_of_stock"


def test_merchant_constants(products):
    for p in products:
        assert p.brand == "MedicalRxLab"
        assert p.condition == "new"
        assert p.identifier_exists is False
        assert p.gtin is None
        assert p.currency == "USD"
        assert p.google_product_category == (
            "Business & Industrial > Science & Laboratory > Laboratory Supplies"
        )
        assert p.source == "medicalrxlab"


def test_product_type_per_class(products):
    assert by_id(products, "MRX-BACWATER-3ML-1V").product_type == "Laboratory supplies"
    assert by_id(products, "MRX-5A1MQ-5MG-1V").product_type == "Research compounds"
    assert by_id(products, "MRX-RETA-5MG-1V").product_type == "Research peptides"
    assert by_id(products, "MRX-BPCTB-BLEND-10MG-1V").product_type == "Research peptides > Blends"
    assert product_class({"category": "Compounds", "description": "Epithalon is a bioregulator peptide."}) == "Bioregulators"


def test_draft_and_noindex_products_are_not_public(extractor):
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    catalog["products"][0]["draft"] = True
    catalog["products"][1]["noindex"] = True
    out = list(extractor.expand(catalog))
    ids = {p.item_group_id for p in out}
    assert "5A1MQ" not in ids and "ADIPO" not in ids
    assert len(extractor.skipped) == 2


# --- descriptions -----------------------------------------------------


def test_placeholder_description_replaced_by_field_derived_text(products, extractor):
    p = by_id(products, "MRX-ADIPO-2MG-1V")
    assert "sourcing catalog" not in p.description
    assert p.description.startswith("Adipotide: research peptide for laboratory research")
    assert "research use only" in p.description.lower()
    assert any(n.startswith("bg-007: placeholder") for n in extractor.notes)


def test_claim_and_human_use_sentences_are_dropped(extractor):
    product = {
        "id": "t", "title": "T", "size": "5mg · 1 vial", "category": "Compounds",
        "description": "<p>T is a 5-residue peptide. It <b>treats</b> inflammation and "
                       "burns fat. Inject 250mcg daily. Research areas: cell signaling.</p>",
    }
    text = describe(product, extractor.notes)
    assert text == (
        "T is a 5-residue peptide. Research areas: cell signaling. "
        "Supplied for laboratory research use only. Not for human or veterinary use."
    )
    assert "<" not in text
    assert sum(1 for n in extractor.notes if n.startswith("t: dropped")) == 2


def test_real_lipoc_injectable_sentence_dropped(products):
    p = by_id(products, "MRX-LC120-10MG-1V")
    assert "injectable" not in p.description.lower()
    assert "research use only" in p.description.lower()


def test_notes_recorded_once_per_product_not_per_variant(extractor, products):
    placeholder_notes = [n for n in extractor.notes if "placeholder" in n]
    assert len(placeholder_notes) == len({n.split(":")[0] for n in placeholder_notes})


# --- validator, feed, provenance --------------------------------------


def test_validator_has_no_errors_on_real_catalog(products):
    feed_build.apply_identifier_policy(products)
    report = validate(products)
    assert report.errors == []
    assert report.rejection_rate == 0.0


def test_identifier_policy_keeps_explicit_false(products):
    feed_build.apply_identifier_policy(products)
    assert all(p.identifier_exists is False for p in products)


def test_xml_feed_round_trip(products, tmp_path):
    out = feed_build.write(products, tmp_path / "mrx.xml", title="medicalrxlab",
                           link="https://medicalrxlab.com")
    root = ET.parse(out).getroot()
    ns = {"g": "http://base.google.com/ns/1.0"}
    items = root.findall("./channel/item")
    assert len(items) == len(products)
    first = next(i for i in items if i.find("g:id", ns).text == "MRX-5A1MQ-5MG-1V")
    assert first.find("g:price", ns).text == "20.99 USD"
    assert first.find("g:identifier_exists", ns).text == "false"
    assert first.find("g:item_group_id", ns).text == "5A1MQ"
    assert first.find("g:mpn", ns).text == "MRX-5A1MQ-5MG-1V"
    assert first.find("g:link", ns).text.endswith("?variant=bg-001-01-1v")
    assert "<" not in first.find("g:description", ns).text


def test_raw_response_is_kept_and_saved(extractor, products, tmp_path):
    assert extractor.raw_response is not None
    assert extractor.raw_sha256 and len(extractor.raw_sha256) == 64
    assert extractor.fetched_at
    out = extractor.save_raw(tmp_path / "raw.json")
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["sha256"] == extractor.raw_sha256
    assert saved["endpoint"] == "https://medicalrxlab.com/api/catalog"
    assert saved["payload"]["products"][0]["id"] == "bg-001"
    assert extractor.provenance()["source"] == "medicalrxlab"


# --- policy pre-check (deterministic) ---------------------------------


def _product(title: str, description: str) -> Product:
    return Product(offer_id="t-1", title=title, description=description)


def test_policy_flags_claims_with_offending_text():
    issues = policy.check_product(_product("BPC-157 5mg", "BPC-157 treats injuries and promotes fat loss."))
    codes = {i.code for i in issues}
    assert "P001" in codes
    texts = [i.message for i in issues if i.code == "P001"]
    assert any("'treats'" in t for t in texts)
    assert any("'fat loss'" in t for t in texts)


def test_policy_flags_human_dosing():
    issues = policy.check_product(_product("X", "Inject 250 mcg subcutaneously twice daily, 0.5 mg/kg."))
    hits = {i.message.split("'")[1].lower() for i in issues if i.code == "P002"}
    assert {"inject", "subcutaneously", "twice daily", "mg/kg"} <= hits


def test_policy_flags_regulated_drug_names_in_title():
    issues = policy.check_product(_product("HGH 10 IU – Single vial", "Somatropin, not approved by the FDA."))
    p003 = [i for i in issues if i.code == "P003"]
    assert any("in title" in i.message and "'HGH'" in i.message for i in p003)
    assert any("'not approved by the FDA'" in i.message for i in p003)
    assert all(i.severity == "warning" for i in issues)


def test_policy_flags_placeholder_descriptions():
    issues = policy.check_product(_product("X", "X is listed in our sourcing catalog with 2 options."))
    assert [i.code for i in issues] == ["P004"]


def test_policy_silent_on_clean_research_text():
    text = ("5-amino-1MQ is a small-molecule quinolinium salt. Research areas: NNMT enzymology "
            "in cell models. Supplied for laboratory research use only.")
    assert policy.check_product(_product("5-amino-1MQ 5mg – Single vial", text)) == []


def test_policy_report_on_real_catalog(products):
    report = policy.check(products)
    assert report.total == len(products)
    assert report.by_code().get("P003", 0) > 0        # HGH, retatrutide, tirzepatide names
    assert "P001" not in report.by_code()              # claims stripped by the connector
    assert "P004" not in report.by_code()              # placeholders replaced by the connector
    assert "Healthcare" in report.summary()
