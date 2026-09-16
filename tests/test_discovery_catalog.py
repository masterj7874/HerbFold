import csv
import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from rdkit import Chem

from herbfold.discovery_catalog import DiscoveryCatalog

SOURCE = {"source_id": "test", "source_url": "https://example.org/open-data", "license_label": "CC-BY-4.0"}


def test_identity_preserves_stereoisomers_and_deduplicates_canonical_equivalents(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)
    report = catalog.ingest_records([
        {"id": "ethanol-a", "smiles": "CCO", "name": "Ethanol"},
        {"id": "ethanol-b", "smiles": "OCC", "name": "Alcohol"},
        {"id": "left", "smiles": "N[C@@H](C)C(=O)O"},
        {"id": "right", "smiles": "N[C@H](C)C(=O)O"},
        {"id": "invalid", "smiles": "definitely-not-a-molecule"},
        {"id": "attachment", "smiles": "CC*"},
        None,
    ], **SOURCE, batch_size=2)
    assert report["status"] == "completed"
    assert report["processed_records"] == 7
    assert report["inserted_compounds"] == 3
    assert report["inserted_records"] == 4
    assert report["invalid_records"] == 3
    assert catalog.summary()["compound_count"] == 3
    rows = catalog.list_compounds()["items"]
    assert len({row["inchikey"] for row in rows}) == 3
    assert len(catalog.get_compound(rows[0]["id"])["provenance"]) == 2


def test_provenance_retains_source_license_taxonomy_and_updated_evidence(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)
    original = {"id": "NP1", "smiles": "CCO", "organisms": [{"name": "Plant species"}], "references": ["doi:original"]}
    catalog.ingest_records([original, original], **SOURCE)
    second = {**original, "references": ["doi:updated"]}
    catalog.ingest_records([second], **SOURCE)
    catalog.ingest_records([original], **{**SOURCE, "source_id": "other", "license_label": "CC0"})
    compound = catalog.get_compound(1)
    assert compound["provenance_count"] == 3
    assert {row["license_label"] for row in compound["provenance"]} == {"CC-BY-4.0", "CC0"}
    assert compound["provenance"][0]["raw"] == original
    assert compound["provenance"][0]["source_url"] == SOURCE["source_url"]
    assert compound["provenance"][0]["fetched_at"]
    assert "herbal" not in compound["provenance"][0]["raw"]
    summary = catalog.summary()
    assert summary["provenance_count"] == 3
    assert {row["id"]: row["compound_count"] for row in summary["sources"]} == {"test": 1, "other": 1}


def test_lotus_mapping_keeps_distinct_occurrences_and_searches_taxonomy(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)
    base = {
        "structure_smiles": "CCO", "structure_inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        "structure_nameTraditional": "Alcohol", "structure_molecular_formula": "C2H6O",
        "organism_name": "Panax ginseng", "organism_taxonomy_06family": "Araliaceae",
        "reference_doi": "10.example/first",
    }
    catalog.ingest_records([base, {**base, "reference_doi": "10.example/second"}], **SOURCE)
    for query in ("Panax", "Araliaceae", "Alcohol", "LFQSCWFLJHTTHZ"):
        assert catalog.list_compounds(search=query)["total"] == 1
    row = catalog.get_compound(1)
    assert row["display_name"] == "Alcohol"
    assert row["formula"] == "C2H6O"
    assert row["provenance_count"] == 2


def test_coconut_alias_and_safe_fts_query(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)
    catalog.ingest_records([{"identifier": "CNP0001", "canonical_smiles": "CCO", "name": "Ethanol",
                             "synonyms": "ethyl alcohol", "molecular_formula": "C2H6O"}], **SOURCE)
    assert catalog.list_compounds(search="ethyl")["total"] == 1
    assert catalog.list_compounds(search="CNP0001")["total"] == 1
    assert catalog.list_compounds(search='" OR * --')["total"] == 0
    assert catalog.list_compounds(search="***")["total"] == 0


def test_streaming_commits_batches_and_never_consumes_past_limit(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)
    observed = []

    def records():
        for i in range(10):
            observed.append(i)
            yield {"id": str(i), "smiles": "CCO"}
        raise AssertionError("Importer read past max_records")

    checkpoints = []

    def progress(report):
        with closing(catalog.connect()) as connection:
            committed = connection.execute("SELECT provenance_count FROM discovery_stats").fetchone()[0]
        checkpoints.append((report["processed_records"], committed))
        report["inserted_records"] = -999  # Callback cannot alter import state.

    report = catalog.ingest_records(records(), **SOURCE, max_records=7, batch_size=3, on_progress=progress)
    assert observed == list(range(7))
    assert checkpoints == [(3, 3), (6, 6), (7, 7)]
    assert report["inserted_records"] == 7
    assert report["limit_reached"] is True
    assert catalog.summary()["imports"][0]["status"] == "completed"


