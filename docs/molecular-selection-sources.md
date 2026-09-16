# Exact molecule and protein selection: experimental reference evidence

The human PTGS2 reference registry is [data/molecular_references.json](../data/molecular_references.json). It identifies **six actual small-molecule complexes**, with their deposited CCD identities, target entity and chains, source URLs, resolution and construct qualifications. It is an experimental reference catalog, not evidence that arbitrary generated candidates have been structurally validated.

On the retrieval recorded in the accompanying JSON and local receipts, an accession search for **human PTGS2 / COX-2, UniProt P35354** returned exactly seven experimental entries: 5F19, 5F1A, 5IKQ, 5IKR, 5IKT, 5IKV and 5KIR. The entire result fits in one page. Source metadata, CCD records and request/response SHA-256 manifests are cached in `runtime/molecular-selection-research/`.

| PDB / primary source | Actual ligand / CCD | Polymer construct | Selected-molecule use |
| --- | --- | --- | --- |
| [5IKR](https://www.rcsb.org/structure/5IKR) | Mefenamic acid / [ID8](https://www.rcsb.org/ligand/ID8) | 551 residues; human UniProt 19–569 | Exact neutral ID8 graph |
| [5IKT](https://www.rcsb.org/structure/5IKT) | Tolfenamic acid / [TLF](https://www.rcsb.org/ligand/TLF) | 551 residues; human UniProt 19–569 | Exact neutral TLF graph |
| [5IKQ](https://www.rcsb.org/structure/5IKQ) | Meclofenamic acid / [JMS](https://www.rcsb.org/ligand/JMS) | 551 residues; human UniProt 19–569 | Exact neutral JMS graph |
| [5IKV](https://www.rcsb.org/structure/5IKV) | Flufenamic acid / [FLF](https://www.rcsb.org/ligand/FLF) | 551 residues; human UniProt 19–569 | Exact neutral FLF graph |
| [5F1A](https://www.rcsb.org/structure/5F1A) | Salicylic acid / [SAL](https://www.rcsb.org/ligand/SAL) | 553-residue sample; extra initial K; deposited mutation annotation N594A | SAL only; never intact aspirin |
| [5KIR](https://www.rcsb.org/structure/5KIR) | Rofecoxib / [RCX](https://www.rcsb.org/ligand/RCX) | 551 residues; one annotated sequence conflict | RCX only, with construct qualification |
| [5F19](https://www.rcsb.org/structure/5F19) | Aspirin-acetylated **protein**, including OAS modified serine | 552-residue sample; modified monomer | Excluded from intact aspirin complex matching |

The six CCD records describe neutral molecules without tetrahedral stereocenters. The registry retains the source `SMILES_stereo` and a canonical isomeric RDKit rendering; it does not strip salts, neutralize molecules or collapse tautomers to force a match. These are chemical dictionary identities, not inferred proton locations in an X-ray map.

All six reference polymers map to P35354, Homo sapiens / taxonomy 9606, entity 1, label and author chains A and B. They are **truncated experimental constructs**, not the full 604-residue canonical protein. For the four fenamate complexes, SIFTS maps the complete 551-residue construct to UniProt positions 19–569 with no reported conflict or mutation. In 5KIR, the sample has N instead of Q at entity position 318 / mapped UniProt position 336; RCSB reports one sequence conflict despite a mutation count of zero. For 5F1A the depositor reports N594A while the current mapped construct has zero computed mutation count; preserve both annotations instead of declaring an unmodified full-length sequence. The [RCSB polymer entity API](https://data.rcsb.org/rest/v1/core/polymer_entity/5KIR/1) and [5F1A polymer entity API](https://data.rcsb.org/rest/v1/core/polymer_entity/5F1A/1) provide these distinct fields.

The actual 5IKR mmCIF `_struct_ref` associates `db_name=UNP`, accession `P35354`, and entity `1`. `_struct_ref_seq` maps chains A/B, entity residues 1–551, to UniProt 19–569; author residue numbering is 34–583. Its ID8 instances are label chain E / author A / residue 601 and label chain O / author B / residue 602. A resolver must use these explicit mappings rather than treating all chains or all HETATM residues as the selected target and molecule. [Deposited 5IKR mmCIF](https://files.rcsb.org/download/5IKR.cif)

## Positive selection controls and strict negatives

Two distinct neutral input molecules for actual UI/API checks:

```text
Mefenamic acid: Cc1cccc(Nc2ccccc2C(=O)O)c1C
Expected human reference: 5IKR, component ID8

Tolfenamic acid: Cc1c(Cl)cccc1Nc1ccccc1C(=O)O
Expected human reference: 5IKT, component TLF
```

The actual mmCIF instances in 5IKR (ID8 chains E/O) and 5IKT (TLF chains G/L) each contain all 18 expected CCD heavy atoms, with exact atom-name and element agreement. This coordinate check supports the two positive controls; other registry entries still require the same runtime validation.

Combined human-accession and exact-chemical API queries returned one entry for each control, respectively 5IKR and 5IKT. The existing catalog inputs quercetin, baicalein, luteolin, berberine, aspirin, ibuprofen and celecoxib each returned HTTP 204 (no matching entry) using the **actual stored SMILES**. Local receipts are `exact-query-{catalog_id_or_CCD}.manifest.json`. This is a bounded, dated experimental-database result, not a claim that the molecules cannot bind COX-2. A generated candidate without a matching experimental entry or a separately validated prediction must return an explicit unavailable state.

Known literature structures must retain their actual species. [4PH9](https://www.rcsb.org/structure/4PH9), the ibuprofen complex, and [3LN1](https://www.rcsb.org/structure/3LN1), the celecoxib complex, both map to **mouse Mus musculus / taxonomy 10090 / UniProt Q05769**, not human P35354. Their entity metadata are cached and can be checked through the [4PH9 polymer API](https://data.rcsb.org/rest/v1/core/polymer_entity/4PH9/1) and [3LN1 polymer API](https://data.rcsb.org/rest/v1/core/polymer_entity/3LN1/1). Homolog structures require an explicitly selected mouse target; silently replacing the human target would mislabel the result. The catalog ibuprofen SMILES additionally leaves stereochemistry unspecified and must not silently become a particular stereoisomer.

## Bounded dynamic lookup

The [RCSB chemical search documentation](https://www.rcsb.org/docs/search-and-browse/advanced-search/chemical-similarity-search) and [2026 RCSB chemical search webinar](https://cdn.rcsb.org/pdb101/train/chemical-search/Chemical-Search-webinar-2026.pdf) distinguish exact chemistry from similarity. The correct current match type is **`graph-exact`**. The older `graph-strict` name must not be treated as proof of an identical molecule; chemical search results still require local verification against the returned CCD.

This exact payload was tested against the [RCSB Search API](https://search.rcsb.org/); substitute the selected canonical isomeric SMILES and validated accession. GET with URL-encoded JSON or POST JSON uses `/rcsbsearch/v2/query`.

```json
{
  "query": {
    "type": "group",
    "logical_operator": "and",
    "nodes": [
      {
        "type": "terminal",
        "service": "text",
        "parameters": {
          "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
          "operator": "exact_match",
          "value": "P35354"
        }
      },
      {
        "type": "terminal",
        "service": "chemical",
        "parameters": {
          "type": "descriptor",
          "descriptor_type": "SMILES",
          "value": "Cc1cccc(Nc2ccccc2C(=O)O)c1C",
          "match_type": "graph-exact"
        }
      }
    ]
  },
  "return_type": "entry",
  "request_options": {
    "paginate": {"start": 0, "rows": 25},
    "results_content_type": ["experimental"]
  }
}
```

Bound response bytes, request timeout, returned entries and candidate coordinate downloads; cache positive and negative results with a finite freshness period. An unavailable service is different from a successful no-match result. A truncated page must be reported as bounded search, not a database-wide negative.

Before display, verify the actual downloaded mmCIF entry ID, experimental method, target accession → entity → displayed chains, deposited construct qualification, selected ligand CCD canonical isomeric graph, and complete observed ligand heavy-atom names/elements. Use the CCD bonds for the deposited ligand instance. Do not construct a pose by placing a separate ligand conformer into a reference pocket. Spatial proximity to the target and the source annotation should be retained; merely having both species somewhere in a PDB entry does not prove a particular binding interaction or an affinity value.
