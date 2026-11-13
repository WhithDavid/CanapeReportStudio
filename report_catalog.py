"""Read-only DOCX anchor inventory and strict report-job mapping; no Word edits."""
import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from prepare import fingerprint

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
FUNCTIONS = {'防夹力':'antipinch', '手动推力':'manual_force', '悬停角度差':'holding',
             '恶意摔门':'slam', '电动开关门':'hvac', '运行平顺性':'smoothness'}
DIRECTIONS = {'平地':'flat', '上坡':'uphill', '下坡':'downhill', '左倾':'left', '右倾':'right'}
ANCHOR = re.compile(r'^(DPOD|PPOD|LPOD|RPOD)车门(.+?)测试数据[:：][(（]([+-]?\d+)℃(平地|上坡|下坡|左倾|右倾)(?:(\d+(?:\.\d+)?)%)?[)）]$')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def condition_key(condition):
    """No automatic unit conversion, room-temperature inference, or slope rounding."""
    door = condition.get('door')
    temperature = condition.get('temperature_c')
    slope = condition.get('slope', {})
    if door not in ('DPOD','PPOD','LPOD','RPOD'):
        raise ValueError('condition.door required')
    if type(temperature) not in (int,float) or not math.isfinite(temperature):
        raise ValueError('Numeric temperature_c required; room temperature is not inferred')
    value, unit, direction = slope.get('value'), slope.get('unit'), slope.get('direction')
    if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
        raise ValueError('Explicit nonnegative slope.value required')
    if unit not in ('percent','degree') or direction not in DIRECTIONS.values():
        raise ValueError('Explicit slope.unit and direction required')
    if (direction == 'flat') != (value == 0) or (unit == 'degree' and value >= 90):
        raise ValueError('Inconsistent slope direction/value')
    return door, float(temperature), direction, unit, float(value)


def extract_report(source):
    path = Path(source['path']).resolve()
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    paragraphs = [''.join(t.text or '' for t in p.findall('.//w:t',NS)) for p in root.findall('.//w:p',NS)]
    table_text = {re.sub(r'\s+','', ''.join(t.text or '' for t in p.findall('.//w:t',NS)))
                  for p in root.findall('.//w:tbl//w:p',NS)}
    items, warnings, seen = [], [], set()
    for number, raw in enumerate(paragraphs, 1):
        if '测试数据' not in raw:
            # Known textual inconsistency from these reports, not an automatic correction.
            if re.match(r'^\+?50℃[，,]右倾[，,]20%$', re.sub(r'\s+','',raw)):
                warnings.append({'paragraph':number, 'raw':raw, 'issue':'50C_in_high_temperature_table_needs_confirmation'})
            continue
        match = ANCHOR.fullmatch(raw.strip())
        if not match:
            warnings.append({'paragraph':number,'raw':raw,'issue':'unparsed_data_anchor'})
            continue
        door, function_label, temperature, direction, value = match.groups()
        issues = []
        if function_label == '运行平顺性力':
            function = 'smoothness'
            issues.append('function_label_typo_needs_confirmation')
        else:
            function = FUNCTIONS.get(function_label)
        if not function:
            warnings.append({'paragraph':number,'raw':raw,'issue':'unknown_function'}); continue
        condition = {'door':door, 'temperature_c':int(temperature),
            'slope':{'direction':DIRECTIONS[direction], 'unit':'percent', 'value':float(value or 0)}}
        condition_key(condition)
        if door != source['door']:
            issues.append('door_mismatch')
        if direction != '平地' and value is None:
            issues.append('missing_slope_value')
        label = f'{int(temperature)}℃{direction}'+(f'{float(value):g}%' if value else '')
        if label not in table_text:
            issues.append('condition_not_found_verbatim_in_tables')
        key = f'{door}.{function}.T{int(temperature)}.{DIRECTIONS[direction]}.percent{float(value or 0):g}'
        if key in seen:
            issues.append('duplicate_anchor')
            for previous in items:
                if previous['item_id'] == key and 'duplicate_anchor' not in previous['mapping_issues']:
                    previous['mapping_issues'].append('duplicate_anchor')
            key += f'.p{number}'
        seen.add(key)
        items.append({'item_id':key, 'report_door':source['door'], 'function_id':function,
            'function_label_raw':function_label, 'condition':condition, 'anchor_text':raw,
            'paragraph_index_1based':number, 'mapping_issues':issues,
            'required_image_count':None, 'spec_status':'pending_user_spec'})
    if not items:
        raise ValueError(f'No data anchors found: {path}')
    return {'door':source['door'],'path':str(path),'sha256':fingerprint(path),
            'anchor_count':len(items),'warnings':warnings}, items