def test_pagination_bounds_source_filters_and_durable_reopen(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)
    catalog.ingest_records([{"id": str(i), "smiles": "C" * i} for i in range(1, 5)], **SOURCE)
    catalog.ingest_records([{"smiles": "O"}], **{**SOURCE, "source_id": "water"})
    page = DiscoveryCatalog(tmp_path).list_compounds(source="test", limit=2, offset=2)
    assert page["total"] == 4
    assert [r["canonical_smiles"] for r in page["items"]] == ["CCC", "CCCC"]
    assert catalog.list_compounds(offset=100)["items"] == []
    assert catalog.list_compounds(source="missing")["total"] == 0
    for bad in (0, -1, 501, True, 2.5):
        with pytest.raises(ValueError):
            catalog.list_compounds(limit=bad)
    with pytest.raises(ValueError):
        catalog.list_compounds(offset=-1)
    with pytest.raises(KeyError):
        catalog.get_compound(900)
    with closing(catalog.connect()) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.parametrize("kind", ["csv", "tsv"])
def test_gzip_delimited_import(tmp_path, kind):
    path = tmp_path / f"source.{kind}.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["SMILES", "NAME", "SPECIES"], delimiter="\t" if kind == "tsv" else ",")
        writer.writeheader()
        writer.writerow({"SMILES": "CCO", "NAME": "Test alcohol", "SPECIES": "Panax ginseng"})
        writer.writerow({"SMILES": "bad"})
    catalog = DiscoveryCatalog(tmp_path / "catalog")
    report = catalog.ingest_file(path, **SOURCE)
    assert report["inserted_compounds"] == 1
    assert report["invalid_records"] == 1
    assert catalog.list_compounds(search="ginseng")["total"] == 1


def test_smi_and_forward_sdf_import(tmp_path):
    catalog = DiscoveryCatalog(tmp_path / "catalog")
    smi = tmp_path / "source.smi"
    smi.write_text("# source\nSMILES ID\nCCO ethanol\nO water\n")
    assert catalog.ingest_file(smi, **SOURCE)["inserted_compounds"] == 2
    sdf = tmp_path / "source.sdf"
    with Chem.SDWriter(str(sdf)) as writer:
        mol = Chem.MolFromSmiles("CCO")
        mol.SetProp("_Name", "Ethanol SDF")
        mol.SetProp("organism", "Test species")
        writer.write(mol)
    report = catalog.ingest_file(sdf, **{**SOURCE, "source_id": "sdf"})
    assert report["inserted_compounds"] == 0
    assert report["inserted_records"] == 1
    assert catalog.list_compounds(search="species")["total"] == 1


def test_stream_failure_persists_partial_report_and_successful_rows(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)

    def records():
        yield {"smiles": "CCO"}
        raise OSError("damaged compressed file")

    with pytest.raises(OSError, match="damaged"):
        catalog.ingest_records(records(), **SOURCE, batch_size=1)
    summary = catalog.summary()
    assert summary["compound_count"] == 1
    assert summary["imports"][0]["status"] == "failed"
    assert "damaged" in summary["imports"][0]["error"]


def test_concurrent_same_structure_has_one_identity_and_atomic_counters(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)

    def ingest(i):
        return catalog.ingest_records([{"id": str(i), "smiles": "CCO"}], **SOURCE)

    with ThreadPoolExecutor(max_workers=4) as executor:
        reports = list(executor.map(ingest, range(8)))
    assert sum(r["inserted_compounds"] for r in reports) == 1
    assert catalog.summary()["compound_count"] == 1
    assert catalog.summary()["provenance_count"] == 8


def test_detail_caps_provenance_and_exposes_count(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)
    catalog.ingest_records(({"id": str(i), "smiles": "CCO"} for i in range(1005)), **SOURCE)
    result = catalog.get_compound(1)
    assert result["provenance_count"] == 1005
    assert len(result["provenance"]) == 1000
    assert result["provenance_truncated"] is True
    assert len(catalog.list_compounds()["items"][0]["sources"]) == 10
    json.dumps(result, allow_nan=False)


