"""Reusable offline MF4 -> native CANape export. No measurement/ECU writes."""
import argparse
import configparser
import json
import math
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from prepare import fingerprint, quoted, valid_png
from report_catalog import condition_key, validate_mapping

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'deps'))


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save_json(path, data):
    # Replace atomically so interruption cannot leave a half-written state file.
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError(f'Invalid identifier: {value}')
    return value


def segment_values(segment):
    start, end, cursor = (float(segment[k]) for k in ('start', 'end', 'cursor'))
    if not all(math.isfinite(v) for v in (start, end, cursor)) or not 0 <= start <= cursor <= end or start == end:
        raise ValueError('Require 0 <= start <= cursor <= end, start < end')
    return start, end, cursor


def validate_profile(profile):
    if profile.get('status') == 'pending_user_spec':
        raise ValueError('Function profile is awaiting user specification; no default signals will be substituted')
    names = profile['signals']
    if not names or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError('Signal list must be nonempty and unique')
    if profile.get('axis_mode', 'common') != 'common':
        raise ValueError('Separate axes reserved, not implemented')
    if profile.get('selection_mode', 'manual') != 'manual':
        raise ValueError('Automatic event selection reserved, not implemented')
    width = profile.get('legend_width', 480)
    if type(width) is not int or not 250 <= width <= 700:
        raise ValueError('legend_width must be 250..700')
    paper = profile.get('paper_mm')
    if paper is not None and (not isinstance(paper, list) or len(paper) != 2 or
            any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 80 <= v <= 600 for v in paper) or paper[0] < paper[1]):
        raise ValueError('paper_mm must be landscape [width, height], each 80..600 mm')
    limits = profile.get('y_range', 'auto')
    if limits != 'auto':
        if not isinstance(limits, list) or len(limits) != 2 or not all(math.isfinite(float(v)) for v in limits) or not limits[0] < limits[1]:
            raise ValueError('y_range must be auto or [min, max]')


def analyze(mdf, names, segment, cache=None):
    import numpy as np
    cache = {} if cache is None else cache
    start, end, cursor = segment_values(segment)
    rows = []
    for name in names:
        if name not in cache:
            occurrences = mdf.whereis(name)
            if len(occurrences) != 1:
                raise ValueError(f'{name}: missing or ambiguous channel ({len(occurrences)} matches)')
            cache[name] = mdf.get(name, raw=True).physical(ignore_value2text_conversions=True)
        signal = cache[name]
        values, times = signal.samples, signal.timestamps
        if values.ndim != 1 or values.dtype.kind not in 'biuf' or not len(times):
            raise ValueError(f'{name}: unsupported/non-numeric channel')
        if start < times[0] or end > times[-1]:
            raise ValueError(f'{name}: requested segment outside recorded time range')
        indices = np.flatnonzero((times >= start) & (times <= end))
        if not len(indices):
            raise ValueError(f'{name}: no samples in segment')
        invalid = signal.invalidation_bits
        if invalid is not None and np.any(invalid[indices]):
            raise ValueError(f'{name}: invalid samples in selected interval')
        selected = values[indices].astype(float)
        if not np.all(np.isfinite(selected)):
            raise ValueError(f'{name}: nonfinite values')
        rows.append({'name': name, 'unit': signal.unit, 'min': float(selected.min()),
                     'max': float(selected.max()), 'samples': len(indices)})
    low, high = min(r['min'] for r in rows), max(r['max'] for r in rows)
    span = high - low or max(abs(high), 1)
    step = 10 ** math.floor(math.log10(span / 8))
    limits = [math.floor((low - span*.03)/step)*step, math.ceil((high + span*.03)/step)*step]
    return rows, limits


