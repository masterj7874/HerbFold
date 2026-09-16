"""Pinned UniProt registrations use mocked HTTP; tests never submit AF3 work."""

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from herbfold import protein_targets
from herbfold.protein_targets import TargetRegistry, TargetRegistryError, normalize_accession
from herbfold.protein_targets_api import make_router
from herbfold.storage import Store


def entry(accession="P23219", sequence="ACDEX"):
    return {
        "primaryAccession": accession,
        "secondaryAccessions": ["A8K1V7"],
        "entryType": "UniProtKB reviewed (Swiss-Prot)",
        "proteinDescription": {"recommendedName": {"fullName": {"value": "Fixture protein"}}},
        "organism": {"scientificName": "Fixture organism", "taxonId": 9606},
        "genes": [{"geneName": {"value": "FIXTURE"}}],
        "sequence": {"value": sequence, "length": len(sequence)},
        "entryAudit": {"sequenceVersion": 2, "entryVersion": 240},
        "comments": [
            {
                "commentType": "ALTERNATIVE PRODUCTS",
                "isoforms": [
                    {
                        "isoformIds": ["P23219-1"],
                        "name": {"value": "1"},
                        "isoformSequenceStatus": "Displayed",
                    },
                    {
                        "isoformIds": ["P23219-2"],
                        "name": {"value": "Short"},
                        "isoformSequenceStatus": "Described",
                    },
                ],
            }
        ],
    }


@pytest.fixture
def remote(monkeypatch):
    original_client = httpx.Client
    requests = []
    routes = {}

    def handler(request):
        requests.append(str(request.url))
        assert request.url.scheme == "https"
        assert request.url.host == "rest.uniprot.org"
        status, body = routes[request.url.path]
        if isinstance(body, Exception):
            raise body
        if isinstance(body, dict):
            return httpx.Response(
                status, json=body, headers={"Location": "https://untrusted.invalid/redirect"}
            )
        return httpx.Response(status, content=body)

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(protein_targets.httpx, "Client", client)
    return routes, requests


@pytest.fixture
def registry(tmp_path):
    return TargetRegistry(Store(tmp_path / "workspace"))


def test_seed_preserves_original_sequence_without_network(registry, remote):
    routes, requests = remote
    original = json.loads((Path(__file__).resolve().parents[1] / "data/ptgs2.json").read_text())
    result = registry.get("p35354")
    assert result["sequence"] == original["sequence"]
    assert result["retrieved_at"] == original["retrieved_at"]
    assert result["source"] == original["source"]
    assert result["sequence_sha256"] == hashlib.sha256(original["sequence"].encode()).hexdigest()
    assert result["length"] == len(original["sequence"])
    assert result["reviewed"] is None
    assert result["provenance"]["kind"] == "bundled_uniprot_snapshot"
    assert registry.register("P35354") == result
    assert requests == []


@pytest.mark.parametrize(
    "accession",
    [
        "",
        "../P35354",
        "https://rest.uniprot.org/P35354",
        "P35354?x=1",
        "AAAAAA",
        "123456",
        "P35354-0",
        "P35354-01",
        "P35354-2/anything",
        None,
        123,
    ],
)
def test_invalid_accessions_never_reach_network(registry, remote, accession):
    with pytest.raises(TargetRegistryError) as error:
        registry.register(accession)
    assert error.value.status_code == 422
    assert not remote[1]


@pytest.mark.parametrize("accession", ["P35354", "Q9Y261", "A8K1V7", "A0A023GPI8", "P23219-2"])
def test_documented_accession_forms(accession):
    assert normalize_accession(" " + accession.lower() + " ") == accession


def test_nondefault_registration_is_persistent_and_never_overwrites(registry, remote):
    routes, requests = remote
    routes["/uniprotkb/P23219.json"] = (200, entry())
    record = registry.register("p23219")
    assert record["accession"] == "P23219"
    assert record["length"] == 5
    assert record["gene"] == "FIXTURE"
    assert record["organism"] == "Fixture organism"
    assert record["taxon_id"] == 9606
    assert record["reviewed"] is True
    assert record["sequence_version"] == 2
    assert record["entry_version"] == 240
    assert record["registered_at"] >= record["retrieved_at"]
    assert len(record["provenance"]["metadata_response_sha256"]) == 64
    assert record["sequence_sha256"] == hashlib.sha256(b"ACDEX").hexdigest()
    routes["/uniprotkb/P23219.json"] = (200, entry(sequence="RRRR"))
    restarted = TargetRegistry(Store(registry.store.root))
    assert restarted.get("P23219") == record
    assert restarted.register("P23219") == record
    assert len(requests) == 1
    assert [item["accession"] for item in restarted.list()] == ["P23219", "P35354"]


