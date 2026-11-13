import json
import tempfile
import unittest
import struct
import zlib
from pathlib import Path
from prepare import prepare, verify, valid_png


def png():
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 100, 100, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\0'*300)*100)) + chunk(b'IEND', b''))


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / '常温三度.MF4'
        self.source.write_bytes(b'test fixture, not a real MF4')
        self.config = self.root / 'jobs.json'
        self.config.write_text(json.dumps({'project': 'test', 'templates': {'one': {
            'window_number': 13, 'expected_page': 6}}, 'jobs': [{'id': 'one',
            'mf4': str(self.source), 'file_alias': 'FILE1', 'cursor_seconds': 19,
            'template': 'one'}]}), encoding='utf-8')
        self.out = self.root / 'run'
        prepare(self.config, self.out)
        self.manifest = self.out / 'manifest.json'

    def test_chinese_encoding(self):
        self.assertIn('常温三度.MF4', (self.out/'one.cns').read_text(encoding='gbk'))

    def test_no_overwrite(self):
        with self.assertRaises(FileExistsError):
            prepare(self.config, self.out)

    def test_no_output_is_failure(self):
        self.assertEqual(verify(self.manifest), 1)

    def test_old_marker_rejected_and_matching_marker_accepted(self):
        (self.out/'one.png').write_bytes(png())
        (self.out/'one.csv').write_text('REPORT_BATCH_OK old one ', encoding='utf-8')
        self.assertEqual(verify(self.manifest), 1)
        run_id = json.loads(self.manifest.read_text())['run_id']
        (self.out/'one.csv').write_text(f'REPORT_BATCH_OK {run_id} one ', encoding='utf-8')
        self.assertEqual(verify(self.manifest), 0)
        self.source.write_bytes(b'changed')
        self.assertEqual(verify(self.manifest), 1)

    def test_corrupt_png_rejected(self):
        path = self.out/'broken.png'
        path.write_bytes(png()[:-5])
        self.assertFalse(valid_png(path))
        damaged = bytearray(png())
        damaged[20] ^= 1
        path.write_bytes(damaged)
        self.assertFalse(valid_png(path))


if __name__ == '__main__':
    unittest.main()