def test_keyset_iterator_source_resume_and_no_offset_or_total_count(tmp_path, monkeypatch):
    catalog = DiscoveryCatalog(tmp_path)
    catalog.ingest_records([{"id": str(i), "smiles": "C" * i} for i in range(1, 9)], **SOURCE)
    catalog.ingest_records([
        {"id": "sparse-a", "smiles": "CCC", "name": "Matched molecule"},
        {"id": "sparse-a-other-occurrence", "smiles": "CCC", "name": "Matched molecule"},
        {"id": "sparse-b", "smiles": "CCCCCC", "name": "Matched molecule"},
        {"id": "sparse-c", "smiles": "CCCCCCCC", "name": "Matched molecule"},
    ], **{**SOURCE, "source_id": "sparse"})
    statements = []
    original_connect = catalog.connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(catalog, "connect", traced_connect)
    rows = list(catalog.iter_compounds(source="sparse", batch_size=1, after_id=3, through_id=7))
    assert [row["id"] for row in rows] == [6]
    assert rows[0]["sources"]
    assert all("OFFSET" not in sql.upper() and "COUNT(" not in sql.upper() for sql in statements)
    all_rows = list(catalog.iter_compounds(source="sparse", batch_size=1, include_sources=False))
    assert [row["id"] for row in all_rows] == [3, 6, 8]
    assert all("sources" not in row for row in all_rows)
    filtered = list(catalog.iter_compounds(search="Matched", source="sparse", batch_size=2))
    assert [row["id"] for row in filtered] == [3, 6, 8]


def test_keyset_iterator_does_not_extend_when_concurrent_import_appends(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)
    catalog.ingest_records([{"id": str(i), "smiles": "C" * i} for i in range(1, 4)], **SOURCE)
    iterator = catalog.iter_compounds(batch_size=1)
    assert next(iterator)["id"] == 1
    catalog.ingest_records([{"id": "later", "smiles": "CCCC"}], **SOURCE)
    assert [row["id"] for row in iterator] == [2, 3]
    assert [row["id"] for row in catalog.iter_compounds(after_id=3)] == [4]
    assert list(catalog.iter_compounds(source="missing")) == []
    assert list(catalog.iter_compounds(search="***")) == []
    for options in ({"batch_size": 501}, {"after_id": -1}, {"through_id": True}, {"after_id": None}):
        with pytest.raises(ValueError):
            list(catalog.iter_compounds(**options))


def test_search_and_source_require_evidence_from_the_same_provenance_row(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)
    catalog.ingest_records([
        {"id": "a-ethanol", "smiles": "CCO", "organisms": "Scutellaria baicalensis"},
    ], **{**SOURCE, "source_id": "source-a"})
    catalog.ingest_records([
        {"id": "b-ethanol", "smiles": "CCO", "organisms": "Panax ginseng"},
        {"id": "b-water", "smiles": "O", "organisms": "Scutellaria baicalensis"},
    ], **{**SOURCE, "source_id": "source-b"})
    assert catalog.list_compounds(search="Scutellaria baicalensis")["total"] == 2
    page = catalog.list_compounds(search="Scutellaria baicalensis", source="source-b")
    assert page["total"] == 1
    assert [row["canonical_smiles"] for row in page["items"]] == ["O"]
    assert catalog.list_compounds(search="Panax ginseng", source="source-a")["total"] == 0
    assert [row["id"] for row in page["items"]] == [
        row["id"] for row in catalog.iter_compounds(search="Scutellaria baicalensis", source="source-b")
    ]
    positive = catalog.list_compounds(search="Panax ginseng", source="source-b")["items"][0]
    assert {source["source_id"] for source in positive["sources"]} == {"source-a", "source-b"}


def test_kind_pages_deduplicate_occurrences_and_mixed_origins(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)
    catalog.ingest_records([
        {"id": "c-ethanol", "smiles": "CCO"},
        {"id": "c-water", "smiles": "O"},
    ], **{**SOURCE, "source_id": "coconut-2026-09"})
    catalog.ingest_records([
        {"id": "l-water-one", "smiles": "O"},
        {"id": "l-water-two", "smiles": "O"},
        {"id": "l-methane", "smiles": "C"},
    ], **{**SOURCE, "source_id": "lotus-2026-04"})
    catalog.ingest_records([
        {"id": "d-ethanol", "smiles": "CCO"},
        {"id": "d-ethanol-two", "smiles": "CCO"},
        {"id": "d-propane", "smiles": "CCC"},
    ], **{**SOURCE, "source_id": "chembl-approved"})
    catalog.ingest_records([{"id": "unclassified", "smiles": "N"}], **SOURCE)

    natural_pages = [catalog.list_compounds(kind="natural_product", limit=1, offset=i) for i in range(4)]
    assert [page["total"] for page in natural_pages] == [3, 3, 3, 3]
    assert [row["canonical_smiles"] for page in natural_pages for row in page["items"]] == ["CCO", "O", "C"]
    assert natural_pages[-1]["items"] == []
    drugs = catalog.list_compounds(kind="drug")
    assert drugs["total"] == 2
    assert [row["canonical_smiles"] for row in drugs["items"]] == ["CCO", "CCC"]
    assert drugs["items"][0]["source_kinds"] == ["natural_product", "drug"]
    assert drugs["items"][1]["source_kinds"] == ["drug"]
    unfiltered = catalog.list_compounds()
    assert unfiltered["total"] == 5
    assert unfiltered["items"][-1]["source_kinds"] == []
    assert catalog.list_compounds(kind="drug", source="lotus-2026-04")["total"] == 0
    assert catalog.list_compounds(kind="natural_product", source="lotus-2026-04")["total"] == 2
    assert catalog.list_compounds(kind="natural_product", source="unknown")["total"] == 0


