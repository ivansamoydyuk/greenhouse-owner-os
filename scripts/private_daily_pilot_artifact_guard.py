"""Fail closed before publishing synthetic TEST artifacts.

This is an accidental-disclosure check, not a boundary against malicious PR code.
Daily suites require credential inventory; other suites must opt into --generic.
Known secret bytes and private filenames are checked, including bounded nested
ZIP, tar, gzip, bzip2 and xz payloads. Nothing is extracted to disk. Other known
archive formats, corrupt archives, links and exceeded limits block publication.
Credential/access files and browser session recordings are never public evidence.
Screenshots/PDFs are byte-scanned; this does not recognize secrets rendered into
pixels, arbitrary encodings, or credentials absent from the supplied inventory.
"""
from __future__ import annotations

import argparse
import bz2
from dataclasses import dataclass
import gzip
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
import tarfile
import time
import zipfile
import zlib


class GuardError(Exception):
    """Safe constant diagnostic: never include filenames, contents or secrets."""


@dataclass(frozen=True)
class Limits:
    file_bytes: int = 64 * 1024 * 1024
    total_bytes: int = 512 * 1024 * 1024
    entries: int = 10000
    archive_depth: int = 4
    expansion_ratio: int = 200
    seconds: float = 60
    inventory_bytes: int = 4 * 1024 * 1024
    inventory_files: int = 100
    secret_count: int = 1000
    secret_bytes: int = 1024 * 1024


PRIVATE_NAMES = ('credential', 'storagestate', 'ownerstate', 'privatesecret', 'privateaccess', 'previewaccess')
SECRET_KEYS = frozenset(('password', 'value', 'draft_key', 'token'))
UNSUPPORTED_SUFFIXES = ('.7z', '.rar', '.zst', '.zstd', '.br', '.lz4', '.lzo', '.cab')
ZIP_SUFFIXES = ('.zip', '.jar', '.whl', '.apk', '.docx', '.xlsx', '.pptx')
CHUNK_SIZE = 64 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GuardError(message)


def _invalid_json_constant(_):
    raise GuardError('invalid inventory JSON constant')


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate inventory key')
        result[key] = value
    return result


def _inventory_secrets(obj, *, required: bool) -> set[bytes]:
    require(isinstance(obj, (dict, list)), 'invalid inventory structure')
    values = set()
    pending = [obj]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key == 'value' and not required:
                    # Supplemental Playwright storage contains preference strings
                    # and IndexedDB object-valued records as well as cookie values.
                    if isinstance(value, str) and len(value) >= 12:
                        values.add(value.encode('utf-8'))
                    elif isinstance(value, (dict, list)):
                        pending.append(value)
                elif key in SECRET_KEYS:
                    require(isinstance(value, str) and bool(value), 'invalid inventory secret')
                    values.add(value.encode('utf-8'))
                elif isinstance(value, (dict, list)):
                    pending.append(value)
        elif isinstance(item, list):
            pending.extend(item)
    require(not required or bool(values), 'empty credential inventory')
    return values


def load_secrets(root: Path, inventories: list[Path], environ, limits: Limits) -> tuple[bytes, ...]:
    """Explicit/env inventories are required; found supplemental files must parse."""
    requested = {path.absolute() for path in inventories}
    require(len(requested) <= limits.inventory_files, 'inventory file limit exceeded')
    sources = {path: True for path in requested}
    for path in requested:
        # Retain legacy capture of generated cookies, storage states and fixture
        # secrets kept beside the required main credential JSON outside artifacts.
        for sibling in path.parent.glob('pilot-private*'):
            sources.setdefault(sibling.absolute(), False)
            require(len(sources) <= limits.inventory_files, 'inventory file limit exceeded')
    values = set()
    for path, required in sources.items():
        require(not path.is_symlink(), 'inventory links are forbidden')
        require(path.is_file(), 'required inventory missing or not a file')
        require(not path.resolve().is_relative_to(root), 'inventory inside artifact root')
        require(path.stat().st_size <= limits.inventory_bytes, 'inventory size limit exceeded')
        with path.open('rb') as stream:
            raw = stream.read(limits.inventory_bytes + 1)
        require(len(raw) <= limits.inventory_bytes, 'inventory size limit exceeded')
        try:
            obj = json.loads(raw, object_pairs_hook=_json_object, parse_constant=_invalid_json_constant)
            values.update(_inventory_secrets(obj, required=required))
        except (ValueError, UnicodeError, RecursionError):
            raise GuardError('malformed credential inventory') from None
        require(len(values) <= limits.secret_count, 'inventory secret limit exceeded')
        require(sum(map(len, values)) <= limits.secret_bytes, 'inventory secret byte limit exceeded')
    if environ.get('PREVIEW_SECRET'):
        values.add(environ['PREVIEW_SECRET'].encode('utf-8'))
    return tuple(values)


