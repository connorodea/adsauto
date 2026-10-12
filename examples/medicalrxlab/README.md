# MedicalRxLab example run

Generated 2026-10-12 from a live `GET https://medicalrxlab.com/api/catalog`
(sha256 `0802caec977b8cf0fb37b761d73baf0aefc9eb17f56ab50459ab633b3c54f3b0`, fetched 2026-10-12T23:25:17.239350+00:00).

```bash
python -m adsauto.cli feed --site https://medicalrxlab.com --connector medicalrxlab \
  --prefix medicalrxlab --out feeds/medicalrxlab.xml --json feeds/medicalrxlab-report.json
```

| File | What |
|---|---|
| `medicalrxlab.xml` | Merchant Center RSS 2.0 feed, 294 items (one per purchasable variant) |
| `medicalrxlab-report.json` | validator report ({'F084': 23}), policy pre-check ({'P003': 272}), connector notes, provenance |
| `medicalrxlab-raw.json` | the raw API response the feed was built from, with sha256 and fetch time |

Result on this run: 294 items, 0 would be rejected by Google, 0 errors,
272 policy warnings (all P003: regulated drug names such as
HGH/somatropin, tirzepatide, retatrutide) on 78 items.
