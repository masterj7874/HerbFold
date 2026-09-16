"""Isolated installer checks; all transfers use mock HTTP and temporary paths."""
import base64
import hashlib
import importlib.util
import io
import json
import random
import subprocess
import sys
import tarfile
from concurrent.futures import Future
from pathlib import Path

import httpx
import pytest

google_crc32c = pytest.importorskip('google_crc32c')
zstandard = pytest.importorskip('zstandard')
_SCRIPT = Path(__file__).parents[1] / 'scripts/install_af3_databases.py'
_SPEC = importlib.util.spec_from_file_location('af3_database_installer_under_test', _SCRIPT)
installer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(installer)


def item_for(payload, *, name='fixture.fasta.zst', expanded_size=0):
    return {'object': name, 'url': installer.SOURCE + name, 'generation': 'fixture-generation-123',
            'compressed_bytes': len(payload), 'hashes': {
                'crc32c': base64.b64encode(google_crc32c.Checksum(payload).digest()).decode(),
                'md5': base64.b64encode(hashlib.md5(payload).digest()).decode()},
            'zstd_first_frame': {'frame_content_size_bytes': expanded_size}}


@pytest.fixture

def worker(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, 'CHUNK', 16)
    monkeypatch.setattr(installer, 'RESERVE', 0)
    monkeypatch.setattr(installer, 'MIN_PDB_FILES', 0)
    monkeypatch.setattr(installer.time, 'sleep', lambda _: None)
    return installer.Installer(tmp_path, {'total_compressed_bytes': 0})


def mock_network(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(installer.httpx, 'Client', lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))


def server_for(item, payload, requests):
    def handler(request):
        assert request.url.params['generation'] == item['generation']
        assert request.headers['accept-encoding'] == 'identity'
        requested_range = request.headers.get('range')
        requests.append(requested_range)
        offset = int(requested_range.split('=')[1][:-1]) if requested_range else 0
        headers = {'x-goog-generation': item['generation']}
        if requested_range:
            headers['content-range'] = f'bytes {offset}-{len(payload) - 1}/{len(payload)}'
        return httpx.Response(206 if requested_range else 200, headers=headers,
                              stream=httpx.ByteStream(payload[offset:]))
    return handler


def install_local_archive(worker, raw, *, name='fixture.fasta.zst', compressed=None):
    compressed = compressed or zstandard.ZstdCompressor(write_checksum=True).compress(raw)
    item = item_for(compressed, name=name, expanded_size=len(raw))
    path = worker.download_dir / name
    path.write_bytes(compressed)
    receipt = {'url': item['url'], 'sha256': hashlib.sha256(compressed).hexdigest(),
               'generation': item['generation'], 'size_bytes': len(compressed),
               'stat': installer.stat_record(path), 'crc32c_verified': True, 'md5_verified': True,
               'crc32c': item['hashes']['crc32c'], 'md5': item['hashes']['md5']}
    return item, receipt