class ArtifactScanner:
    def __init__(self, secrets: tuple[bytes, ...], limits: Limits):
        self.secrets = secrets
        self.limits = limits
        self.total_bytes = 0
        self.entries = 0
        self.files = 0
        self.started = time.monotonic()

    def check_time(self):
        require(time.monotonic() - self.started <= self.limits.seconds, 'scan time limit exceeded')

    def entry(self):
        self.check_time()
        self.entries += 1
        require(self.entries <= self.limits.entries, 'entry limit exceeded')

    def check_bytes(self, raw: bytes):
        self.check_time()
        for value in self.secrets:
            self.check_time()
            require(value not in raw, 'known secret in artifact')

    def check_name(self, name: str, *, archive: bool = False):
        self.check_bytes(name.encode('utf-8', errors='surrogatepass'))
        normalized = name.lower().replace('-', '').replace('_', '')
        require(not any(word in normalized for word in PRIVATE_NAMES), 'private artifact name')
        require(not name.lower().endswith(('.har', '.trace', 'trace.zip', 'traces.zip')),
                'browser session recording forbidden')
        if archive:
            parts = PurePosixPath(name).parts
            require(bool(name) and not name.startswith(('/', '\\')) and '\\' not in name
                    and ':' not in name and '\x00' not in name and '..' not in parts,
                    'unsafe archive member path')

    def read(self, stream, *, expected: int | None = None, packed: int | None = None) -> bytes:
        if expected is not None:
            require(0 <= expected <= self.limits.file_bytes, 'file size limit exceeded')
            if packed is not None:
                require(expected <= max(packed, 1) * self.limits.expansion_ratio,
                        'archive expansion ratio exceeded')
        raw = bytearray()
        overlap = max((len(secret) for secret in self.secrets), default=1) - 1
        tail = b''
        while True:
            self.check_time()
            block = stream.read(min(CHUNK_SIZE, self.limits.file_bytes - len(raw) + 1))
            if not block:
                break
            self.total_bytes += len(block)
            require(len(raw) + len(block) <= self.limits.file_bytes, 'file size limit exceeded')
            require(self.total_bytes <= self.limits.total_bytes, 'total byte limit exceeded')
            if packed is not None:
                require(len(raw) + len(block) <= max(packed, 1) * self.limits.expansion_ratio,
                        'archive expansion ratio exceeded')
            combined = tail + block
            self.check_bytes(combined)
            tail = combined[-overlap:] if overlap else b''
            raw.extend(block)
        require(expected is None or len(raw) == expected, 'artifact size changed or truncated')
        return bytes(raw)

    def scan_payload(self, raw: bytes, name: str, depth: int = 0):
        self.check_time()
        lower = name.lower()
        if (lower.endswith(UNSUPPORTED_SUFFIXES)
                or raw.startswith((b'7z\xbc\xaf\x27\x1c', b'Rar!', b'\x28\xb5\x2f\xfd', b'\x04\x22\x4d\x18'))):
            raise GuardError('unsupported archive format')
        kind = None
        if lower.endswith(ZIP_SUFFIXES) or raw.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')) or zipfile.is_zipfile(io.BytesIO(raw)):
            kind = 'zip'
        elif lower.endswith(('.gz', '.tgz')) or raw.startswith(b'\x1f\x8b'):
            kind = 'gzip'
        elif lower.endswith(('.bz2', '.tbz', '.tbz2')) or raw.startswith(b'BZh'):
            kind = 'bzip2'
        elif lower.endswith(('.xz', '.txz', '.lzma')) or raw.startswith(b'\xfd7zXZ\x00'):
            kind = 'xz'
        elif lower.endswith('.tar') or raw[257:262] == b'ustar':
            kind = 'tar'
        if kind is None:
            return
        require(depth < self.limits.archive_depth, 'archive depth limit exceeded')
        if kind == 'zip':
            self.scan_zip(raw, depth + 1)
        elif kind == 'tar':
            self.scan_tar(raw, depth + 1)
        else:
            self.entry()
            unpacked_name = name.rsplit('.', 1)[0] if '.' in name else 'expanded'
            if lower.endswith(('.tgz', '.tbz', '.tbz2', '.txz')):
                unpacked_name += '.tar'
            self.check_name(unpacked_name)
            if kind == 'xz':
                expanded = self.read_xz(raw)
            else:
                opener = gzip.GzipFile if kind == 'gzip' else bz2.BZ2File
                kwargs = {'fileobj': io.BytesIO(raw)} if kind == 'gzip' else {'filename': io.BytesIO(raw)}
                with opener(**kwargs) as stream:
                    expanded = self.read(stream, packed=len(raw))
            self.scan_payload(expanded, unpacked_name, depth + 1)

    def scan_zip(self, raw: bytes, depth: int):
        # Bound central-directory object allocation before ZipFile parses it.
        end = raw.rfind(b'PK\x05\x06', max(0, len(raw) - 65557))
        require(end >= 0 and end + 22 <= len(raw), 'invalid ZIP directory')
        _, disk, start_disk, disk_count, count, size, offset, comment = struct.unpack_from('<4s4H2LH', raw, end)
        require(disk == start_disk == 0 and disk_count == count, 'multipart ZIP forbidden')
        require(count != 65535 and size != 0xffffffff and offset != 0xffffffff, 'ZIP64 forbidden')
        require(count <= self.limits.entries - self.entries, 'entry limit exceeded')
        require(end + 22 + comment == len(raw), 'invalid ZIP trailer')
        require(raw[max(0, end - 20):end - 16] != b'PK\x06\x07', 'ZIP64 forbidden')
        # A dishonest EOCD count must not let a huge directory allocate first.
        require(raw.count(b'PK\x01\x02', max(0, end - size), end) <= self.limits.entries - self.entries,
                'entry limit exceeded')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            require(len(archive.infolist()) == count, 'invalid ZIP entry count')
            for member in archive.infolist():
                self.entry()
                self.check_name(member.orig_filename, archive=True)
                require(not member.flag_bits & 1, 'encrypted archive forbidden')
                # ZipFile's LZMA decoder has no memory cap; normal TEST bundles
                # only need these codecs with bounded decoder memory.
                require(member.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2),
                        'unsupported ZIP compression')
                mode = member.external_attr >> 16
                require(stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR), 'archive links or special files forbidden')
                if member.is_dir():
                    continue
                with archive.open(member) as stream:
                    data = self.read(stream, expected=member.file_size, packed=member.compress_size)
                self.scan_payload(data, member.filename, depth)

    def scan_tar(self, raw: bytes, depth: int):
        # Plain tar only; compression was already expanded under byte limits.
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            for member in archive:
                self.entry()
                self.check_name(member.name, archive=True)
                require(member.isdir() or member.isfile(), 'archive links or special files forbidden')
                require(not member.sparse, 'sparse archive forbidden')
                if member.isdir():
                    continue
                with archive.extractfile(member) as stream:
                    data = self.read(stream, expected=member.size)
                self.scan_payload(data, member.name, depth)

    def read_xz(self, raw: bytes) -> bytes:
        decoder = lzma.LZMADecompressor(memlimit=self.limits.file_bytes)
        expanded = bytearray()
        pending = raw
        while not decoder.eof:
            self.check_time()
            block = decoder.decompress(pending, max_length=CHUNK_SIZE)
            pending = b''
            self.total_bytes += len(block)
            expanded.extend(block)
            require(len(expanded) <= self.limits.file_bytes, 'file size limit exceeded')
            require(self.total_bytes <= self.limits.total_bytes, 'total byte limit exceeded')
            require(len(expanded) <= max(len(raw), 1) * self.limits.expansion_ratio,
                    'archive expansion ratio exceeded')
            require(decoder.eof or not decoder.needs_input, 'truncated xz archive')
        require(not decoder.unused_data, 'xz trailing payload forbidden')
        data = bytes(expanded)
        self.check_bytes(data)
        return data

    def scan_directory(self, root: Path):
        require(not root.is_symlink() and root.is_dir(), 'artifact root missing or not a directory')
        self.check_name(root.name)
        pending = [root]
        while pending:
            with os.scandir(pending.pop()) as entries:
                for item in entries:
                    self.entry()
                    self.check_name(item.name)
                    require(not item.is_symlink(), 'artifact links forbidden')
                    if item.is_dir(follow_symlinks=False):
                        pending.append(Path(item.path))
                    else:
                        require(item.is_file(follow_symlinks=False), 'artifact special file forbidden')
                        with open(item.path, 'rb') as stream:
                            data = self.read(stream, expected=os.fstat(stream.fileno()).st_size)
                        self.files += 1
                        self.scan_payload(data, item.name)
        require(self.files > 0, 'artifact root contains no files')


