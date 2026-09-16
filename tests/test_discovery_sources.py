"""Official-source download and extraction integrity without network access."""

import hashlib
import json
import zipfile

import httpx
import pytest

from herbfold import discovery_sources as sources


@pytest.fixture
def release():
    return {
        "id": "reviewed-2026",
        "filename": "release.csv.gz",
        "license": "CC0",
        "download_url": "https://data.example.org/release.csv.gz",
    }


@pytest.fixture
def mock_http(monkeypatch):
    client_class = httpx.Client
    requests = []

    def install(handler):
        def tracked(request):
            requests.append(request)
            return handler(request)

        monkeypatch.setattr(
            sources.httpx,
            "Client",
            lambda **kwargs: client_class(transport=httpx.MockTransport(tracked), **kwargs),
        )
        monkeypatch.setattr(sources.time, "sleep", lambda _: None)
        return requests

    return install


def test_download_uses_exact_bytes_and_complete_receipt_then_verified_cache(tmp_path, release, mock_http):
    data = b"structure,name\nCCO,ethanol\n"
    release["md5"] = hashlib.md5(data).hexdigest()
    calls = mock_http(lambda _: httpx.Response(200, content=data, headers={"content-length": str(len(data))}))
    progress = []
    path, receipt = sources.download_source(release, tmp_path, progress.append)
    assert path.read_bytes() == data
    assert receipt["sha256"] == hashlib.sha256(data).hexdigest()
    assert receipt["source_id"] == release["id"]
    assert calls[0].headers["accept-encoding"] == "identity"
    assert progress[-1]["downloaded_bytes"] == len(data)
    assert sources.download_source(release, tmp_path)[0] == path
    assert len(calls) == 1


def test_publisher_checksum_rejects_wrong_body_before_complete_cache(tmp_path, release, mock_http):
    release["md5"] = hashlib.md5(b"correct release").hexdigest()
    mock_http(lambda _: httpx.Response(200, content=b"incorrect release"))
    with pytest.raises(ValueError, match="publisher checksum"):
        sources.download_source(release, tmp_path)
    assert not (tmp_path / release["filename"]).exists()
    assert not list(tmp_path.glob("*.manifest.json"))


def test_same_size_download_corruption_is_rejected(tmp_path, release, mock_http):
    mock_http(lambda _: httpx.Response(200, content=b"complete source"))
    path, _ = sources.download_source(release, tmp_path)
    path.write_bytes(b"tampered source")
    with pytest.raises(ValueError, match="checksum mismatch"):
        sources.download_source(release, tmp_path)


def test_receipt_identity_and_unverified_cache_are_not_relabelled(tmp_path, release, mock_http):
    mock_http(lambda _: httpx.Response(200, content=b"complete source"))
    path, receipt = sources.download_source(release, tmp_path)
    marker = path.with_suffix(path.suffix + ".manifest.json")
    receipt["source_id"] = "unrelated-release"
    marker.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="pinned release"):
        sources.download_source(release, tmp_path)
    marker.unlink()
    with pytest.raises(ValueError, match="no verified receipt"):
        sources.download_source(release, tmp_path)


def test_interrupted_stream_does_not_become_a_cached_release(tmp_path, release, mock_http):
    mock_http(lambda _: httpx.Response(200, content=b"complete source"))
    with pytest.raises(InterruptedError):
        sources.download_source(release, tmp_path, stopped=lambda: True)
    assert not (tmp_path / release["filename"]).exists()
    assert not list(tmp_path.glob("*.manifest.json"))


def test_retryable_http_failure_retries_then_records_success(tmp_path, release, mock_http):
    failures = 0

    def handler(_):
        nonlocal failures
        failures += 1
        return httpx.Response(503 if failures < 3 else 200, content=b"source")

    calls = mock_http(handler)
    assert sources.download_source(release, tmp_path)[0].read_bytes() == b"source"
    assert len(calls) == 3


def _archive(path, content=b"smiles,name\nCCO,ethanol\n", member="folder/source.csv"):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, content)
    return path


def test_archive_member_paths_do_not_escape_and_corrupt_cache_is_repaired(tmp_path):
    original = b"smiles,name\nCCO,ethanol\n"
    archive = _archive(tmp_path / "source.zip", original, "../../source.csv")
    extracted = sources.extract_csv(archive)
    assert extracted == tmp_path / "source.csv"
    assert extracted.read_bytes() == original
    extracted.write_bytes(original.replace(b"ethanol", b"MUTATED"))
    assert extracted.stat().st_size == len(original)
    assert sources.extract_csv(archive).read_bytes() == original


def test_unmodified_archive_cache_is_verified_without_reextraction(tmp_path, monkeypatch):
    archive = _archive(tmp_path / "source.zip")
    extracted = sources.extract_csv(archive)

    def should_not_extract(*_args, **_kwargs):
        raise AssertionError("A CRC-verified cache should not be re-extracted")

    monkeypatch.setattr(zipfile.ZipFile, "open", should_not_extract)
    assert sources.extract_csv(archive) == extracted


def test_ambiguous_archive_rejected(tmp_path):
    archive = _archive(tmp_path / "source.zip")
    with zipfile.ZipFile(archive, "a") as stream:
        stream.writestr("second.csv", b"CCN,name")
    with pytest.raises(ValueError, match="one CSV"):
        sources.extract_csv(archive)


def test_chembl_paging_preserves_withdrawal_and_limits_records(tmp_path, mock_http):
    rows = [
        {
            "molecule_chembl_id": "CHEMBL1",
            "pref_name": "Example",
            "withdrawn_flag": True,
            "molecule_structures": {"canonical_smiles": "CCO"},
            "molecule_properties": {"full_molformula": "C2H6O"},
        },
        {"molecule_chembl_id": "CHEMBL2", "pref_name": "Other", "molecule_structures": None},
    ]
    calls = mock_http(
        lambda _: httpx.Response(
            200, json={"molecules": rows, "page_meta": {"next": "next-page", "total_count": 20}}
        )
    )
    actual = list(sources.chembl_drug_records(tmp_path, max_records=1))
    assert len(actual) == len(calls) == 1
    assert actual[0]["withdrawn_flag"] is True
    assert actual[0]["source_status"] == "approved_history_not_current_market_status"
    assert actual[0]["category"] == "drug"
    assert json.loads(next(tmp_path.glob("approved-*.json")).read_text())["data"]["molecules"] == rows