def native_config(source, names, title, layout, segment, profile, limits, target):
    cfg = configparser.RawConfigParser(interpolation=None)
    cfg.optionxform = str
    cfg.read(ROOT / 'templates/common_axis_v4.cna', encoding='gbk')
    channels = {cfg[s]['ChannelName']: (dict(cfg[s]), dict(cfg[s+'_DISPLAY_1']))
                for s in cfg.sections() if re.fullmatch(r'MDF_OBJECT_\d+', s)}
    default = next(iter(channels.values()))
    for section in list(cfg.sections()):
        if section.startswith('MDF_OBJECT_'):
            cfg.remove_section(section)
    cfg['MASTER_CONFIG']['DisplayName'] = title
    cfg['DISPLAY_PAGE_1'].update({'Name': title, 'PrintLayout': layout})
    cfg['MDF_LIST']['Count'] = str(len(names))
    start, end, cursor = segment_values(segment)
    width = profile.get('legend_width', 480)
    cfg['WINDOW_1'].update({'Title': title, 'XMin': str(round(start*1000)),
        'XMax': str(round(end*1000)), 'XStart': str(round(start*1000)),
        'XLen': str(round((end-start)*1000)), 'MARKER_TIME_1': str(round(cursor*1e9)),
        'LegendPixWidth': str(width)})
    cfg['WINDOW_1_LEG_COLUMNS']['COL_0'] = f'31, {width}'
    cfg['Y_COMMON_AXIS_1'].update(dict(zip(('YMin','YMax','YMin-Home','YMax-Home'), map(str,limits*2))))
    for i, name in enumerate(names, 1):
        base, display = channels.get(name, default)
        cfg[f'MDF_OBJECT_{i}'] = {**base, 'ChannelName': name, 'OrginalFileName': str(source), 'FileID': 'FILE1'}
        palette=[65280,255,65535,16776960,16711935,33023,16744448,16777215,
                 32768,128,32896,8421376,8388736,16512,8388608,8454143]
        cfg[f'MDF_OBJECT_{i}_DISPLAY_1'] = {**display, 'Color':str(palette[(i-1)%len(palette)]), 'Index': str(len(names)-i),
            'StoredFocused': str(int(i==1)), 'YMin': str(limits[0]), 'YMax': str(limits[1]),
            'YMinHome': str(limits[0]), 'YMaxHome': str(limits[1])}
    printing = configparser.RawConfigParser(interpolation=None)
    printing.optionxform = str
    printing.read(ROOT / 'templates/no_indices.cna', encoding='utf-8')
    for section in printing.sections():
        if section.startswith('PRINT_LAYOUT_'):
            cfg[section] = dict(printing[section])
    cfg['PRINT_LAYOUT_0']['Name'] = layout
    if 'paper_mm' in profile:
        paper_width, paper_height = profile['paper_mm']
        cfg['PRINT_LAYOUT_0'].update({'PaperSize': '0', 'PaperOrientation': '2',
            'PaperWidth': str(paper_width), 'PaperLength': str(paper_height)})
    with target.open('w', encoding='gbk') as stream:
        cfg.write(stream, space_around_delimiters=False)


