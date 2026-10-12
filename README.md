# adsauto

Automation tooling for Google Ads and Google Merchant Center, built for
e-commerce advertisers in Ukraine.

The tool automates the repetitive parts of setting up and maintaining
Google Ads accounts: building a keyword set from real data, generating
product feeds, creating campaigns from those feeds, and reporting on the
result. A human reviews and enables everything that spends money.

*Русская версия: [README.ru.md](README.ru.md)*

---

## Status

| Module | State |
|---|---|
| Product feed extraction and validation | **working**, 27 tests |
| MedicalRxLab connector + Merchant Center policy pre-check | **working**, 41 tests, real feed run 0 errors |
| Feed Doctor — automatic repair of feed errors | in progress |
| Keyword research | design complete, waiting on API access |
| Ad copy generation and review | design complete |
| Campaign publishing | design complete |
| Monitoring and weekly reports | design complete |

---

## Design principles

**The model never invents data.** Keywords come only from APIs that
return real numbers — `KeywordPlanIdeaService`, the search terms report,
Search Console — or from the advertiser's own product catalogue. Every
keyword row in the database carries a `raw_response_id` column declared
`NOT NULL`, referencing the stored raw API response it came from. A
keyword that no API returned cannot be written to the database at all.
This is enforced by the schema, not by a prompt.

**Deterministic checks run before any model is called.** Character
limits, price consistency, duplicate identifiers and broken URLs are
checked in code. Language models count characters unreliably; asking one
to enforce a 30-character headline limit is the wrong tool. Models are
used only where meaning is required: whether ad copy matches the intent
of its keyword cluster, whether a claim about a product is supported by
the feed, and grammar in Ukrainian and Russian.

**Every automated verdict must cite evidence.** A review that marks ad
copy as failing must quote the specific keyword, product attribute or
policy clause it relied on. A verdict without evidence is treated as
invalid and re-requested.

**Nothing spends money without a human.** Campaigns are created in
`PAUSED` status. A person enables them.

---

## What works today

Extracts a product catalogue from WooCommerce (or, with
`--connector medicalrxlab`, from a custom JSON catalog), converts it to the
Merchant Center data model, generates an XML feed and reports every
problem that would cause Google to reject a product.

```bash
python -m adsauto.cli feed \
  --site https://example.com \
  --prefix example \
  --out feeds/example.xml \
  --json feeds/report.json
```

Output:

```
Feed: feeds/example.xml
Products: 412
Would be rejected by Google: 0 (0.0%)
Errors: 0, warnings: 3
```

### Problems this catches

Verified against a live WooCommerce store on 2026-08-17.

**Variant products.** A variable product's parent has a price *range*,
not a price, and must not be sent to Merchant Center. Its variants are
sent instead, joined by `item_group_id`.

**Non-unique SKUs.** In the store used for testing, all three colour
variants of one desk share the SKU `132-60`. Deriving `g:id` from SKU
would collapse three products into one. Identifiers are derived from the
CMS entity ID, which is stable across exports — Merchant Center treats a
changed identifier as a new product and resets its accumulated
statistics.

**Prices in minor units.** WooCommerce returns
`{"price": "430000", "currency_minor_unit": 2}`, which is 4 300, not
430 000. Using the raw field would list a desk at a hundred times its
price. The validator also flags any product priced more than 50× the
feed median, since that pattern almost always means a missing division.

**Fields the API blanks on variants.** The WooCommerce Store API returns
empty `categories`, `brands` and `attributes` for variants; they are
inherited from the parent. Variant attribute values arrive as slugs
(`bilyj-bilyj`) while human-readable names live on the parent — without
expanding them, transliterated slugs end up in product titles.

**Missing GTINs.** Manufacturers selling their own goods have no
barcodes. Without an explicit `identifier_exists: false`, Google expects
a GTIN and rejects the product.

**Dimension mismatches.** A title reading "desk 90×50 cm" alongside
attributes stating a width of 1000 mm means one of the two is wrong.
Google compares titles against the landing page; the buyer compares them
against what arrives.

---

## MedicalRxLab