def guard(root: Path, *, inventories: list[Path], generic: bool = False, environ=None, limits: Limits | None = None):
    environ = os.environ if environ is None else environ
    limits = limits or Limits()
    require(not root.is_symlink(), 'artifact links forbidden')
    root = root.resolve()
    inventories = list(inventories)
    if environ.get('PILOT_E2E_CREDENTIALS'):
        inventories.append(Path(environ['PILOT_E2E_CREDENTIALS']))
    require(generic or bool(inventories), 'required credential inventory not configured')
    values = load_secrets(root, inventories, environ, limits)
    scanner = ArtifactScanner(values, limits)
    scanner.scan_directory(root)
    return scanner


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--require-inventory', action='append', type=Path, default=[])
    parser.add_argument('--generic', action='store_true', help='suite does not require credential inventories')
    args = parser.parse_args(argv)
    try:
        guard(args.root, inventories=args.require_inventory, generic=args.generic)
    except GuardError as error:
        print('ARTIFACT_GUARD_FAIL: ' + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, EOFError, RuntimeError, tarfile.TarError, zipfile.BadZipFile, lzma.LZMAError, zlib.error, struct.error):
        # Parser/filesystem diagnostics can contain private names or contents.
        print('ARTIFACT_GUARD_FAIL: unreadable artifact or invalid archive/inventory', file=sys.stderr)
        return 1
    print('PRIVATE_DAILY_ARTIFACT_GUARD_PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
