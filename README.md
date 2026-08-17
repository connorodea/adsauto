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

Extracts a product catalogue from WooCommerce, converts it to the
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

---

## License

MIT
