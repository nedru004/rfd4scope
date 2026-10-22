# Public reference data

Binderscope reads a few public records about the target: its UniProt entry, the PDBe/SIFTS residue mappings of the reference structure, AlphaFold DB models and, as an option, ConSurf-DB grades. This page says which records are used and why, how the offline-first cache works (`src/binder_review/refdata.py`), what is sent over the network, what happens when a record is missing and under which terms the data come. It is for developers and advanced users. Nothing else in the package talks to the network.

The public repository ships no cache. Records are fetched by accession, once, when a build is allowed to (`--online`, see [cli.md](cli.md)), and are stored in the cache from then on.

## Records and who uses them

| Source | Cache path under the cache folder | Request | Used by |
|---|---|---|---|
| UniProt entry (JSON) | `uniprot/<ACC>.json` | `https://rest.uniprot.org/uniprotkb/<ACC>.json` | target frame: topology, domains of the strip, glycosylation sites, disulfide bonds, isoforms, functional sites; specificity: organism, gene and review status of every panel member |
| UniProt sequence (FASTA) | `uniprot/<ACC>.fasta` | `https://rest.uniprot.org/uniprotkb/<ACC>.fasta` | the reference sequence: numbering check of the target frame and every alignment of the specificity panel |
| UniProt search (TSV) | `uniprot/search/<tag>.tsv` | `https://rest.uniprot.org/uniprotkb/search?query=...` by gene symbol, taxon id and, optionally, one protein-name word | choosing orthologs ahead of a build (`search_uniprot`); not used during a build |
| PDBe / SIFTS mappings | `pdbe/<pdb>/mappings_<kind>.json`, kind one of `uniprot`, `uniprot_segments`, `pfam`, `interpro`, `cath` | `https://www.ebi.ac.uk/pdbe/api/mappings/<kind>/<pdb>` | target frame: the SIFTS segments are a second witness for the numbering, CATH gives the domains of the frame strip, Pfam and InterPro the finer spans |
| PDBe observed residues | `pdbe/<pdb>/unobserved_residues.json`, else `pdbe/<pdb>/polymer_coverage.json` | `https://www.ebi.ac.uk/pdbe/api/pdb/entry/...` | `get_unobserved` (falls back to the coverage record when PDBe has no unobserved-residue record); not used during a build, the frame reads the observed residues from the reference file itself |
| AlphaFold DB | `alphafold/<ACC>/prediction.json`, the model file and, on request, the PAE file | `https://alphafold.ebi.ac.uk/api/prediction/<ACC>`; the model and PAE URLs come from that answer | target frame: a plausibility check of the membrane plane; specificity: the structural alignment of paralogs onto the reference model |
| ConSurf-DB (optional) | `consurfdb/<PDB>_<chain>_consurf_grades.txt` | `https://consurfdb.tau.ac.il/results/<PDB>/<chain>/<PDB>_<chain>_consurf_grades.txt` | specificity: the optional grade overlay, only with `--consurf` |
| Panel file | `panel.json` | written by `scripts/fetch_reference.py --config` | specificity: the details of orthologs and paralogs that a profile names without them, and the species of a profile that names none ([specificity.md](specificity.md)) |

`<ACC>` is a UniProt accession and `<pdb>` a PDB id. The SIFTS and ConSurf-DB records are looked up by the PDB id of the reference structure, so they need a reference that is a PDB entry named by its four-character id (the id of the reference structure is taken from its file name unless it is given explicitly, see [cli.md](cli.md)). Without a UniProt accession for the target the frame reads no UniProt, SIFTS or AlphaFold record at all ([target_frame.md](target_frame.md)). The PAE matrix of an AlphaFold model is fetched only when a caller asks for it, and parsed only when the file is smaller than 4 MB; a build does not ask for it.

## The cache

- **Place.** `data/reference/` in the source tree, or the folder named by the environment variable `BINDERSCOPE_REFDATA_DIR` (a different folder for tests, a read-only install or a cache shared by several checkouts).
- **Hit and miss.** A hit returns the stored file as it is. A miss fetches the record once, stores the raw bytes and records a manifest entry. Records are keyed by accession or PDB id, so every project that needs the same record shares it.
- **Manifest.** `manifest.json` in the cache folder has one entry per path, relative to the cache folder: `url`, `retrieved` (date), `sha256`, `bytes`, `status` (HTTP status), `content_type` and `file`. `refdata.verify_cache()` re-checks every file against its entry (missing file, size, sha256) and returns the problems; it repairs nothing.
- **Writes are safe.** A download goes to a `.part` file that is renamed when it is complete, and the manifest is rewritten atomically under a file lock (`fcntl`, so the cache needs a POSIX system). A failed download leaves the existing cache untouched.
- **Refreshing.** Delete a file and its manifest entry; the next allowed fetch stores it again.
- **In the bundle.** The manifest of a bundle lists the cached records the build used (`reference[]`: id, source, url, retrieved date, sha256, bytes) and carries a licence list. `privacy.network_requests` is the number of records that the build added to the cache; it is 0 for a build that only read the cache.

