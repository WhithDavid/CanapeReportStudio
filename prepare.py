"""Generate offline CANape export scripts; never connect to CANape itself."""
import argparse
import json
import math
import re
import hashlib
import uuid
import struct
import zlib
from pathlib import Path


def quoted(value):
    return json.dumps(str(value).replace('/', '\\'), ensure_ascii=False)


def fingerprint(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def valid_png(path):
    """Check chunk boundaries and CRCs; this is not visual acceptance."""
    if not path.is_file():
        return False
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return False
    pos, first, pixels = 8, True, False
    while pos + 12 <= len(data):
        size = struct.unpack('>I', data[pos:pos+4])[0]
        end = pos + 12 + size
        if end > len(data):
            return False
        kind = data[pos+4:pos+8]
        payload = data[pos+8:pos+8+size]
        crc = struct.unpack('>I', data[pos+8+size:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            return False
        if first:
            if kind != b'IHDR' or size != 13:
                return False
            width, height = struct.unpack('>II', payload[:8])
            if width < 100 or height < 100:
                return False
            first = False
        pixels |= kind == b'IDAT' and size > 0
        if kind == b'IEND':
            return size == 0 and end == len(data) and pixels
        pos = end
    return False


def prepare(config_path, output):
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    jobs = config['jobs']
    if not jobs:
        raise ValueError('No jobs')
    identifiers = set()
    validated = []
    for job in jobs:
        key = job['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', key) or key in identifiers:
            raise ValueError(f'Invalid or duplicate job id: {key}')
        identifiers.add(key)
        source = Path(job['mf4']).resolve()
        if not source.is_file() or source.suffix.lower() != '.mf4':
            raise ValueError(f'Missing MF4: {source}')
        str(source).encode('gbk')
        alias = job['file_alias']
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', alias):
            raise ValueError('Invalid file alias')
        cursor = float(job['cursor_seconds'])
        if not math.isfinite(cursor) or cursor < 0:
            raise ValueError('Invalid cursor time')
        template = config['templates'][job['template']]
        for field in ('window_number', 'expected_page'):
            if type(template[field]) is not int or template[field] < 1:
                raise ValueError(f'Invalid {field}')
        if 'start_seconds' in job or 'end_seconds' in job:
            raise ValueError('Exact time-range control is not implemented yet')
        validated.append((job, template, source, cursor))
    output = output.resolve()
    str(output).encode('gbk')
    # A new directory prevents accidental reuse of old images or logs.
    output.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex
    manifest = {'project': config['project'], 'run_id': run_id, 'status': 'prepared_not_executed', 'jobs': []}
    for job, template, source, cursor in validated:
        key = job['id']
        image = output / f'{key}.png'
        log = output / f'{key}.csv'
        script = output / f'{key}.cns'
        fit = 'rc = ExecuteMenuCommand("CM_FIT");\nif (rc != 0) { return; }' if template.get('fit') else ''
        body = f'''// Offline display only. No measurement or calibration commands.
long rc, page;
char pageName[256];
double actualCursor;
rc = ReplaceMeasurementFile({quoted(job['file_alias'])}, {quoted(source)}, 1);
if (rc != 0) {{ return; }}
ShowWindowNumber({template['window_number']});
Sleep(500);
page = GetDisplayPageName(pageName);
if (page != {template['expected_page']}) {{ return; }}
{fit}
rc = SetGlobalCursorMode(1);
if (rc != 0) {{ return; }}
rc = SetGlobalCursor({cursor});
if (rc != 0) {{ return; }}
rc = GetGlobalCursor(actualCursor);
if (rc != 0) {{ return; }}
if (abs(actualCursor - {cursor}) > 0.001) {{ return; }}
rc = ExportDisplayPageByIndex(page - 1, {quoted(image)}, 0, "", 1);
if (rc != 0) {{ return; }}
Write("REPORT_BATCH_OK {run_id} {key} cursor=%f page=%d", actualCursor, page);
ExportWriteWindow({quoted(log)}, 1, 1);
'''
        script.write_text(body, encoding='gbk')
        manifest['jobs'].append({**job, 'mf4': str(source), 'source_sha256': fingerprint(source),
                                 'script': str(script), 'image': str(image), 'log': str(log)})
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Prepared {len(validated)} job(s): {output}')


def verify(manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    failed = False
    for job in manifest['jobs']:
        image, log = Path(job['image']), Path(job['log'])
        png_ok = valid_png(image)
        marker = f"REPORT_BATCH_OK {manifest.get('run_id', 'NO_RUN_ID')} {job['id']} "
        log_ok = log.is_file() and marker in log.read_text(encoding='utf-8', errors='replace')
        source = Path(job['mf4'])
        source_ok = source.is_file() and fingerprint(source) == job.get('source_sha256')
        ok = png_ok and log_ok and source_ok
        failed |= not ok
        print(f"{job['id']}: {'API_OUTPUT_OK_NEEDS_VISUAL_REVIEW' if ok else 'MISSING_OR_FAILED'}")
    return 1 if failed else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('prepare')
    build.add_argument('config', type=Path)
    build.add_argument('output', type=Path)
    check = sub.add_parser('verify')
    check.add_argument('manifest', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.config, args.output)
    else:
        raise SystemExit(verify(args.manifest))
