"""Synthetic-only regression probes for fail-closed artifact publication checks."""
from __future__ import annotations

import bz2
from dataclasses import replace
import gzip
import io
import json
import lzma
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

from scripts.private_daily_pilot_artifact_guard import GuardError, Limits, guard


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/private_daily_pilot_artifact_guard.py'
SYNTHETIC = 'SYNTHETIC_TEST_ONLY_credential_bytes_0123456789'


def zip_bytes(content=b'harmless evidence', *, name='report.txt', compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as archive:
        archive.writestr(name, content)
    return output.getvalue()


def tar_bytes(content=b'harmless evidence', *, name='report.txt', kind=None, linkname=''):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        info = tarfile.TarInfo(name)
        if kind is not None:
            info.type = kind
            info.linkname = linkname
            archive.addfile(info)
        else:
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


class ArtifactGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'artifacts'
        self.root.mkdir()
        (self.root / 'report.json').write_text('{"status": "PASS"}')
        self.inventory = self.base / 'pilot-private-credentials.json'
        self.inventory.write_text(json.dumps({'owner': {'password': SYNTHETIC}}))

    def check(self, **kwargs):
        kwargs.setdefault('inventories', [self.inventory])
        kwargs.setdefault('environ', {})
        return guard(self.root, **kwargs)

    def cli(self, *args, env=None, optimized=False):
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('PILOT_E2E_CREDENTIALS', 'PREVIEW_SECRET')}
        environment.update(env or {})
        command = [sys.executable] + (['-O'] if optimized else []) + [str(SCRIPT), str(self.root), *map(str, args)]
        return subprocess.run(command, capture_output=True, text=True, env=environment, timeout=10)

    def test_valid_main_and_supplemental_inventory_pass(self):
        (self.base / 'pilot-private-credentials.json.journey-state.json').write_text('[{"engine":"chromium"}]')
        (self.base / 'pilot-private-credentials.json.owner-state.json').write_text('{"cookies":[{"value":"en"}],"indexedDB":[{"value":{"payload":1}}]}')
        self.assertEqual(self.check().files, 1)
        result = self.cli('--require-inventory', self.inventory)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PRIVATE_DAILY_ARTIFACT_GUARD_PASS', result.stdout)

    def test_generic_does_not_claim_inventory_exists(self):
        self.inventory.unlink()
        self.check(inventories=[], generic=True)
        self.assertEqual(self.cli('--generic').returncode, 0)
        with self.assertRaisesRegex(GuardError, 'not configured'):
            self.check(inventories=[])
        self.assertNotEqual(self.cli().returncode, 0)

    def test_missing_explicit_and_environment_inventory_fail(self):
        self.inventory.unlink()
        with self.assertRaisesRegex(GuardError, 'missing'):
            self.check()
        for args, env in [(['--require-inventory', self.inventory], {}),
                          ([], {'PILOT_E2E_CREDENTIALS': str(self.inventory)}),
                          (['--generic'], {'PILOT_E2E_CREDENTIALS': str(self.inventory)})]:
            with self.subTest(args=args):
                self.assertNotEqual(self.cli(*args, env=env).returncode, 0)

    def test_malformed_empty_and_wrong_shape_inventory_fail(self):
        for raw in ('{', 'null', '"text"', '{}', '[]', '{"password":""}',
                    '{"password":42}', '{"password":"okay","number":NaN}', '{"password":"first","password":"second"}',
                    '[{"value":null}]', '{"owner":{"username":"only"}}'):
            with self.subTest(raw=raw):
                self.inventory.write_text(raw)
                self.assertNotEqual(self.cli('--require-inventory', self.inventory).returncode, 0)

    def test_malformed_supplemental_inventory_fails(self):
        (self.base / 'pilot-private-cookie.json').write_text('{broken')
        self.assertNotEqual(self.cli('--require-inventory', self.inventory).returncode, 0)

    def test_supplemental_secret_and_arbitrary_explicit_inventory_are_scanned(self):
        extra = self.base / 'pilot-private-credentials.json.secrets.json'
        extra.write_text(json.dumps([{'value': SYNTHETIC + '_extra'}]))
        self.inventory.write_text('{"password":"another-synthetic-password"}')
        (self.root / 'log.txt').write_text(SYNTHETIC + '_extra')
        with self.assertRaisesRegex(GuardError, 'known secret'):
            self.check()
        extra.unlink()
        explicit = self.base / 'named-differently.json'
        explicit.write_text(json.dumps({'token': SYNTHETIC + '_extra'}))
        with self.assertRaisesRegex(GuardError, 'known secret'):
            self.check(inventories=[self.inventory, explicit])

    def test_short_explicit_secret_is_not_silently_ignored(self):
        self.inventory.write_text('{"password":"tiny"}')
        (self.root / 'log.txt').write_text('tiny')
        with self.assertRaisesRegex(GuardError, 'known secret'):
            self.check()

    def test_preview_secret_without_inventory_is_scanned(self):
        (self.root / 'log.txt').write_text(SYNTHETIC)
        with self.assertRaisesRegex(GuardError, 'known secret'):
            self.check(inventories=[], generic=True, environ={'PREVIEW_SECRET': SYNTHETIC})

    def test_plain_secret_and_secret_crossing_read_boundary_fail(self):
        for prefix in (b'', b'x' * (64 * 1024 - 7)):
            with self.subTest(prefix=len(prefix)):
                (self.root / 'log.txt').write_bytes(prefix + SYNTHETIC.encode() + b'end')
                with self.assertRaisesRegex(GuardError, 'known secret'):
                    self.check()

    def test_compressed_and_nested_secrets_fail_including_disguised_archives(self):
        payloads = {
            'bundle.zip': zip_bytes(SYNTHETIC.encode()),
            'trace.bin': zip_bytes(SYNTHETIC.encode()),
            'evidence.tar': tar_bytes(SYNTHETIC.encode()),
            'evidence.gz': gzip.compress(SYNTHETIC.encode()),
            'evidence.bz2': bz2.compress(SYNTHETIC.encode()),
            'evidence.xz': lzma.compress(SYNTHETIC.encode()),
            'evidence.tgz': gzip.compress(tar_bytes(SYNTHETIC.encode())),
            'nested.zip': zip_bytes(zip_bytes(SYNTHETIC.encode()), name='inner.zip'),
            'nested.tar': tar_bytes(gzip.compress(SYNTHETIC.encode()), name='inner.gz'),
        }
        for name, payload in payloads.items():
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(payload)
                with self.assertRaises(GuardError):
                    self.check()
                path.unlink()

    def test_safe_archives_pass(self):
        for name, payload in {'bundle.zip': zip_bytes(), 'evidence.tar': tar_bytes(),
                              'report.gz': gzip.compress(b'evidence'), 'report.bz2': bz2.compress(b'evidence'),
                              'report.xz': lzma.compress(b'evidence'),
                              'nested.zip': zip_bytes(zip_bytes(), name='inner.zip')}.items():
            (self.root / name).write_bytes(payload)
        self.check()

    def test_private_names_in_directories_and_archives_fail(self):
        for name in ('credentials.json', 'storage-state.json', 'owner-state.json', 'private-secret.txt', 'storageState.json', 'storage_state.json',
                     'preview-access.txt', 'owner-r3-private-access.txt', 'private-access.txt'):
            with self.subTest(name=name):
                path = self.root / name
                path.mkdir()
                with self.assertRaisesRegex(GuardError, 'private artifact name'):
                    self.check()
                path.rmdir()
                (self.root / 'bundle.zip').write_bytes(zip_bytes(name=name))
                with self.assertRaisesRegex(GuardError, 'private artifact name'):
                    self.check()
                (self.root / 'bundle.zip').unlink()

    def test_no_secret_or_private_name_is_printed_on_failure_even_with_optimization(self):
        (self.root / (SYNTHETIC + '.txt')).write_text(SYNTHETIC)
        result = self.cli('--require-inventory', self.inventory, optimized=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(SYNTHETIC, result.stdout + result.stderr)
        self.assertNotIn(str(self.base), result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_archive_traversal_absolute_and_windows_paths_fail_without_extraction(self):
        for name in ('../escape.txt', '/absolute.txt', 'x/../../escape.txt', 'C:/drive.txt', 'x\\..\\escape.txt'):
            for maker in (zip_bytes, tar_bytes):
                with self.subTest(name=name, maker=maker.__name__):
                    path = self.root / ('archive.zip' if maker == zip_bytes else 'archive.tar')
                    path.write_bytes(maker(name=name))
                    with self.assertRaisesRegex(GuardError, 'unsafe archive member path'):
                        self.check()
                    path.unlink()
        self.assertFalse((self.base / 'escape.txt').exists())

    def test_filesystem_links_and_inventory_links_fail(self):
        link = self.root / 'link.txt'
        link.symlink_to(self.inventory)
        with self.assertRaisesRegex(GuardError, 'links'):
            self.check()
        link.unlink()
        target = self.base / 'source.json'
        self.inventory.rename(target)
        self.inventory.symlink_to(target)
        with self.assertRaisesRegex(GuardError, 'links'):
            self.check()

    def test_archive_links_and_special_files_fail(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            with self.subTest(kind=kind):
                (self.root / 'archive.tar').write_bytes(tar_bytes(kind=kind, linkname='../outside'))
                with self.assertRaisesRegex(GuardError, 'links or special'):
                    self.check()
        (self.root / 'archive.tar').unlink()
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            info = zipfile.ZipInfo('link')
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, '../outside')
        (self.root / 'archive.zip').write_bytes(output.getvalue())
        with self.assertRaisesRegex(GuardError, 'links or special'):
            self.check()

    def test_empty_missing_root_and_inventory_inside_root_fail(self):
        (self.root / 'report.json').unlink()
        with self.assertRaisesRegex(GuardError, 'no files'):
            self.check()
        (self.root / 'inside.json').write_text(self.inventory.read_text())
        with self.assertRaisesRegex(GuardError, 'inside artifact root'):
            self.check(inventories=[self.root / 'inside.json'])
        (self.root / 'inside.json').unlink()
        self.root.rmdir()
        with self.assertRaisesRegex(GuardError, 'missing'):
            self.check()

    def test_unsupported_corrupt_and_encrypted_archives_fail(self):
        for name, data in [('broken.zip', b'not a zip'), ('broken.gz', b'not gzip'),
                           ('broken.tar', b'not tar'), ('broken.bz2', b'not bz2'),
                           ('broken.xz', b'not xz'), ('encrypted.7z', b'opaque'),
                           ('disguised.bin', b'7z\xbc\xaf\x27\x1copaque')]:
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(data)
                self.assertNotEqual(self.cli('--require-inventory', self.inventory).returncode, 0)
                path.unlink()
        encrypted = bytearray(zip_bytes())
        struct.pack_into('<H', encrypted, 6, 1)
        central = encrypted.index(b'PK\x01\x02')
        struct.pack_into('<H', encrypted, central + 8, 1)
        (self.root / 'encrypted.zip').write_bytes(encrypted)
        with self.assertRaisesRegex(GuardError, 'encrypted'):
            self.check()

    def test_byte_count_depth_ratio_and_time_limits_fail_closed(self):
        (self.root / 'large.txt').write_bytes(b'A' * 2048)
        with self.assertRaisesRegex(GuardError, 'file size limit'):
            self.check(limits=replace(Limits(), file_bytes=1024))
        with self.assertRaisesRegex(GuardError, 'total byte limit'):
            self.check(limits=replace(Limits(), total_bytes=1024))
        (self.root / 'large.txt').unlink()
        with self.assertRaisesRegex(GuardError, 'entry limit'):
            self.check(limits=replace(Limits(), entries=0))
        with self.assertRaisesRegex(GuardError, 'time limit'):
            self.check(limits=replace(Limits(), seconds=-1))
        (self.root / 'nested.zip').write_bytes(zip_bytes(zip_bytes(), name='inner.zip'))
        with self.assertRaisesRegex(GuardError, 'depth limit'):
            self.check(limits=replace(Limits(), archive_depth=1))
        (self.root / 'nested.zip').unlink()
        for name, data in [('bomb.zip', zip_bytes(b'A' * 200000)), ('bomb.gz', gzip.compress(b'A' * 200000)),
                           ('bomb.xz', lzma.compress(b'A' * 200000))]:
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(data)
                with self.assertRaisesRegex(GuardError, 'expansion ratio'):
                    self.check()
                path.unlink()

    def test_zip_declared_entry_count_limit_and_zip64_fail_closed(self):
        data = bytearray(zip_bytes())
        end = data.index(b'PK\x05\x06')
        for count in (20000, 65535):
            with self.subTest(count=count):
                changed = bytearray(data)
                struct.pack_into('<HH', changed, end + 8, count, count)
                (self.root / 'count.zip').write_bytes(changed)
                with self.assertRaises(GuardError):
                    self.check()
        (self.root / 'count.zip').unlink()

    def test_zip_lzma_without_decoder_memory_bound_is_rejected(self):
        (self.root / 'lzma.zip').write_bytes(zip_bytes(compression=zipfile.ZIP_LZMA))
        with self.assertRaisesRegex(GuardError, 'unsupported ZIP compression'):
            self.check()

    def test_archive_total_expansion_and_entry_count_limits(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for index in range(20):
                archive.writestr(f'{index}.txt', b'bounded data ' * 100)
        data = output.getvalue()
        (self.root / 'many.zip').write_bytes(data)
        with self.assertRaisesRegex(GuardError, 'entry limit'):
            self.check(limits=replace(Limits(), entries=10))
        with self.assertRaisesRegex(GuardError, 'total byte limit'):
            self.check(limits=replace(Limits(), total_bytes=len(data) + 4000))

    def test_inventory_limits(self):
        with self.assertRaisesRegex(GuardError, 'inventory size limit'):
            self.check(limits=replace(Limits(), inventory_bytes=4))
        with self.assertRaisesRegex(GuardError, 'inventory secret limit'):
            self.check(limits=replace(Limits(), secret_count=0))
        (self.base / 'pilot-private-extra.json').write_text('[]')
        with self.assertRaisesRegex(GuardError, 'inventory file limit'):
            self.check(limits=replace(Limits(), inventory_files=1))

    def test_browser_session_recordings_are_blocked(self):
        for name in ('capture.har', 'session.trace', 'trace.zip', 'playwright-traces.zip'):
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(zip_bytes() if name.endswith('.zip') else b'{}')
                with self.assertRaisesRegex(GuardError, 'session recording'):
                    self.check()
                path.unlink()

    def test_known_secret_in_filename_is_blocked(self):
        (self.root / (SYNTHETIC + '.txt')).write_text('harmless')
        with self.assertRaisesRegex(GuardError, 'known secret'):
            self.check()

    def test_xz_memory_and_trailing_payload_limits(self):
        data = lzma.compress(b'evidence', filters=[{'id': lzma.FILTER_LZMA2, 'dict_size': 64 * 1024 * 1024}])
        (self.root / 'big-dictionary.xz').write_bytes(data)
        self.assertNotEqual(self.cli('--require-inventory', self.inventory).returncode, 0)
        (self.root / 'big-dictionary.xz').unlink()
        (self.root / 'trailing.xz').write_bytes(lzma.compress(b'evidence') + lzma.compress(b'other evidence'))
        with self.assertRaisesRegex(GuardError, 'trailing payload'):
            self.check()


if __name__ == '__main__':
    unittest.main()