def test_get_never_fetches_unregistered_target_and_workspaces_are_isolated(registry, remote, tmp_path):
    routes, requests = remote
    with pytest.raises(KeyError):
        registry.get("P23219")
    assert not requests
    routes["/uniprotkb/P23219.json"] = (200, entry())
    registry.register("P23219")
    other = TargetRegistry(Store(tmp_path / "other"))
    with pytest.raises(KeyError):
        other.get("P23219")
    assert len(other.list()) == 1


def test_simultaneous_registration_returns_one_pinned_record(registry, remote):
    remote[0]["/uniprotkb/P23219.json"] = (200, entry())
    with ThreadPoolExecutor(max_workers=5) as executor:
        records = list(executor.map(lambda _: registry.register("P23219"), range(5)))
    assert all(record == records[0] for record in records)
    assert sum(record["accession"] == "P23219" for record in registry.list()) == 1


def test_explicit_merged_alias_is_verified_and_persisted(registry, remote):
    routes, requests = remote
    routes["/uniprotkb/A8K1V7.json"] = (
        303,
        {
            "entryType": "Inactive",
            "primaryAccession": "A8K1V7",
            "inactiveReason": {"inactiveReasonType": "MERGED", "mergeDemergeTo": ["P23219"]},
        },
    )
    routes["/uniprotkb/P23219.json"] = (200, entry())
    record = registry.register("A8K1V7")
    assert record["accession"] == "P23219"
    assert record["provenance"]["alias_mappings"][0]["from"] == "A8K1V7"
    assert registry.get("A8K1V7") == registry.get("P23219") == record
    restarted = TargetRegistry(Store(registry.store.root))
    assert restarted.register("A8K1V7") == record
    assert len(requests) == 2
    assert all(url.startswith(protein_targets.REST_BASE) for url in requests)


def test_secondary_alias_requires_explicit_response_mapping(registry, remote):
    routes, requests = remote
    routes["/uniprotkb/A8K1V7.json"] = (200, entry())
    assert registry.register("A8K1V7")["accession"] == "P23219"
    wrong = entry("Q9Y261")
    routes["/uniprotkb/Q9Y261.json"] = (
        200,
        {**wrong, "primaryAccession": "P12345", "secondaryAccessions": []},
    )
    with pytest.raises(TargetRegistryError, match="매핑"):
        registry.register("Q9Y261")
    with pytest.raises(KeyError):
        registry.get("Q9Y261")


def test_registering_alias_does_not_replace_an_existing_primary_sequence(registry, remote):
    routes, requests = remote
    routes["/uniprotkb/P23219.json"] = (200, entry())
    original = registry.register("P23219")
    routes["/uniprotkb/A8K1V7.json"] = (200, entry(sequence="RRRR"))
    assert registry.register("A8K1V7") == original
    assert registry.get("P23219")["sequence"] == "ACDEX"


@pytest.mark.parametrize("isoform", ["P23219-1", "P23219-2"])
def test_exact_isoform_fasta_is_kept_separate_from_canonical(registry, remote, isoform):
    routes, requests = remote
    routes["/uniprotkb/P23219.json"] = (200, entry())
    routes[f"/uniprotkb/{isoform}.fasta"] = (
        200,
        f">sp|{isoform}|PGH1_HUMAN Isoform Fixture OS=Fixture\nACD\n",
    )
    record = registry.register(isoform)
    assert record["accession"] == isoform
    assert record["sequence"] == "ACD"
    assert record["length"] == 3
    assert record["sequence_version"] is None
    assert record["provenance"]["parent_sequence_version"] == 2
    assert record["provenance"]["sequence_url"].endswith(isoform + ".fasta")
    assert record["provenance"]["parent_accession"] == "P23219"
    with pytest.raises(KeyError):
        registry.get("P23219")