## Online and offline

| Setting | Effect |
|---|---|
| default build | offline: the pipeline sets `BINDERSCOPE_OFFLINE=1` for the duration of the build, so a cache miss is an error or a warning, never a request |
| `--online` | the build may fetch missing records by accession and cache them. It does not clear `BINDERSCOPE_OFFLINE`: if that variable is already set in the environment the build stays offline |
| `BINDERSCOPE_OFFLINE` | any value other than empty, `0`, `false` or `no` turns a miss into `RefDataOffline`, whose message names the record and its source URL |
| `BINDERSCOPE_REFDATA_DIR` | the cache folder |
| `scripts/fetch_reference.py` | fills the cache ahead of time with network access, so that later builds can run offline; records that are already cached are not fetched again (see below) |

## What is sent

Only accessions, PDB ids, chain ids, gene symbols and taxon ids ever go into a request. Every request builder checks its values against a pattern before a URL exists, so a binder sequence or a design id raises `ValueError` instead of leaving the machine.

| Value | Check |
|---|---|
| UniProt accession | the UniProt accession format (6 or 10 characters), upper-cased |
| PDB id | a digit followed by three letters or digits, lower-cased |
| chain id | one to four letters or digits |
| gene symbol (and the protein-name word of a search) | starts with a letter or digit, then letters, digits, `_`, `.` or `-`, at most 20 characters |
| taxon id | an integer from 1 to 99999999 |

Every request goes through one function that refuses any host that is not on an allow-list: `rest.uniprot.org`, `www.ebi.ac.uk`, `alphafold.ebi.ac.uk`, `consurfdb.tau.ac.il`, `files.rcsb.org`, `rest.ensembl.org` and `cdnjs.cloudflare.com`. The check also covers URLs that a server hands back (the model and PAE files of an AlphaFold answer). No getter of `refdata.py` uses the last three hosts.

A request is a plain HTTPS GET with a `User-Agent` that says it is a protein-design triage tool fetching public reference data. There is no account, no cookie and no credential. Requests to one host are spaced at least 0.4 s apart; the timeout is 60 s; a request is tried up to three times with waits of 2, 4 and 8 s, and only network errors and the HTTP statuses 408, 425, 429, 500, 502, 503 and 504 are retried.

## Failure modes