def test_kind_search_uses_matching_provenance_and_preview_survives_truncation(tmp_path):
    catalog = DiscoveryCatalog(tmp_path)
    catalog.ingest_records([
        {"id": f"plant-{i}", "smiles": "CCO", "organisms": "Panax ginseng"}
        for i in range(12)
    ], **{**SOURCE, "source_id": "coconut-2026-09"})
    catalog.ingest_records([
        {"id": "lotus-ethanol", "smiles": "CCO", "organisms": "Scutellaria baicalensis"},
    ], **{**SOURCE, "source_id": "lotus-2026-04"})
    catalog.ingest_records([
        {"id": "drug-ethanol", "smiles": "CCO", "name": "Drug-only synonym"},
    ], **{**SOURCE, "source_id": "chembl-approved"})

    first = catalog.list_compounds()["items"][0]
    assert first["source_kinds"] == ["natural_product", "drug"]
    assert {row["source_id"] for row in first["sources"]} == {"coconut-2026-09"}
    for options, matched_source in (
        ({"kind": "drug"}, "chembl-approved"),
        ({"kind": "natural_product", "source": "lotus-2026-04"}, "lotus-2026-04"),
        ({"source": "chembl-approved"}, "chembl-approved"),
    ):
        item = catalog.list_compounds(**options)["items"][0]
        assert len(item["sources"]) == 10
        assert item["sources"][0]["source_id"] == matched_source
    assert catalog.list_compounds(kind="drug", search="Panax")["total"] == 0
    assert catalog.list_compounds(kind="natural_product", search="Drug-only")["total"] == 0
    assert catalog.list_compounds(kind="drug", search="Drug-only")["total"] == 1
    assert catalog.list_compounds(
        kind="natural_product", source="lotus-2026-04", search="Panax",
    )["total"] == 0
    assert catalog.list_compounds(
        kind="natural_product", source="lotus-2026-04", search="Scutellaria",
    )["total"] == 1
    assert catalog.list_compounds(kind="drug", search="***")["total"] == 0


def test_kind_count_cache_tracks_new_provenance_for_existing_identity(tmp_path, monkeypatch):
    catalog = DiscoveryCatalog(tmp_path)
    catalog.ingest_records([{"id": "water", "smiles": "O"}], **{**SOURCE, "source_id": "lotus-2026-04"})
    catalog.ingest_records([{"id": "ethanol", "smiles": "CCO"}], **SOURCE)
    statements = []
    original_connect = catalog.connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(catalog, "connect", traced_connect)
    assert catalog.list_compounds(kind="natural_product")["total"] == 1
    assert any("SELECT COUNT(*)" in sql for sql in statements)
    statements.clear()
    assert catalog.list_compounds(kind="natural_product")["total"] == 1
    assert not any("SELECT COUNT(*)" in sql for sql in statements)
    # A second importer changes only evidence, leaving the identity count fixed.
    other = DiscoveryCatalog(tmp_path)
    other.ingest_records([
        {"id": "natural-ethanol", "smiles": "CCO"},
    ], **{**SOURCE, "source_id": "coconut-2026-09"})
    assert catalog.list_compounds(kind="natural_product")["total"] == 2
    assert catalog.summary()["compound_count"] == 2


@pytest.mark.parametrize("kind", ["herbal", "reference", "", "DRUG", 1, True, []])
def test_kind_rejects_unknown_classification(tmp_path, kind):
    with pytest.raises(ValueError, match="kind"):
        DiscoveryCatalog(tmp_path).list_compounds(kind=kind)