def tar_bytes(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for name, kind, body in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = '../../outside.cif'
            if kind == tarfile.REGTYPE:
                info.size = len(body)
            archive.addfile(info, io.BytesIO(body) if kind == tarfile.REGTYPE else None)
    return output.getvalue()


def test_download_resume_from_existing_partial_verifies_whole_file(worker, monkeypatch):
    payload = bytes(range(96))
    item = item_for(payload)
    partial = worker.download_dir / (item['object'] + '.partial')
    partial.write_bytes(payload[:23])
    requests = []
    mock_network(monkeypatch, server_for(item, payload, requests))
    receipt = worker.download(item)
    assert requests == ['bytes=23-']
    assert (worker.download_dir / item['object']).read_bytes() == payload
    assert receipt['sha256'] == hashlib.sha256(payload).hexdigest()
    assert receipt['crc32c_verified'] and receipt['md5_verified']
    assert not partial.exists()


def test_download_recovers_after_http_stream_interruption(worker, monkeypatch):
    payload = bytes(range(128))
    item = item_for(payload)
    requests = []
    normal = server_for(item, payload, requests)

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield payload[:32]
            raise httpx.ReadError('fixture interruption')

    def handler(request):
        if not requests:
            requests.append(None)
            return httpx.Response(200, headers={'x-goog-generation': item['generation']},
                                  stream=BrokenStream())
        return normal(request)

    mock_network(monkeypatch, handler)
    worker.download(item)
    assert requests == [None, 'bytes=32-']
    assert (worker.download_dir / item['object']).read_bytes() == payload


@pytest.mark.parametrize('invalid_hash', ['crc32c', 'md5'])
def test_download_hash_mismatch_never_publishes_archive(worker, monkeypatch, invalid_hash):
    payload = b'fixture bytes with a deliberately mismatched published checksum'
    item = item_for(payload)
    item['hashes'][invalid_hash] = 'incorrect'
    mock_network(monkeypatch, server_for(item, payload, []))
    with pytest.raises((AssertionError, ValueError)):
        worker.download(item)
    assert not (worker.download_dir / item['object']).exists()
    assert not (worker.download_dir / (item['object'] + '.receipt.json')).exists()


def test_download_rejects_generation_change_without_appending(worker, monkeypatch):
    payload = b'a verified generation must remain constant'
    item = item_for(payload)
    partial = worker.download_dir / (item['object'] + '.partial')
    partial.write_bytes(payload[:11])
    mock_network(monkeypatch, lambda request: httpx.Response(
        206, headers={'x-goog-generation': 'changed'}, stream=httpx.ByteStream(payload[11:])))
    with pytest.raises((AssertionError, ValueError)):
        worker.download(item)
    assert partial.read_bytes() == payload[:11]
    assert not (worker.download_dir / item['object']).exists()


def test_download_rejects_ignored_range_without_appending(worker, monkeypatch):
    payload = b'the range response must not duplicate earlier bytes'
    item = item_for(payload)
    partial = worker.download_dir / (item['object'] + '.partial')
    partial.write_bytes(payload[:11])
    mock_network(monkeypatch, lambda request: httpx.Response(
        200, headers={'x-goog-generation': item['generation']}, stream=httpx.ByteStream(payload)))
    with pytest.raises((AssertionError, ValueError)):
        worker.download(item)
    assert partial.read_bytes() == payload[:11]


class WriteProxy:
    def __init__(self, wrapped, behavior):
        self.wrapped, self.behavior = wrapped, behavior

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.wrapped.__exit__(*args)

    def __getattr__(self, key):
        return getattr(self.wrapped, key)

    def write(self, data):
        return self.behavior(self.wrapped, data)


def patch_download_writes(monkeypatch, worker, behavior):
    original = Path.open

    def patched(path, mode='r', *args, **kwargs):
        opened = original(path, mode, *args, **kwargs)
        if path.parent == worker.download_dir and path.name.endswith('.partial') and mode == 'ab':
            return WriteProxy(opened, behavior)
        return opened
    monkeypatch.setattr(Path, 'open', patched)


def test_short_writes_cannot_publish_truncated_archive(worker, monkeypatch):
    payload = bytes(range(96))
    item = item_for(payload)
    mock_network(monkeypatch, server_for(item, payload, []))
    patch_download_writes(monkeypatch, worker, lambda file, data: file.write(data[:3]))
    try:
        receipt = worker.download(item)
    except (AssertionError, ValueError, OSError):
        assert not (worker.download_dir / item['object']).exists()
    else:
        assert receipt['stat']['size'] == len(payload)
        assert (worker.download_dir / item['object']).read_bytes() == payload


def test_partial_write_then_oserror_resumes_from_actual_disk_bytes(worker, monkeypatch):
    payload = bytes(range(96))
    item = item_for(payload)
    requests = []
    mock_network(monkeypatch, server_for(item, payload, requests))
    interrupted = False

    def behavior(file, data):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            file.write(data[:7])
            raise OSError('fixture write interruption after seven persisted bytes')
        return file.write(data)
    patch_download_writes(monkeypatch, worker, behavior)
    try:
        worker.download(item)
    except (AssertionError, ValueError, OSError):
        assert not (worker.download_dir / item['object']).exists()
    else:
        assert (worker.download_dir / item['object']).read_bytes() == payload
        assert requests == [None, 'bytes=7-']


def test_verified_download_receipt_avoids_network(worker, monkeypatch):
    payload = b'one complete verified compressed object'
    item = item_for(payload)
    requests = []
    mock_network(monkeypatch, server_for(item, payload, requests))
    first = worker.download(item)
    second = worker.download(item)
    assert first == second
    assert requests == [None]


def test_fasta_streaming_counts_records_across_chunk_boundary(worker):
    raw = b'>one\nABCDEFGHIJ\n>two\nMNOP\n'
    item, receipt = install_local_archive(worker, raw)
    result = worker.expand(item, receipt)
    assert result['record_count'] == 2
    assert result['sha256'] == hashlib.sha256(raw).hexdigest()
    assert (worker.root / result['relative_path']).read_bytes() == raw


@pytest.mark.parametrize('raw', [b'not-a-fasta\n', b'>one\nAC\x00DE\n', b'>one\n>two\nACDE\n', b'>one\n'])
def test_malformed_or_empty_fasta_is_not_published(worker, raw):
    item, receipt = install_local_archive(worker, raw)
    with pytest.raises((AssertionError, ValueError)):
        worker.expand(item, receipt)
    assert not (worker.root / 'fixture.fasta').exists()


@pytest.mark.parametrize("missing", [1, 2, 3, 4])
def test_truncated_zstd_footer_is_not_marked_decode_verified(worker, missing):
    raw = b'>entry\nACDEFGHIKLMNPQRSTVWY\n'
    compressed = zstandard.ZstdCompressor(write_checksum=True).compress(raw)
    item, receipt = install_local_archive(worker, raw, compressed=compressed[:-missing])
    with pytest.raises((AssertionError, ValueError, zstandard.ZstdError)):
        worker.expand(item, receipt)
    assert not (worker.root / 'fixture.fasta').exists()


def test_normal_pdb_tar_publishes_inventory_and_exact_content(worker):
    body = b'data_1abc\n#\n'
    raw = tar_bytes([('mmcif_files', tarfile.DIRTYPE, b''),
                     ('mmcif_files/1abc.cif', tarfile.REGTYPE, body)])
    item, receipt = install_local_archive(worker, raw, name='fixture.tar.zst')
    result = worker.expand(item, receipt)
    assert result['record_count'] == 1
    assert (worker.root / 'mmcif_files/1abc.cif').read_bytes() == body
    inventory = (worker.root / 'pdb_inventory.jsonl').read_bytes()
    assert hashlib.sha256(inventory).hexdigest() == result['inventory']['sha256']
    assert json.loads(inventory)['sha256'] == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize('name,kind', [
    ('../outside.cif', tarfile.REGTYPE), ('/tmp/outside.cif', tarfile.REGTYPE),
    ('mmcif_files/../../outside.cif', tarfile.REGTYPE),
    ('mmcif_files/link.cif', tarfile.SYMTYPE), ('mmcif_files/link.cif', tarfile.LNKTYPE),
    ('mmcif_files/device.cif', tarfile.CHRTYPE),
])
def test_unsafe_tar_members_never_publish_tree(worker, name, kind):
    raw = tar_bytes([(name, kind, b'data_bad\n')])
    item, receipt = install_local_archive(worker, raw, name='fixture.tar.zst')
    with pytest.raises((AssertionError, ValueError)):
        worker.expand(item, receipt)
    assert not (worker.root / 'mmcif_files').exists()
    assert not (worker.root / 'pdb_inventory.jsonl').exists()


def test_duplicate_tar_member_is_rejected(worker):
    raw = tar_bytes([('mmcif_files/1abc.cif', tarfile.REGTYPE, b'data_one\n'),
                     ('mmcif_files/1abc.cif', tarfile.REGTYPE, b'data_two\n')])
    item, receipt = install_local_archive(worker, raw, name='fixture.tar.zst')
    with pytest.raises(ValueError, match='Duplicate'):
        worker.expand(item, receipt)
    assert not (worker.root / 'mmcif_files').exists()


def test_changed_pdb_inventory_invalidates_expansion_receipt(worker):
    raw = tar_bytes([('mmcif_files/1abc.cif', tarfile.REGTYPE, b'data_one\n')])
    item, receipt = install_local_archive(worker, raw, name='fixture.tar.zst')
    worker.expand(item, receipt)
    inventory = worker.root / 'pdb_inventory.jsonl'
    inventory.write_text(inventory.read_text().replace('1abc', '9xyz'))
    with pytest.raises((AssertionError, ValueError)):
        worker.expand(item, receipt)


def test_changed_pdb_leaf_invalidates_receipt_even_with_unchanged_directory(worker):
    raw = tar_bytes([('mmcif_files/1abc.cif', tarfile.REGTYPE, b'data_one\n')])
    item, receipt = install_local_archive(worker, raw, name='fixture.tar.zst')
    worker.expand(item, receipt)
    directory_before = installer.stat_record(worker.root / 'mmcif_files')
    (worker.root / 'mmcif_files/1abc.cif').write_bytes(b'data_two\n')
    assert installer.stat_record(worker.root / 'mmcif_files') == directory_before
    with pytest.raises(ValueError, match='PDB file changed'):
        worker.expand(item, receipt)


@pytest.mark.parametrize('kind', ['fasta', 'tar'])
def test_legacy_expansion_receipt_is_revalidated_without_rewriting_data(worker, kind):
    body = b'data_one\n' if kind == 'tar' else b'>one\nACDEFG\n'
    raw = tar_bytes([('mmcif_files/1abc.cif', tarfile.REGTYPE, body)]) if kind == 'tar' else body
    name = 'fixture.tar.zst' if kind == 'tar' else 'fixture.fasta.zst'
    item, download = install_local_archive(worker, raw, name=name)
    first = worker.expand(item, download)
    receipt_path = worker.work / (name + '.expanded.json')
    legacy = dict(first)
    legacy.pop('validation_version')
    installer.atomic_json(receipt_path, legacy)
    data_path = worker.root / ('mmcif_files/1abc.cif' if kind == 'tar' else 'fixture.fasta')
    before = installer.stat_record(data_path)
    upgraded = worker.expand(item, download)
    assert upgraded['validation_version'] == installer.VALIDATION_VERSION
    assert upgraded['zstd_frame_eof_verified']
    assert data_path.read_bytes() == body
    assert installer.stat_record(data_path) == before


def test_archive_published_before_receipt_can_be_recovered_without_download(worker, monkeypatch):
    payload = b'complete bytes already atomically published before a process stopped'
    item = item_for(payload)
    path = worker.download_dir / item['object']
    path.write_bytes(payload)
    before = installer.stat_record(path)
    mock_network(monkeypatch, lambda request: pytest.fail('no HTTP should be requested'))
    result = worker.download(item)
    assert result['sha256'] == hashlib.sha256(payload).hexdigest()
    assert installer.stat_record(path) == before


def test_expanded_data_published_before_receipt_is_preserved_and_revalidated(worker):
    raw = b'>one\nACDEFG\n'
    item, receipt = install_local_archive(worker, raw)
    path = worker.root / 'fixture.fasta'
    path.write_bytes(raw)
    before = installer.stat_record(path)
    result = worker.expand(item, receipt)
    assert result['validation_version'] == installer.VALIDATION_VERSION
    assert installer.stat_record(path) == before


def test_source_changed_after_download_receipt_is_rejected(worker):
    item, receipt = install_local_archive(worker, b'>one\nACDEFG\n')
    path = worker.download_dir / item['object']
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match='verified receipt'):
        worker.expand(item, receipt)
    assert not (worker.root / 'fixture.fasta').exists()