| Situation | Result |
|---|---|
| record not cached and the build is offline | `RefDataOffline` (a `RefDataError`). The target frame turns it into a warning in `frame.meta["warnings"]` (and so into the manifest's warnings) and leaves out what needs the record: the annotations of the strip, the membrane plane, the numbering check against UniProt. The specificity stage skips a species or paralog it cannot get and says so in `generated.warnings`; it needs the target's own UniProt record, and without it the stage stops with the error |
| network error or timeout | `RefDataError` after the third try; nothing is stored |
| HTTP 404 | recorded in the manifest (status 404, no file). Later calls know the record does not exist, even offline: they raise `RefDataNotFound`, or return `None` where a getter allows a missing record |
| ConSurf-DB unavailable (offline, 404, network error) | `get_consurfdb` returns `None` and the overlay is simply absent; the rest of the code treats ConSurf-DB as an optional overlay |
| a value that fails its pattern | `ValueError` before any request |
| a host outside the allow-list | `RefDataError`, no request |
| damaged cache | reported by `verify_cache()` |

## Filling the cache ahead of time

`scripts/fetch_reference.py` fetches the records a project needs through the same getters and the same checks as a build, and prints what it stored. Run it with network access, then use the cache offline, or copy the cache folder to another machine. It reads and writes the folder of `BINDERSCOPE_REFDATA_DIR` when that is set.

| Command | Fetches |
|---|---|
| `python scripts/fetch_reference.py --uniprot ACCESSION --reference-id PDBID` | the target: its UniProt entry and sequence, the AlphaFold DB model with its PAE file and, because a PDB id is given, the SIFTS mappings of the reference structure. `--reference-chain` names the chain (default `A`) |
| `python scripts/fetch_reference.py --profile PROFILE.json` | everything a profile names: `target.uniprot` and `target.reference.pdb`, the `specificity.species` entries written as `acc:<accession>` and the `specificity.paralogs.members` (UniProt entry and sequence of each; the AlphaFold DB model of each paralog) |
| `python scripts/fetch_reference.py --config PANEL.json` | a panel chosen by search; writes `panel.json` into the cache. `--uniprot` and `--reference-id` override the target of the file |

Further options: `--consurf` also fetches the ConSurf-DB grades of the reference chain (academic, non-commercial use), `--no-alphafold` skips the AlphaFold DB models, and `--no-vendor`, `--force-vendor` and `--vendor-only` control the viewer library. Unless `--no-vendor` is given, the script also downloads the 3Dmol.js library and its licence into `viewer/vendor/` from two fixed addresses; that step is independent of the reference cache. A reference id that is not a four-character PDB id gets no SIFTS and no ConSurf-DB record. The script ends with `verify_cache()` and exits with 1 when it finds a problem.

**The panel config** is a JSON file; every key except `target` is optional:

```json
{"target": {"accession": "P12345", "pdb": "1abc"},
 "orthologs": {"search_gene": "GENE",
               "fallback_protein_name": "word",
               "paralog_reference": "P23456",
               "min_identity": 0.7, "min_length": 300, "max_length": 400,
               "species": [["Mus musculus", "mouse", 10090, "default"],
                           ["Sus scrofa", "pig", 9823, "optional"]]},
 "paralogs": [["P23456", "GENE2", 9606, "default"]]}
```

(The accessions, gene symbols, taxa and sizes are examples.) A `species` entry is the organism name, a common name, a taxon id and a role, `default` or `optional`. A `paralogs` entry is an accession, the gene symbol and the taxon id the entry must carry (the script stops if they differ) and a role. For each species the script searches UniProt for `search_gene` in that taxon and ranks the rows with `refdata.rank_ortholog_candidates`; if nothing qualifies and `fallback_protein_name` is set, it searches that protein-name word instead, which finds an ortholog whose proteome carries no gene name. It caches the UniProt entry and sequence of the winner and records, for each pick, why it was chosen and which candidates were eligible.

**The ranking rule** (`rank_ortholog_candidates(rows, reference_seq, paralog_seq=None, *, min_identity=0.7, min_length=None, max_length=None, gene=None)`): a row is eligible when it is not a fragment, its length lies between `min_length` and `max_length` (default 0.9 and 1.1 times the length of the reference), its sequence is at least `min_identity` (default `MIN_ORTHOLOG_IDENTITY`, 0.70) identical to the reference in a global BLOSUM62 alignment (identity over the query) and, when the sequence of the closest paralog is given, it is closer to the reference than to that paralog. The last rule separates the ortholog from the paralog when an entry has no gene name. Eligible rows are ordered reviewed entries first, then by length closest to the reference, then by identity to the reference, then by accession, so the choice is reproducible. Most orthologs are unreviewed genome-annotation entries: treat them as sequences for a screen at the epitope, not as annotated references.

**`panel.json`** holds `target` (the UniProt summary), `orthologs` (accession, entry name, gene, species, common name, organism, taxon id, length, reviewed, role, the reason and the candidate list) and `paralogs` (the UniProt summary with the role and the reason). `refdata.get_panel()` reads it.

## Licences

The cache holds third-party data. The exporter writes a licence list into the manifest of every bundle, and each cached record keeps its source URL in the cache manifest. Check the terms of a provider before you redistribute a cache or a bundle.

| Data | Terms |
|---|---|
| UniProt sequences and annotation | CC BY 4.0 |
| PDB entries used as reference structures | CC0 (wwPDB). A reference structure that is not a PDB entry is listed as supplied with the project: its terms are yours to check before the bundle is shared |
| AlphaFold DB models | published by EMBL-EBI under CC BY 4.0 (stated by the database, not checked by the code) |
| PDBe / SIFTS mappings | provided by EMBL-EBI; see its terms of use (not checked by the code) |
| ConSurf-DB grades | academic, non-commercial use. The overlay is off by default and the manifest records it as `optional-off` or `optional-on` |

## API

`get_uniprot`, `get_uniprot_fasta`, `search_uniprot`, `get_sifts(pdb_id, kind)`, `get_unobserved`, `get_alphafold(acc, with_pae=True)`, `get_consurfdb(pdb_id, chain)`, `get_panel`, `manifest`, `verify_cache`, `fetch_cached(rel, url, allow_missing=False)`, `cache_dir`, `is_offline`; helpers `uniprot_summary`, `fasta_sequence`, `sequence_identity`, `rank_ortholog_candidates`, `parse_consurf_grades`; checks `check_accession`, `check_pdb_id`, `check_chain`, `check_gene`, `check_taxon`. Errors: `RefDataError`, with the subclasses `RefDataOffline` and `RefDataNotFound`.

## See also

[cli.md](cli.md) · [target_frame.md](target_frame.md) · [specificity.md](specificity.md) · [export.md](export.md) · [architecture.md](architecture.md)