def build(sources, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Do not overwrite an existing catalog; use a new version')
    catalog = {'schema_version':1,'reports':[],'items':[],
               'note':'Data-caption inventory only; not a test verdict, page locator, or promised image count.'}
    for source in read(sources)['reports']:
        report, items = extract_report(source)
        catalog['reports'].append(report); catalog['items'].extend(items)
    ids = [item['item_id'] for item in catalog['items']]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate item IDs across reports')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:
        json.dump(catalog,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'items':len(ids),'mapping_pending':sum(bool(i['mapping_issues']) for i in catalog['items']),
        'report_warnings':sum(len(r['warnings']) for r in catalog['reports'])},ensure_ascii=False))


def validate_mapping(job, profile, catalog):
    matches = [i for i in catalog['items'] if i['item_id'] == job.get('report_item_id')]
    if len(matches) != 1:
        raise ValueError('One exact report_item_id is required')
    item = matches[0]
    if item['mapping_issues']:
        raise ValueError('Report anchor needs confirmation: '+','.join(item['mapping_issues']))
    if condition_key(job.get('condition',{})) != condition_key(item['condition']):
        raise ValueError('Report condition mismatch (temperature/direction/unit/value); no implicit conversion')
    if profile.get('function_id') != item['function_id'] or profile.get('status') != 'confirmed':
        raise ValueError('Confirmed function-specific profile required; sample profile cannot fill report')
    if not profile.get('confirmation_note'):
        raise ValueError('Profile requires user specification reference in confirmation_note')
    report = next(r for r in catalog['reports'] if r['door'] == item['report_door'])
    if fingerprint(Path(report['path'])) != report['sha256']:
        raise ValueError('Source report changed; rebuild catalog and reconfirm mapping')
    return item


def coverage(catalog, cases, profiles):
    """Missing means not supplied/mapped to this workflow, not absent from the vehicle test."""
    indexed, unmatched = {}, []
    known = {i['item_id'] for i in catalog['items']}
    for case in cases:
        for job in case['jobs']:
            key = job.get('report_item_id')
            if case.get('purpose') != 'report' or key not in known:
                unmatched.append(job['id']); continue
            indexed.setdefault(key,[]).append(job)
    rows = []
    for item in catalog['items']:
        jobs = indexed.get(item['item_id'],[])
        blockers = []
        if item['mapping_issues']:
            blockers.append('REPORT_MAPPING_NEEDS_CONFIRMATION')
        if not jobs:
            blockers.append('NO_MAPPED_JOB')
        if not jobs or any(not Path(j.get('mf4','')).is_file() or Path(j.get('mf4','')).suffix.lower() != '.mf4' for j in jobs):
            blockers.append('NO_AVAILABLE_MF4_MAPPING')
        if not jobs or any(profiles.get(j.get('profile'),{}).get('status') != 'confirmed' for j in jobs):
            blockers.append('FUNCTION_SPEC_PENDING')
        for job in jobs:
            try:
                validate_mapping(job,profiles.get(job.get('profile'),{}),catalog)
            except (ValueError, OSError, StopIteration) as error:
                blockers.append('INVALID_JOB_MAPPING: '+str(error))
        rows.append({'item_id':item['item_id'],'blockers':list(dict.fromkeys(blockers)),
                     'export_status':'NOT_ASSESSED','review_status':'NOT_ASSESSED'})
    return {'item_count':len(rows),'unmapped_case_jobs':unmatched,
            'blocker_counts':dict(Counter(b.split(':')[0] for row in rows for b in row['blockers'])), 'items':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('build');p.add_argument('sources');p.add_argument('output')
    p=sub.add_parser('status');p.add_argument('catalog');p.add_argument('--cases',nargs='*',default=[])
    p.add_argument('--profiles',default=str(Path(__file__).with_name('profiles.json')));p.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.command=='build':
        build(args.sources,args.output)
    else:
        result=coverage(read(args.catalog),[read(p) for p in args.cases],read(args.profiles))
        target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='items'},ensure_ascii=False))


if __name__=='__main__':
    main()