def prepare_case(case_path, output):
    from asammdf import MDF
    case = read_json(case_path)
    profiles = read_json(ROOT/'profiles.json')
    purpose = case.get('purpose', 'preview')
    if purpose not in ('preview','report'):
        raise ValueError('purpose must be preview or report')
    catalog = None
    if purpose == 'report':
        if not case.get('catalog'):
            raise ValueError('Formal report jobs require catalog')
        catalog_path = Path(case['catalog'])
        if not catalog_path.is_absolute():
            catalog_path = Path(case_path).resolve().parent/catalog_path
        catalog = read_json(catalog_path)
    # Validate every mapping before creating files or reading large MF4 data.
    for job in case['jobs']:
        base = profiles[job['profile']]
        if base.get('status') == 'pending_user_spec':
            raise ValueError('Function profile pending user specification: '+job['profile'])
        if any(k in job.get('display',{}) for k in ('status','function_id','confirmation_note')):
            raise ValueError('display cannot override profile approval or function identity')
        if purpose == 'report':
            validate_mapping(job,base,catalog)
            if 'signals' in job.get('display',{}):
                raise ValueError('Formal signal changes require a separately confirmed profile')
        elif job.get('report_item_id'):
            raise ValueError('A report_item_id requires purpose=report; preview cannot silently fill a report')
        elif 'temperature_c' in job.get('condition',{}):
            condition_key(job['condition'])
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'version': 2, 'purpose':purpose, 'run_id': uuid.uuid4().hex[:12], 'project': case['project'],
                'probe': case['probe'], 'jobs': []}
    if catalog is not None:
        manifest['catalog'] = str(catalog_path.resolve())
        manifest['catalog_sha256'] = fingerprint(catalog_path)
    seen = set()
    if not case['jobs']:
        raise ValueError('No jobs')
    for job in case['jobs']:
        profile = {**profiles[job['profile']], **job.get('display', {})}
        validate_profile(profile)
        source = Path(job['mf4']).resolve()
        if not source.is_file() or source.suffix.lower() != '.mf4':
            raise ValueError(f'Missing MF4: {source}')
        str(source).encode('gbk')
        digest = fingerprint(source)
        if not job['segments']:
            raise ValueError('No segments')
        with MDF(source) as mdf:
            signal_cache = {}
            for segment in job['segments']:
                key = identifier(job['id'])+'__'+identifier(segment['id'])
                if key in seen:
                    raise ValueError(f'Duplicate output id: {key}')
                seen.add(key)
                rows, limits = analyze(mdf, profile['signals'], segment, signal_cache)
                if profile.get('y_range', 'auto') != 'auto':
                    limits = profile['y_range']
                folder = output/key
                folder.mkdir()
                title = 'REPORT_'+manifest['run_id']+'_'+str(len(seen))
                layout = 'LAYOUT_'+manifest['run_id']+'_'+str(len(seen))
                cna = folder/'display.cna'
                native_config(source, profile['signals'], title, layout, segment, profile, limits, cna)
                manifest['jobs'].append({'id': key, 'mf4': str(source), 'source_sha256': digest,
                    'report_item_id': job.get('report_item_id'), 'profile_id':job['profile'],
                    'condition': job.get('condition', {}), 'report_slot': job.get('report_slot'),
                    'profile': profile, 'segment': segment, 'analysis': rows, 'y_range': limits,
                    'title': title, 'layout': layout, 'cna': str(cna), 'cna_sha256': fingerprint(cna)})
    save_json(output/'manifest.json', manifest)
    save_json(output/'case_snapshot.json', case)
    print(f'PREPARED {len(manifest["jobs"])} image(s): {output}')
    return output/'manifest.json'


def make_script(job, attempt, initialize, token):
    log, image = attempt/'canape.csv', attempt/'image.png'
    def check(expr):
        return f'rc={expr};\nif(rc!=0){{Write("FAILED {token} rc=%d",rc);ExportWriteWindow({quoted(log)},1,1);return;}}\n'
    body = 'long rc;\ndouble actual;\n'
    body += check(f'ReplaceMeasurementFile("FILE1",{quoted(job["mf4"])},1)')
    if initialize:
        body += check(f'LoadConfiguration({quoted(job["cna"])})')
    body += f'ShowWindow({quoted(job["title"])});\nSleep(1000);\n'
    if initialize:
        for command in ('CM_LEGEND_SELECT_ALL', 'CM_LEGEND_LN_VALUE', 'CM_MEASURE_POINT'):
            body += check(f'ExecuteWindowMenuCommand({quoted(command)},{quoted(job["title"])},1)')
        body += f'Write("INITIALIZED {token}");\nExportWriteWindow({quoted(log)},1,1);\n'
    cursor = float(job['segment']['cursor'])
    body += check('SetGlobalCursorMode(1)') + check(f'SetGlobalCursor({cursor})')
    body += 'Sleep(500);\n' + check('GetGlobalCursor(actual)')
    body += f'if(abs(actual-{cursor})>0.001){{Write("FAILED {token} cursor");ExportWriteWindow({quoted(log)},1,1);return;}}\n'
    body += check(f'ExportWindow({quoted(job["title"])},{quoted(image)},0,"PRINT_LAYOUT \'{job["layout"]}\'",1)')
    body += f'Write("EXPORTED {token}");\nExportWriteWindow({quoted(log)},1,1);\n'
    return body