[medicalrxlab.com](https://medicalrxlab.com) sells research-use-only
peptides and bioregulators: a React storefront with a Node API. Its
WooCommerce instance (`/commerce/`) is a closed backend whose Store API
answers 503 by design, so the WooCommerce connector does not apply. The
public truth is one JSON document, `GET https://medicalrxlab.com/api/catalog`.

```bash
python -m adsauto.cli feed \
  --site https://medicalrxlab.com \
  --connector medicalrxlab \
  --prefix medicalrxlab \
  --out feeds/medicalrxlab.xml \
  --json feeds/medicalrxlab-report.json
```

No credentials or environment variables are needed: the catalog endpoint
is public. Output of the run on 2026-10-12 (kept with the raw response
under [`examples/medicalrxlab/`](examples/medicalrxlab/)):

```
Фид: feeds/medicalrxlab.xml
Raw response: feeds/medicalrxlab-raw.json (sha256 0802caec977b)
Товаров в фиде: 294
Будет отклонено Google: 0 (0.0%)
Ошибок: 0, предупреждений: 0
Policy pre-check (Healthcare & medicines): 272 warnings on 78 of 294 items
  P003: 272
```

### Mapping

One feed item per purchasable variant (dose × pack). Draft, `noindex`
and sample products are skipped.

| Merchant Center | Source |
|---|---|
| `g:id` | the variant's D2C SKU, `MRX-<CODE>[-BLEND]-<DOSE>-<1V\|BX10>`, exactly as the site generates it with `shared/sku.mjs`. The connector re-derives the dose and pack tokens with a port of that module and **skips** a variant whose SKU disagrees with its own label rather than renaming it. Same `g:id` as the site's own `/feeds/google-merchant.xml`. |
| `g:item_group_id` | the product code from the SKU (`BPC157`, `BPCTB`) |
| `g:title` | `<Product> <dose> – Single vial` / `– Box of 10` |
| `g:description` | catalog description, HTML stripped. Import-time placeholders ("listed in our sourcing catalog…") are replaced by text built only from catalog fields; any sentence with claim or dosing wording (the P001/P002 patterns below) is dropped. Drug names stay — they are the product's identity. Every change is listed under `notes` in the JSON report. |
| `g:link` | `https://medicalrxlab.com/products/<handle>?variant=<variant id>` (`/product/<handle>` is a 404) |
| `g:image_link` | the dose's box render (`image.boxByDose`), else the product box, else the vial image, made absolute |
| `g:availability` | `availabilityStatus` → `in_stock` / `out_of_stock` (unavailable variants stay in the feed so their ids are stable) |
| `g:price` | single-unit price, `XX.XX USD`; `amount` is dollars in the live payload, `unitAmount` (cents) wins if present |
| `g:brand` / `g:condition` | `MedicalRxLab` / `new` |
| `g:mpn` / `g:identifier_exists` | the SKU / `false` — no GTINs exist for these items. Merchant Center may warn that an MPN was supplied alongside `identifier_exists=false`; that is informational. |
| `g:google_product_category` | `Business & Industrial > Science & Laboratory > Laboratory Supplies` |
| `g:product_type` | `Research peptides`, `Research peptides > Blends`, `Bioregulators` (when the catalog text calls the item one), `Research compounds` (text says "not a peptide"), `Laboratory supplies` |
| `g:product_detail` | Strength, Pack, Intended use = laboratory research only |

**Shipping is not in the feed on purpose.** The site ships to the US with
free shipping from $100; a per-item `g:shipping` of 0 would be wrong for
orders under $100. Configure it once in Merchant Center (below).

**Every row cites its raw response.** The connector keeps the whole
`/api/catalog` payload, writes it next to the feed as
`<feed>-raw.json` with its sha256 and fetch time, and the JSON report
carries the same `provenance` block.

### Policy pre-check (P001–P004)

Google's *Healthcare and medicines* policy restricts unapproved
pharmaceuticals and supplements, prescription drugs, health claims and
human-use instructions. A research-peptide catalogue is squarely in that
zone — the product names alone (somatropin/HGH, tirzepatide, retatrutide,
GLP-1) are on Google's regulated lists. **This catalog is likely to hit
that policy. Nothing here predicts or promises approval.** The pre-check
is deterministic code that reports the offending text so the owner can
decide before Google does.

| Code | Flags | Example from this catalog |
|---|---|---|
| P001 | health/efficacy claim — treats, cures, fat loss, muscle growth, anti-aging, boosts… | none after the connector's sentence filter |
| P002 | human-use or dosing wording — inject, dosage, mg/kg, per day, subcutaneous… | "injectable formulation" (Lipo-C), dropped by the connector |
| P003 | regulated or prescription drug reference — somatropin, HGH, tirzepatide, semaglutide, insulin, "not approved by the FDA"… | `HGH 10 IU – Single vial`, "Retatrutide … is not approved by the FDA" |
| P004 | placeholder description from the catalog import | "listed in our sourcing catalog with 2 options" — 52 of 64 products on 2026-10-12, replaced in the feed; the site pages still show them |

All four are warnings. They appear in the CLI output and under `policy`
in the JSON report, with the matched word and a text excerpt.

### Merchant Center and Google Ads: what the owner does

1. **Claim and verify `medicalrxlab.com`** in Merchant Center (Business
   information → Website): HTML tag, DNS record or Google Search Console.
   The feed's `g:link` values must be on the claimed domain.
2. **Shipping** (Shipping and returns): one US service, flat rate, with a
   free-shipping threshold at **$100.00**. This is where "free ≥ $100"
   lives — not in the feed.
3. **Tax** (US): choose Google-determined sales tax or enter the states
   where tax is collected; leave the feed without `g:tax`.
4. **Add the feed** (Products → Add products → Add products from a file →
   *Scheduled fetch*), daily, pointing at the published feed URL. The
   site also serves `/feeds/google-merchant.xml` (built by the storefront
   from the same catalog with the same SKUs); Merchant Center can fetch
   that URL directly. adsauto's role is validation, the policy pre-check
   and the later keyword and campaign modules, not hosting the feed.
5. **Write real product descriptions** on the site for the P004 items.
   Google compares feed text with the landing page.
6. **Expect a Healthcare & medicines review.** Items flagged P003 may be
   disapproved for Shopping ads or require pharmacy/healthcare
   certification. Products with no P003 hits are the ones to put in the
   first Shopping campaign. Google Ads campaigns adsauto creates are
   always `PAUSED`; a person enables them.

Tests: `tests/test_medicalrxlab.py` (fixture = a trimmed copy of the
live `/api/catalog` response from 2026-10-12).

---

## Architecture

```
Connectors → Postgres → Keyword research → Generation → Review → Publishing → Monitoring → Reports
                  ↑_________________ feedback from live metrics ________________|
```

Eleven tables. The ones that carry the design:

- `api_responses` — every raw API response, stored whole. The anchor for
  every derived fact.
- `keywords` — `source`, `source_class` (observed vs estimated) and
  `raw_response_id NOT NULL`.
- `qa_reviews` — every automated verdict with its evidence, so the
  checks themselves can be measured over time.
- `actions_log` — what changed, who changed it, and why.

---

## Stack

Python 3.12, PostgreSQL 16, Docker Compose. No dependencies beyond
`httpx`, `psycopg` and `pytest`.

```bash
cp .env.example .env
docker compose up -d db          # schema applies automatically
docker compose run --rm app python -m pytest
```

---

## Validation rules

Severity: `error` — Google rejects the product; `warning` — accepted but
degraded; `info` — no effect on Google, relevant to downstream
automation.

| Codes | Area |
|---|---|
| F001–F005 | identifier, title length, casing, promotional text |
| F010–F012 | description: empty, too long, leftover HTML |
| F020–F023 | price: missing, zero, sale above regular, outlier |
| F030 | availability value |
| F040–F044 | links and images |
| F050–F052 | brand, GTIN, `identifier_exists` |
| F060–F061 | Google product category, product type |
| F070 | variant without a distinguishing label |
| F080–F084 | feed-wide: duplicate ids, shared SKUs, duplicate titles |
| F090–F091 | dimension mismatch, SEO suffix in title |
| P001–P004 | Merchant Center *Healthcare and medicines* pre-check: claims, dosing wording, regulated drug names, placeholder descriptions (warnings) |

---

## License

MIT