@pytest.mark.parametrize(
    "fasta",
    [
        ">sp|P23219|PGH1_HUMAN canonical\nACDEX\n",
        ">sp|P23219-3|PGH1_HUMAN wrong\nACD\n",
        ">sp|P23219-2|PGH1_HUMAN\nACD\n>sp|P23219|PGH1_HUMAN\nACDEX\n",
        "not fasta\nACD",
    ],
)
def test_isoform_never_silently_uses_canonical_or_wrong_header(registry, remote, fasta):
    remote[0]["/uniprotkb/P23219.json"] = (200, entry())
    remote[0]["/uniprotkb/P23219-2.fasta"] = (200, fasta)
    with pytest.raises(TargetRegistryError, match="헤더"):
        registry.register("P23219-2")
    assert len(registry.list()) == 1


def test_unknown_isoform_or_ambiguous_alias_does_not_download_a_substitute(registry, remote):
    routes, requests = remote
    routes["/uniprotkb/P23219.json"] = (200, entry())
    with pytest.raises(TargetRegistryError) as error:
        registry.register("P23219-99")
    assert error.value.status_code == 404
    assert requests == [protein_targets.REST_BASE + "P23219.json"]
    routes["/uniprotkb/A8K1V7.json"] = (200, entry())
    with pytest.raises(TargetRegistryError) as error:
        registry.register("A8K1V7-2")
    assert error.value.code == "ambiguous_isoform_alias"


@pytest.mark.parametrize("sequence", ["ACDU", "ACDO", "ACDB", "ACD*", "ACD-", "", "A" * 10001])
def test_af3_unsupported_sequences_are_not_registered(registry, remote, sequence):
    remote[0]["/uniprotkb/P23219.json"] = (200, entry(sequence=sequence))
    with pytest.raises(TargetRegistryError) as error:
        registry.register("P23219")
    assert error.value.status_code == 422
    assert len(registry.list()) == 1


@pytest.mark.parametrize(
    "broken", ["length", "organism", "genes", "proteinDescription", "nonobject", "bad_json"]
)
def test_malformed_upstream_metadata_is_a_clear_error(registry, remote, broken):
    data = copy.deepcopy(entry())
    if broken == "length":
        data["sequence"]["length"] = 99
    elif broken == "nonobject":
        data = b"[]"
    elif broken == "bad_json":
        data = b"not JSON"
    else:
        data[broken] = None
    remote[0]["/uniprotkb/P23219.json"] = (200, data)
    with pytest.raises(TargetRegistryError) as error:
        registry.register("P23219")
    assert error.value.status_code == 502


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (404, b"missing", 404),
        (503, b"busy", 502),
        (302, b"redirect", 502),
        (200, httpx.ReadTimeout("read timed out"), 504),
        (200, httpx.ConnectError("offline"), 502),
    ],
)
def test_unavailable_or_untrusted_redirect_preserves_registry(registry, remote, status, body, expected):
    remote[0]["/uniprotkb/P23219.json"] = (status, body)
    with pytest.raises(TargetRegistryError) as error:
        registry.register("P23219")
    assert error.value.status_code == expected
    assert len(registry.list()) == 1
    assert len(remote[1]) == 1


def test_response_download_is_bounded(registry, remote, monkeypatch):
    monkeypatch.setattr(protein_targets, "MAX_RESPONSE_BYTES", 100)
    remote[0]["/uniprotkb/P23219.json"] = (200, b"x" * 101)
    with pytest.raises(TargetRegistryError, match="크기"):
        registry.register("P23219")