def image_check(path, black_required=True):
    if not valid_png(path):
        return {'ok': False, 'reason': 'missing_or_invalid_png'}
    from PIL import Image
    with Image.open(path) as source:
        source.load()
        rgb = source.convert('RGB')
        # Right-central region avoids white legend and outside page margins.
        region = rgb.crop((int(rgb.width*.55), int(rgb.height*.12), int(rgb.width*.95), int(rgb.height*.85)))
        pixels = region.get_flattened_data() if hasattr(region, 'get_flattened_data') else region.getdata()
        dark = sum(max(p)<45 for p in pixels)/len(pixels)
        ok = not black_required or dark > .60
        return {'ok': ok, 'reason': 'needs_visual_review' if ok else 'print_background_not_black',
                'dark_ratio': round(dark, 4), 'size': list(rgb.size)}


def check_attempt(job, state):
    attempt = Path(state['attempt'])
    log = attempt/'canape.csv'
    text = log.read_text(errors='replace') if log.exists() else ''
    result = image_check(attempt/'image.png', job['profile'].get('require_black_background', True))
    result['export_marker'] = 'EXPORTED '+state['token'] in text
    result['source_unchanged'] = fingerprint(Path(job['mf4'])) == job['source_sha256']
    result['ok'] = result['ok'] and result['export_marker'] and result['source_unchanged']
    return result


def verify_case(manifest_path):
    manifest_path = Path(manifest_path)
    manifest = read_json(manifest_path)
    summary = []
    for job in manifest['jobs']:
        state_path = Path(job['cna']).parent/'state.json'
        state = read_json(state_path) if state_path.exists() else {}
        result = check_attempt(job, state) if 'attempt' in state else {'ok': False, 'reason': 'not_run'}
        summary.append({'id': job['id'], 'condition': job['condition'], 'report_slot': job['report_slot'],
            'report_item_id':job.get('report_item_id'), 'purpose':manifest.get('purpose','preview'),
            'segment': job['segment'], 'signals': job['profile']['signals'],
            'image': str(Path(state['attempt'])/'image.png') if 'attempt' in state else None,
            'verification': result, 'visual_review': 'required'})
    save_json(manifest_path.parent/'summary.json', summary)
    print('SUMMARY '+str(manifest_path.parent/'summary.json'))
    return all(item['verification']['ok'] for item in summary)


