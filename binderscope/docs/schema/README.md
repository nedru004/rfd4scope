# docs/schema

The machine-readable data contract of Binderscope: the JSON Schemas of every data file the viewer reads, plus the registry of the profile text keys.

| File | What |
|---|---|
| `common.schema.json` | shared definitions (residue lists, ranges, coordinates, ids, filter failure) |
| `campaign.schema.json` | `data/campaign.json` (RAW): target, designs, runs, modes, fold, diversity |
| `inspect.schema.json` | `data/inspect.json` (INS): per-design structure, contacts, tracks, context |
| `homolog.schema.json` | `data/homolog.json` (HOM): conservation, orthologs, paralogs (empty lists when no specificity panel was given) |
| `profile.schema.json` | the profile: identity, frame, specificity, capability flags, checks, text templates |
| `manifest.schema.json` | `data/manifest.json`: provenance, hashes, thresholds, validation, privacy |
| `review_event.schema.json` | one line of `reviews/<reviewer>.jsonl` |
| `profile.keys.json` | registry of the text keys of a profile: the page that reads each key, what it is for and the tokens it supplies (a key written with `<entry>` stands for one key per entry of `specificity.detail`) |
| `examples/` | example profiles and example data |

The exporter validates every blob it writes against these schemas (`binder_review.export`), and `scripts/validate_schema.py` checks the rules a JSON Schema cannot express (array lengths, index ranges, numbering, capability flags against the data, cross rules between blobs).

Validate (from a source checkout):

```
python scripts/validate_schema.py --examples        # the profiles and example data in docs/schema/examples
python scripts/validate_schema.py --bundle DIR      # a bundle folder (data/*.json, reviews/*.jsonl)
python scripts/validate_schema.py --kind campaign FILE.json
```

Change the contract:

1. Edit the schema (add the field with a `description`; mark it `required` only if a page cannot draw without it).
2. Add or adjust a semantic rule in `scripts/validate_schema.py` and a test.
3. A text key of a profile is listed in `profile.keys.json` with its tokens; the validator reports unknown keys and tokens.