@pytest.mark.parametrize('raw', [b'', b'A' * 300000, random.Random(7).randbytes(300000)])
@pytest.mark.parametrize('checksum', [True, False])
def test_frame_validation_handles_raw_compressed_and_repeated_data(worker, raw, checksum):
    compressed = zstandard.ZstdCompressor(write_checksum=checksum).compress(raw)
    item, receipt = install_local_archive(worker, raw, compressed=compressed)
    path = worker.download_dir / item['object']
    output = bytearray()
    with path.open('rb') as source, installer.CheckedZstdReader(source, item, receipt['sha256']) as reader:
        while chunk := reader.read(65536):
            output.extend(chunk)
    assert bytes(output) == raw


def test_unexpected_concatenated_zstd_frame_is_rejected(worker):
    raw = b'>one\nACDEFG\n'
    compressor = zstandard.ZstdCompressor(write_checksum=True)
    compressed = compressor.compress(raw) + compressor.compress(b'>extra\nACDE\n')
    item, receipt = install_local_archive(worker, raw, compressed=compressed)
    with pytest.raises(ValueError, match='trailing|concatenated'):
        worker.expand(item, receipt)
    assert not (worker.root / 'fixture.fasta').exists()


def test_integrity_guards_remain_enabled_under_python_optimization():
    code = (
        'import importlib.util;'
        f's=importlib.util.spec_from_file_location("installer",{str(_SCRIPT)!r});'
        'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
        'm.require(False,"integrity check is active")'
    )
    result = subprocess.run([sys.executable, '-O', '-c', code], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'ValueError: integrity check is active' in result.stderr


@pytest.mark.parametrize('raw,records,symbols', [
    (b'>one\nACD\n>two description\nE.F-*\n', 2, 8),
    (b'>one\r\n A C D\t\r\n>two\r\nMN\r\n', 2, 5),
    (b'>one\n\nACDE\n\n>two\n\nFG\n', 2, 6),
    (b'>description >contains >symbols\nACD', 1, 3),
    (b'>one\nacgturykmswbdhvn\n', 1, 16),
    (b'>' + b'long description ' * 20 + b'\nAA\n', 1, 2),
    ('>한약 학명 설명\nACD\n'.encode(), 1, 3),
    (b'>one\nACD', 1, 3),
    (b'>one\n*.-\n', 1, 3),
    (b'> \tidentifier \t\nAC\n', 1, 2),
])
def test_fasta_validation_matches_all_single_splits_and_small_chunk_boundaries(raw, records, symbols):
    partitions = [[raw[:index], raw[index:]] for index in range(len(raw) + 1)]
    partitions += [[raw[index:index + step] for index in range(0, len(raw), step)]
                   for step in range(1, 18)]
    for chunks in partitions:
        validator = installer.FastaValidator()
        for chunk in chunks:
            validator.feed(chunk)
        validator.finish()
        assert (validator.records, validator.symbols) == (records, symbols)


@pytest.mark.parametrize('raw', [
    b'', b'\n>one\nAC\n', b' >one\nAC\n', b'AC\n>one\nAA\n',
    b'>\nAA\n', b'> \t\r\nAA\n', b'> \v\f\nAA\n', b'>header', b'>header\n\n',
    b'>a\n>b\nAA\n', b'>a\nAA\n>b\n', b'>a\nAA>BB\n', b'>a\nAA\n> \nAA\n',
    b'>a\nAA\x00\n', b'>a\x00\nAA\n', b'>a\nA1\n', b'>a\nA_\n',
    b'>a\nAC\vDE\n', b'>a\nAC\xffDE\n',
])
def test_invalid_fasta_remains_invalid_at_every_byte_boundary(raw):
    partitions = [[raw[:index], raw[index:]] for index in range(len(raw) + 1)]
    partitions += [[raw[index:index + step] for index in range(0, len(raw), step)]
                   for step in range(1, 8)]
    for chunks in partitions:
        validator = installer.FastaValidator()
        with pytest.raises(ValueError):
            for chunk in chunks:
                validator.feed(chunk)
            validator.finish()


def test_fasta_many_records_random_partitions_preserve_exact_counts():
    rng = random.Random(23)
    records = []
    symbols = 0
    for index in range(1000):
        sequence = bytes(rng.choice(b'ACDEFGHIKLMNPQRSTVWY') for _ in range(rng.randrange(1, 151)))
        records.append(b'>' + str(index).encode() + b' description >text\n' + sequence + b'\n')
        symbols += len(sequence)
    raw = b''.join(records)
    validator = installer.FastaValidator()
    cursor = 0
    while cursor < len(raw):
        size = rng.randrange(1, 500)
        validator.feed(raw[cursor:cursor + size])
        cursor += size
    validator.finish()
    assert validator.records == 1000
    assert validator.symbols == symbols


def test_expansion_failure_is_published_before_download_collection_finishes(worker):
    pending = Future()

    class ControlledExecutor:
        def submit(self, function, item, download):
            assert function == worker.expand
            return pending

    item = item_for(b'fixture compressed source')
    future = worker.submit_expansion(ControlledExecutor(), item, {'sha256': 'fixture'})
    assert future is pending
    assert not (worker.work / 'status.json').exists()
    pending.set_exception(ValueError('fixture archive member type'))
    status = json.loads((worker.work / 'status.json').read_text())
    assert status['status'] == 'running'
    assert status['components'][item['object']]['stage'] == 'failed'
    assert status['components'][item['object']]['error'] == 'ValueError: fixture archive member type'
    with pytest.raises(ValueError, match='fixture archive member type'):
        pending.result()


# Exact 45 bytes observed in the pinned official source, kept opaque here.
_ANCILLARY_BODY = bytes.fromhex(
    '1f8b0800000000000003edc1010d000000c2a0f74f6d0e37a00000000000000000008037039ade1d2700280000')


def official_pdb_fixture(worker, entries):
    name = 'pdb_2022_09_28_mmcif_files.tar.zst'
    item, receipt = install_local_archive(worker, tar_bytes(entries), name=name)
    item['generation'] = receipt['generation'] = installer.PDB_ANCILLARY['source_generation']
    return item, receipt


def test_official_ancillary_is_preserved_opaque_outside_template_tree_and_reused(worker, monkeypatch):
    body = b'data_1abc\n#\n'
    item, receipt = official_pdb_fixture(worker, [
        ('mmcif_files/1abc.cif', tarfile.REGTYPE, body),
        (installer.PDB_ANCILLARY['archive_path'], tarfile.REGTYPE, _ANCILLARY_BODY)])
    result = worker.expand(item, receipt)
    excluded, = result['excluded_ancillary_members']
    assert result['record_count'] == 1 and result['size_bytes'] == len(body)
    assert excluded['archive_path'] == installer.PDB_ANCILLARY['archive_path']
    assert excluded['sha256'] == hashlib.sha256(_ANCILLARY_BODY).hexdigest()
    ancillary = worker.root / excluded['preserved_relative_path']
    assert ancillary.read_bytes() == _ANCILLARY_BODY
    assert list((worker.root / 'mmcif_files').iterdir()) == [worker.root / 'mmcif_files/1abc.cif']
    before = installer.stat_record(ancillary)
    monkeypatch.setattr(installer, 'CheckedZstdReader', lambda *args: pytest.fail('No repeat decode needed'))
    assert worker.expand(item, receipt) == result
    assert installer.stat_record(ancillary) == before


@pytest.mark.parametrize('change', ['wrong_generation', 'wrong_url', 'wrong_path', 'wrong_size',
                                  'wrong_content', 'symlink', 'hardlink', 'duplicate', 'missing'])
def test_ancillary_exception_requires_every_verified_condition(worker, change):
    entry = (installer.PDB_ANCILLARY['archive_path'], tarfile.REGTYPE, _ANCILLARY_BODY)
    if change == 'wrong_path':
        entry = ('mmcif_files/other.tar.gz', entry[1], entry[2])
    elif change == 'wrong_size':
        entry = (entry[0], entry[1], entry[2] + b'x')
    elif change == 'wrong_content':
        entry = (entry[0], entry[1], b'x' + entry[2][1:])
    elif change in ('symlink', 'hardlink'):
        entry = (entry[0], tarfile.SYMTYPE if change == 'symlink' else tarfile.LNKTYPE, b'')
    entries = [('mmcif_files/1abc.cif', tarfile.REGTYPE, b'data_one\n')]
    if change != 'missing':
        entries.append(entry)
    if change == 'duplicate':
        entries.append(entry)
    item, receipt = official_pdb_fixture(worker, entries)
    if change == 'wrong_generation':
        item['generation'] = receipt['generation'] = 'other-generation'
    if change == 'wrong_url':
        item['url'] = receipt['url'] = 'https://example.org/another-source'
    with pytest.raises(ValueError):
        worker.expand(item, receipt)
    assert not (worker.root / 'mmcif_files').exists()
    assert not (worker.root / 'pdb_inventory.jsonl').exists()


@pytest.mark.parametrize('change', ['body', 'missing', 'receipt'])
def test_ancillary_evidence_tamper_invalidates_installed_receipt(worker, change):
    item, receipt = official_pdb_fixture(worker, [
        ('mmcif_files/1abc.cif', tarfile.REGTYPE, b'data_one\n'),
        (installer.PDB_ANCILLARY['archive_path'], tarfile.REGTYPE, _ANCILLARY_BODY)])
    result = worker.expand(item, receipt)
    path = worker.root / result['excluded_ancillary_members'][0]['preserved_relative_path']
    if change == 'body':
        path.write_bytes(b'x' * len(_ANCILLARY_BODY))
    elif change == 'missing':
        path.unlink()
    else:
        result['excluded_ancillary_members'] = []
        installer.atomic_json(worker.work / (item['object'] + '.expanded.json'), result)
    with pytest.raises(ValueError, match='ancillary'):
        worker.expand(item, receipt)


def test_retry_after_later_cif_write_failure_preserves_ancillary_evidence(worker, monkeypatch):
    item, receipt = official_pdb_fixture(worker, [
        (installer.PDB_ANCILLARY['archive_path'], tarfile.REGTYPE, _ANCILLARY_BODY),
        ('mmcif_files/1abc.cif', tarfile.REGTYPE, b'data_one\n')])
    original = installer.write_all

    def failing(output, body):
        if bytes(body).startswith(b'data_'):
            raise OSError('Simulated interrupted CIF write')
        return original(output, body)

    monkeypatch.setattr(installer, 'write_all', failing)
    with pytest.raises(OSError, match='interrupted'):
        worker.expand(item, receipt)
    ancillary = worker.root / installer.PDB_ANCILLARY['preserved_relative_path']
    before = installer.stat_record(ancillary)
    assert ancillary.read_bytes() == _ANCILLARY_BODY
    assert not (worker.root / 'mmcif_files').exists()
    monkeypatch.setattr(installer, 'write_all', original)
    result = worker.expand(item, receipt)
    assert result['record_count'] == 1
    assert installer.stat_record(ancillary) == before
