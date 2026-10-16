"""Real filesystem transactions and adversarial publication boundaries."""
import hashlib
import errno
import ctypes
import json
import os
from pathlib import Path
import tempfile
import stat
import struct
import unittest
from unittest.mock import patch

try:
    from molscene import bundles
except ImportError:
    bundles = None


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(bundles, 'bundle implementation is required')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source.pse'
        self.source.write_bytes(b'original\r\n\x00snapshot')
        self.original = self.source.read_bytes()
        self.destination = self.root/'result'
        self.inventory = ('inputs/source.pse', 'report.json', 'logs/tool.log', 'manifest.json')

    def bundle(self, **kw):
        return bundles.Bundle(self.destination, protected=[self.source], inventory=self.inventory, **kw)

    def fill(self, bundle):
        record = bundle.copy_input(self.source, 'inputs/source.pse')
        self.assertEqual(record, {'path':'inputs/source.pse', 'sha256':hashlib.sha256(self.original).hexdigest(), 'bytes':len(self.original)})
        (bundle.stage/'report.json').write_text('{}', encoding='utf-8')
        (bundle.stage/'logs').mkdir(exist_ok=True)
        (bundle.stage/'logs/tool.log').write_bytes(b'')

    def test_publish_exact_inventory_hashes_and_empty_log(self):
        self.destination.mkdir()
        with self.bundle() as bundle:
            self.fill(bundle)
            self.assertEqual(bundle.stage.parent, self.destination.parent)
            self.assertEqual(bundle.scratch.parent, self.destination.parent)
            stage, scratch = bundle.stage, bundle.scratch
            self.assertEqual(bundle.publish(), self.destination)
        manifest = json.loads((self.destination/'manifest.json').read_text())
        self.assertEqual(set(manifest['files']), set(self.inventory)-{'manifest.json'})
        for name, record in manifest['files'].items():
            raw = (self.destination/name).read_bytes()
            self.assertEqual(record, {'sha256':hashlib.sha256(raw).hexdigest(), 'bytes':len(raw)})
        self.assertFalse(stage.exists())
        self.assertFalse(scratch.exists())
        self.assertFalse(bundle.failure_path.exists())
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_missing_extra_and_undeclared_frame_fail(self):
        for change in ('missing', 'extra', 'frame'):
            with self.subTest(change=change), self.assertRaises(ValueError):
                with self.bundle() as bundle:
                    self.fill(bundle)
                    if change == 'missing':
                        (bundle.stage/'report.json').unlink()
                    elif change == 'extra':
                        (bundle.stage/'other').write_bytes(b'x')
                    else:
                        (bundle.stage/'frames').mkdir()
                        (bundle.stage/'frames/frame_000002.png').write_bytes(b'x')
                    bundle.publish()
            self.assertFalse(self.destination.exists())

    def test_protected_ancestry_and_nonempty_destination(self):
        for dest in (self.root, self.source, self.source/'nested'):
            with self.subTest(dest=dest), self.assertRaises(ValueError):
                with bundles.Bundle(dest, protected=[self.source], inventory=['manifest.json']):
                    pass
        self.destination.mkdir()
        sentinel = self.destination/'keep'
        sentinel.write_bytes(b'untouched')
        with self.assertRaises(ValueError):
            with self.bundle():
                pass
        self.assertEqual(sentinel.read_bytes(), b'untouched')

    def test_invalid_inventory_paths_and_aliases(self):
        for inventory in [('manifest.json','../source.pse'), ('manifest.json','/absolute'),
                ('manifest.json','a\\b'), ('manifest.json','x','X'),
                ('manifest.json','a','a/b'), ('manifest.json','logs/*')]:
            with self.subTest(inventory=inventory), self.assertRaises(ValueError):
                bundles.Bundle(self.destination, protected=[self.source], inventory=inventory)

    def test_source_change_during_copy_fails_without_publishing(self):
        real_copy = bundles.shutil.copyfileobj
        def mutate(source, destination, *args, **kwargs):
            result = real_copy(source, destination, *args, **kwargs)
            self.source.write_bytes(b'external edit')
            return result
        with self.assertRaises(ValueError):
            with self.bundle() as bundle, patch.object(bundles.shutil, 'copyfileobj', side_effect=mutate):
                bundle.copy_input(self.source, 'inputs/source.pse')
        self.assertEqual(self.source.read_bytes(), b'external edit')
        self.assertFalse(self.destination.exists())

    def test_input_copy_does_not_write_a_replaced_leaf(self):
        real_copy = bundles.shutil.copyfileobj
        def replace(source, destination, *args, **kwargs):
            # On POSIX an open output can be unlinked/replaced; Windows refuses
            # this attack while the handle is open. Both preserve the outsider.
            target = Path(destination.name)
            try:
                target.unlink()
            except PermissionError:
                return real_copy(source, destination, *args, **kwargs)
            target.write_bytes(b'outsider')
            return real_copy(source, destination, *args, **kwargs)
        with self.bundle() as bundle:
            with patch.object(bundles.shutil, 'copyfileobj', side_effect=replace):
                try:
                    bundle.copy_input(self.source, 'inputs/source.pse')
                except ValueError:
                    self.assertEqual((bundle.stage/'inputs/source.pse').read_bytes(), b'outsider')
                else:
                    self.assertEqual((bundle.stage/'inputs/source.pse').read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_empty_race_created_destination_is_not_replaced(self):
        real_rename = bundles._rename_noreplace
        def race(source, destination):
            destination.mkdir()
            return real_rename(source, destination)
        with self.assertRaises(OSError):
            with self.bundle() as bundle:
                self.fill(bundle)
                with patch.object(bundles, '_rename_noreplace', side_effect=race):
                    bundle.publish()
        self.assertTrue(self.destination.is_dir())
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_cancellation_after_manifest_prevents_commit(self):
        cancelled = [False]
        real_manifest = bundles.write_manifest
        def cancel(stage, inventory):
            result = real_manifest(stage, inventory)
            cancelled[0] = True
            return result
        with self.assertRaises(bundles.CancelledError):
            with self.bundle(cancelled=lambda:cancelled[0]) as bundle:
                self.fill(bundle)
                with patch.object(bundles, 'write_manifest', side_effect=cancel):
                    bundle.publish()
        self.assertFalse(self.destination.exists())

    def test_post_manifest_payload_change_or_extra_entry_prevents_publish(self):
        real_manifest = bundles.write_manifest
        for change in ('extra', 'payload', 'source'):
            def mutate(stage, inventory):
                result = real_manifest(stage, inventory)
                if change == 'extra':
                    (stage/'unexpected').write_bytes(b'late file')
                elif change == 'payload':
                    (stage/'report.json').write_bytes(b'changed')
                else:
                    self.source.write_bytes(b'external edit')
                return result
            with self.subTest(change=change), self.assertRaises(ValueError):
                with self.bundle() as bundle:
                    self.fill(bundle)
                    with patch.object(bundles, 'write_manifest', side_effect=mutate):
                        bundle.publish()
            self.assertFalse(self.destination.exists())
            self.source.write_bytes(self.original)

    def test_bounded_failure_logs_do_not_copy_scientific_inputs(self):
        with patch.object(bundles, 'MAX_LOG_BYTES', 8):
            with self.assertRaisesRegex(RuntimeError, 'tool failed'):
                with self.bundle() as bundle:
                    log = bundle.scratch/'stderr'
                    bundle.retain_failure_logs({'tool':log})
                    log.write_bytes(b'123456789abc')
                    raise RuntimeError('tool failed')
        self.assertEqual((bundle.failure_path/'logs/tool.log').read_bytes(), b'12345678')
        record = json.loads((bundle.failure_path/'failure.json').read_text())['logs']['tool']
        self.assertEqual(record['bytes'], 8)
        self.assertTrue(record['truncated'])

    def test_published_logs_must_respect_bound(self):
        with patch.object(bundles, 'MAX_LOG_BYTES', 8), self.assertRaises(ValueError):
            with self.bundle() as bundle:
                self.fill(bundle)
                log = bundle.stage/'logs/tool.log'
                bundle.retain_failure_logs({'tool':log})
                log.write_bytes(b'123456789')
                bundle.publish()
        self.assertFalse(self.destination.exists())

    def test_replaced_owned_directory_is_preserved_by_cleanup(self):
        with self.assertRaises(ValueError):
            with self.bundle() as bundle:
                original_stage = bundle.stage.with_name(bundle.stage.name+'-original')
                bundle.stage.rename(original_stage)
                bundle.stage.mkdir()
                (bundle.stage/'sentinel').write_bytes(b'outsider')
                bundle.publish()
        self.assertEqual((bundle.stage/'sentinel').read_bytes(), b'outsider')

    def test_copied_source_change_before_publish_is_rejected(self):
        with self.assertRaises(ValueError):
            with self.bundle() as bundle:
                self.fill(bundle)
                self.source.write_bytes(b'external edit')
                bundle.publish()
        self.assertEqual(self.source.read_bytes(), b'external edit')
        self.assertFalse(self.destination.exists())

    def test_destination_created_between_check_and_rename_is_not_replaced(self):
        real_rename = bundles._rename_noreplace
        def race(source, destination):
            destination.mkdir()
            (destination/'sentinel').write_bytes(b'race winner')
            return real_rename(source, destination)
        with self.assertRaises(OSError):
            with self.bundle() as bundle:
                self.fill(bundle)
                with patch.object(bundles, '_rename_noreplace', side_effect=race):
                    bundle.publish()
        self.assertEqual((self.destination/'sentinel').read_bytes(), b'race winner')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_existing_destination_becomes_nonempty_before_removal(self):
        self.destination.mkdir()
        with self.assertRaises(ValueError):
            with self.bundle() as bundle:
                self.fill(bundle)
                (self.destination/'sentinel').write_bytes(b'race winner')
                bundle.publish()
        self.assertEqual((self.destination/'sentinel').read_bytes(), b'race winner')

    def test_cancellation_before_commit_retains_only_registered_logs(self):
        cancel = [False]
        with self.assertRaises(bundles.CancelledError):
            with self.bundle(cancelled=lambda:cancel[0]) as bundle:
                self.fill(bundle)
                log = bundle.scratch/'solver.log'
                bundle.retain_failure_logs({'solver.stderr':log})
                log.write_bytes(b'diagnostic\x00bytes')
                cancel[0] = True
                bundle.publish()
        self.assertFalse(self.destination.exists())
        self.assertEqual((bundle.failure_path/'logs/solver.stderr.log').read_bytes(), b'diagnostic\x00bytes')
        failure = json.loads((bundle.failure_path/'failure.json').read_text())
        self.assertTrue(failure['cancelled'])
        files = {p.relative_to(bundle.failure_path).as_posix() for p in bundle.failure_path.rglob('*') if p.is_file()}
        self.assertEqual(files, {'logs/solver.stderr.log','failure.json'})
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_failure_log_path_must_be_owned(self):
        with self.bundle() as bundle:
            with self.assertRaises(ValueError):
                bundle.retain_failure_logs({'source':self.source})

    def test_readonly_scratch_is_cleaned_before_success(self):
        with self.bundle() as bundle:
            self.fill(bundle)
            readonly = bundle.scratch/'readonly-tool-output'
            readonly.write_bytes(b'owned tool temporary')
            readonly.chmod(stat.S_IREAD)
            # Restore only our own test file if the regression leaves it behind.
            self.addCleanup(lambda: readonly.chmod(stat.S_IWRITE) if readonly.exists() else None)
            bundle.publish()
        self.assertTrue(self.destination.is_dir())
        self.assertFalse(bundle.scratch.exists())
        self.assertFalse(bundle.failure_path.exists())
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_scratch_cleanup_failure_never_commits(self):
        original_cleanup = bundles._cleanup_owned
        failure = PermissionError('locked scratch output')
        def blocked(path, identity):
            if path == bundle.scratch:
                raise failure
            return original_cleanup(path, identity)
        with self.assertRaises(PermissionError) as raised:
            with self.bundle() as bundle:
                self.fill(bundle)
                with patch.object(bundles, '_cleanup_owned', side_effect=blocked):
                    bundle.publish()
        self.assertIs(raised.exception, failure)
        self.assertFalse(self.destination.exists())
        self.assertEqual(json.loads((bundle.failure_path/'failure.json').read_text())['error']['message'], 'locked scratch output')

    def test_scratch_log_survives_late_publication_failure(self):
        with self.assertRaisesRegex(OSError, 'publication denied'):
            with self.bundle() as bundle:
                self.fill(bundle)
                log = bundle.scratch/'solver.stderr'
                log.write_bytes(b'last diagnostic')
                bundle.retain_failure_logs({'solver':log})
                with patch.object(bundles, '_rename_noreplace', side_effect=OSError('publication denied')):
                    bundle.publish()
        self.assertFalse(self.destination.exists())
        self.assertEqual((bundle.failure_path/'logs/solver.log').read_bytes(), b'last diagnostic')

    def test_log_roles_reject_case_aliases_and_windows_devices(self):
        with self.bundle() as bundle:
            a, b = bundle.scratch/'a', bundle.scratch/'b'
            for roles in ({'solver':a, 'SOLVER':b}, {'CON':a}, {'nul.output':a}, {'COM1':a}, {'LPT9.more':a}):
                with self.subTest(roles=list(roles)), self.assertRaises(ValueError):
                    bundle.retain_failure_logs(roles)
            # Rejected batches must not partially register their first entry.
            bundle.retain_failure_logs({'SOLVER':b})
            with self.assertRaises(ValueError):
                bundle.retain_failure_logs({'solver':a})

    def test_log_copy_failure_preserves_original_error_and_failure_metadata(self):
        original_error = RuntimeError('original solver failure')
        real_open = Path.open
        def fail_log(path, *args, **kwargs):
            if path == bundle.failure_path/'logs/solver.log':
                raise PermissionError('evidence denied')
            return real_open(path, *args, **kwargs)
        with patch.object(Path, 'open', fail_log), self.assertRaises(RuntimeError) as raised:
            with self.bundle() as bundle:
                log = bundle.scratch/'stderr'
                log.write_bytes(b'diagnostic')
                bundle.retain_failure_logs({'solver':log})
                raise original_error
        self.assertIs(raised.exception, original_error)
        metadata = json.loads((bundle.failure_path/'failure.json').read_text())
        self.assertEqual(metadata['error']['message'], 'original solver failure')
        self.assertEqual(metadata['evidence_errors'][0]['role'], 'solver')
        self.assertIn('evidence denied', metadata['evidence_errors'][0]['message'])

    def test_failure_metadata_write_error_does_not_mask_operation_error(self):
        original_error = RuntimeError('original operation failure')
        with patch.object(bundles, 'write_json', side_effect=PermissionError('evidence disk denied')):
            with self.assertRaises(RuntimeError) as raised:
                with self.bundle():
                    raise original_error
        self.assertIs(raised.exception, original_error)
        self.assertIn('evidence disk denied', str(original_error.bundle_diagnostics))

    @unittest.skipUnless(os.name == 'nt', 'Windows junction mechanism')
    def test_windows_junction_rejected_without_traversal(self):
        # Direct bounded Win32 calls: no visible helper, shell or global changes.
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                           ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
        create.restype = ctypes.c_void_p
        ioctl = kernel.DeviceIoControl
        ioctl.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                          ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p)
        ioctl.restype = ctypes.c_int
        close = kernel.CloseHandle
        close.argtypes = (ctypes.c_void_p,)
        close.restype = ctypes.c_int
        outside = self.root/'junction-target'
        outside.mkdir()
        sentinel = outside/'sentinel'
        sentinel.write_bytes(b'external bytes')
        with self.bundle() as bundle:
            junction = bundle.stage/'inputs'
            junction.mkdir()
            substitute = ('\\??\\'+str(outside)).encode('utf-16-le')
            printable = str(outside).encode('utf-16-le')
            strings = substitute+b'\0\0'+printable+b'\0\0'
            buffer = struct.pack('<IHHHHHH', 0xA0000003, 8+len(strings), 0,
                                 0, len(substitute), len(substitute)+2, len(printable))+strings
            handle = create(str(junction), 0x40000000, 7, None, 3, 0x02200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                count = ctypes.c_uint32()
                raw = ctypes.create_string_buffer(buffer)
                result = ioctl(handle, 0x900A4, raw, len(buffer), None, 0, ctypes.byref(count), None)
                error = ctypes.get_last_error() if not result else 0
            finally:
                close(handle)
            if error in (1, 50):
                self.skipTest('filesystem does not support junction reparse data: WinError '+str(error))
            if error:
                raise ctypes.WinError(error)
            self.assertTrue(junction.lstat().st_file_attributes & 0x400)
            with self.assertRaises(ValueError):
                bundle.copy_input(self.source, 'inputs/source.pse')
        self.assertEqual(sentinel.read_bytes(), b'external bytes')
        self.assertEqual(set(outside.iterdir()), {sentinel})

    def test_scientific_copies_cannot_be_registered_as_failure_logs(self):
        with self.bundle() as bundle:
            self.fill(bundle)
            with self.assertRaises(ValueError):
                bundle.retain_failure_logs({'source':bundle.stage/'inputs/source.pse'})
        with self.bundle() as bundle:
            bundle.retain_failure_logs({'source':bundle.stage/'inputs/source.pse'})
            with self.assertRaises(ValueError):
                bundle.copy_input(self.source, 'inputs/source.pse')

    def test_nested_symlink_rejected_and_cleanup_preserves_target(self):
        with self.bundle() as bundle:
            target = self.root/'outside'
            target.mkdir()
            (target/'sentinel').write_bytes(b'keep')
            try:
                (bundle.stage/'inputs').symlink_to(target, target_is_directory=True)
            except OSError as exc:
                if getattr(exc, 'winerror', None) not in (1, 50, 1314) and exc.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM):
                    raise
                self.skipTest('filesystem does not support creating symlink: '+str(exc))
            with self.assertRaises(ValueError):
                bundle.copy_input(self.source, 'inputs/source.pse')
        self.assertEqual((target/'sentinel').read_bytes(), b'keep')

    def test_hardlink_alias_rejected_without_source_damage(self):
        with self.bundle() as bundle:
            self.fill(bundle)
            (bundle.stage/'report.json').unlink()
            try:
                os.link(self.source, bundle.stage/'report.json')
            except OSError as exc:
                if getattr(exc, 'winerror', None) not in (1, 50, 1314) and exc.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM):
                    raise
                self.skipTest('filesystem does not support creating hardlink: '+str(exc))
            with self.assertRaises(ValueError):
                bundle.publish()
        self.assertEqual(self.source.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