@contextmanager
def exclusive_run():
    import msvcrt
    path = ROOT/'runs/.workflow.lock'
    path.parent.mkdir(exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError('Another report workflow is running') from error
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def probe(manifest, log, script=None):
    command = [manifest['probe'], manifest['project']]
    if script:
        command.append(str(script))
    try:
        result = subprocess.run(command, capture_output=True, timeout=55)
    except subprocess.TimeoutExpired as error:
        log.write_bytes((error.stdout or b'')+(error.stderr or b'')+b'\nPROBE_TIMEOUT')
        raise RuntimeError('Connection timed out; confirm ASAP3 Active and close setting dialogs. No automatic retry.') from error
    text = (result.stdout+result.stderr).decode('utf-8', errors='replace')
    log.write_text(text, encoding='utf-8')
    if result.returncode or 'MeasurementState=0' not in text:
        raise RuntimeError('CANape unavailable/not idle. Confirm ASAP3 Active; see '+str(log))
    match = re.search(r'ConnectedProjectDirectory=(.+)', text)
    if not match or Path(match[1].strip()).resolve() != Path(manifest['project']).resolve():
        raise RuntimeError('Connected CANape project mismatch')
    return text


def run_case(manifest_path, repeat=False):
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path)
    if manifest.get('purpose') == 'report':
        catalog_path = Path(manifest['catalog'])
        if fingerprint(catalog_path) != manifest['catalog_sha256']:
            raise RuntimeError('Report catalog changed; prepare again')
        catalog = read_json(catalog_path)
        for job in manifest['jobs']:
            validate_mapping(job,job['profile'],catalog)
    with exclusive_run():
        verified_sources = set()
        for job in manifest['jobs']:
            source = Path(job['mf4'])
            if str(source) not in verified_sources:
                if fingerprint(source) != job['source_sha256']:
                    raise RuntimeError('MF4 changed: '+str(source))
                verified_sources.add(str(source))
            if fingerprint(Path(job['cna'])) != job['cna_sha256']:
                raise RuntimeError('Generated template changed; prepare a new run')
            folder = Path(job['cna']).parent
            state_path = folder/'state.json'
            state = read_json(state_path) if state_path.exists() else {'initialized': False}
            if state.get('execution_pending'):
                old = Path(state['attempt'])/'canape.csv'
                previous = old.read_text(errors='replace') if old.exists() else ''
                if not any(marker+state['token'] in previous for marker in ('EXPORTED ', 'FAILED ')):
                    raise RuntimeError('Previous script outcome uncertain; inspect its log before another submission')
                state['execution_pending'] = False
            if state.get('initialization_pending'):
                old = Path(state['attempt'])/'canape.csv'
                if old.is_file() and 'INITIALIZED '+state['token'] in old.read_text(errors='replace'):
                    state.update(initialized=True, initialization_pending=False)
                else:
                    raise RuntimeError('Initialization outcome uncertain; do not replay toggles. Inspect logs or prepare a new run.')
            if state.get('status') == 'API_OUTPUT_OK_NEEDS_VISUAL_REVIEW' and not repeat and check_attempt(job,state)['ok']:
                print('SKIP completed '+job['id']); continue
            token = uuid.uuid4().hex
            attempt = folder/('attempt_'+token[:12])
            attempt.mkdir()
            # Preflight occurs before reserving initialization; connection failures are safe to retry.
            probe(manifest, attempt/'preflight.txt')
            initialize = not state.get('initialized', False)
            script = attempt/'render.cns'
            script.write_text(make_script(job, attempt, initialize, token), encoding='gbk')
            state.update(attempt=str(attempt), token=token, initialization_pending=initialize,
                         execution_pending=True, status='SUBMITTING')
            save_json(state_path, state)
            probe(manifest, attempt/'submission.txt', script)
            deadline = time.monotonic()+30
            log = attempt/'canape.csv'
            text = ''
            while time.monotonic()<deadline:
                text = log.read_text(errors='replace') if log.exists() else ''
                if 'EXPORTED '+token in text or 'FAILED '+token in text:
                    break
                time.sleep(.25)
            if 'INITIALIZED '+token in text:
                state.update(initialized=True, initialization_pending=False)
            state['execution_pending'] = not any(marker+token in text for marker in ('EXPORTED ','FAILED '))
            result = check_attempt(job, state)
            state['status'] = 'API_OUTPUT_OK_NEEDS_VISUAL_REVIEW' if result['ok'] else 'FAILED'
            save_json(attempt/'verification.json', result)
            save_json(state_path, state)
            print(job['id']+': '+state['status']+' '+str(attempt/'image.png'))
            if not result['ok']:
                raise RuntimeError('Output rejected; inspect verification.json. No blind retry.')
        verify_case(manifest_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('prepare', 'all'):
        p = sub.add_parser(command)
        p.add_argument('case', type=Path); p.add_argument('output', type=Path)
    p = sub.add_parser('run'); p.add_argument('manifest', type=Path)
    p.add_argument('--repeat', action='store_true', help='Export again; never repeat initialization toggles')
    p = sub.add_parser('verify'); p.add_argument('manifest', type=Path)
    args = parser.parse_args()
    if args.command in ('prepare', 'all'):
        manifest = prepare_case(args.case, args.output)
        if args.command == 'all':
            run_case(manifest)
    elif args.command == 'run':
        run_case(args.manifest, args.repeat)
    elif not verify_case(args.manifest):
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('STOP: '+str(error), file=sys.stderr)
        raise SystemExit(1)