def test_registration_api_lists_reads_and_registers_without_running_jobs(tmp_path, remote):
    store = Store(tmp_path / "api")
    app = FastAPI()
    app.include_router(make_router(store))
    remote[0]["/uniprotkb/P23219.json"] = (200, entry())
    with TestClient(app) as client:
        assert [item["accession"] for item in client.get("/api/molecular/targets").json()["items"]] == [
            "P35354"
        ]
        assert client.get("/api/molecular/targets/P35354").status_code == 200
        assert client.get("/api/molecular/targets/P23219").status_code == 404
        assert not remote[1]
        response = client.post("/api/molecular/targets/register", json={"accession": "p23219"})
        assert response.status_code == 200
        assert response.json()["accession"] == "P23219"
        assert client.get("/api/molecular/targets/P23219").json() == response.json()
        assert (
            client.post("/api/molecular/targets/register", json={"accession": "invalid"}).status_code == 422
        )
        assert (
            client.post(
                "/api/molecular/targets/register", json={"accession": "P23219", "sequence": "fake"}
            ).status_code
            == 422
        )
        remote[0]["/uniprotkb/Q9Y261.json"] = (200, httpx.ReadTimeout("slow"))
        timeout = client.post("/api/molecular/targets/register", json={"accession": "Q9Y261"})
        assert timeout.status_code == 504
        assert "초과" in timeout.json()["detail"]
        assert client.get("/api/molecular/targets/P23219").json() == response.json()
    assert store.list() == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("accession", "P23219"),
        ("sequence", "BAD"),
        ("sequence", None),
        ("sequence_sha256", "0" * 64),
        ("length", 999),
        ("name", None),
        ("organism", []),
        ("source", "https://untrusted.invalid/entry"),
        ("registered_at", "not-a-date"),
        ("reviewed", "yes"),
        ("taxon_id", True),
        ("provenance", {}),
    ],
)
def test_corrupt_pinned_fields_are_rejected_and_never_overwritten(registry, remote, field, value):
    corrupt = registry.get("P35354")
    corrupt[field] = value
    raw = json.dumps(corrupt)
    with registry.store.connect() as con:
        con.execute("UPDATE protein_targets SET record_json=? WHERE accession='P35354'", (raw,))
    for operation in (lambda: registry.get("P35354"), lambda: registry.register("P35354"), registry.list):
        with pytest.raises(TargetRegistryError) as error:
            operation()
        assert error.value.code == "registered_target_corrupt"
        assert error.value.status_code == 409
    assert not remote[1]
    with registry.store.connect() as con:
        assert (
            con.execute("SELECT record_json FROM protein_targets WHERE accession='P35354'").fetchone()[0]
            == raw
        )


@pytest.mark.parametrize("raw", ["broken JSON", "null", "[]", '{"accession":"P35354"}'])
def test_corrupt_seed_keeps_application_available_with_clear_api_errors(registry, remote, raw):
    with registry.store.connect() as con:
        con.execute("UPDATE protein_targets SET record_json=? WHERE accession='P35354'", (raw,))
    restarted = Store(registry.store.root)
    app = FastAPI()
    app.include_router(make_router(restarted))
    with TestClient(app) as client:
        for response in (
            client.get("/api/molecular/targets"),
            client.get("/api/molecular/targets/P35354"),
            client.post("/api/molecular/targets/register", json={"accession": "P35354"}),
        ):
            assert response.status_code == 409
            assert "저장" in response.json()["detail"]
        assert client.get("/api/molecular/targets/P23219").status_code == 404
    assert not remote[1]


def test_existing_corrupt_primary_is_not_repaired_through_alias_registration(registry, remote):
    remote[0]["/uniprotkb/P23219.json"] = (200, entry())
    registry.register("P23219")
    with registry.store.connect() as con:
        con.execute("UPDATE protein_targets SET record_json='{}' WHERE accession='P23219'")
    remote[0]["/uniprotkb/A8K1V7.json"] = (200, entry())
    with pytest.raises(TargetRegistryError) as error:
        registry.register("A8K1V7")
    assert error.value.code == "registered_target_corrupt"
    with pytest.raises(KeyError):
        registry.get("A8K1V7")


def test_trickle_download_checks_deadline_before_buffer_is_full(monkeypatch):
    clock = [0]

    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(60):
                clock[0] += 1
                yield b"a"

    monkeypatch.setattr(protein_targets.time, "monotonic", lambda: clock[0])
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Trickle()))
    ) as client:
        with pytest.raises(TargetRegistryError) as error:
            protein_targets._download(client, "P23219", "json", 30)
    assert error.value.code == "uniprot_timeout"
    assert clock[0] == 30
