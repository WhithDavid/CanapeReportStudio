"""Local GUI configuration helpers. All GUI jobs are explicitly preview-only."""
import json
import math
import re
import sys
import uuid
import shutil
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent
BACKEND = APP.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND/'deps'))
from workflow import segment_values, validate_profile

RATIOS = {'2:1 横向特写': [297,148.5], '16:9 横向': [297,167.0625],
          '4:3': [280,210], 'A4 横向': [297,210]}
FUNCTIONS = {'手动预览（14信号样例）':'power_cycle', '防夹力':'antipinch',
             '手动开关门力':'manual_force', '悬停角度差':'holding', '恶意摔门':'slam',
             '空调开启下电动开关门':'hvac', '平顺性 / 开关门时间':'smoothness'}
DEFAULTS = {'mf4':'', 'project':'', 'door':'DPOD',
    'temperature_label':'待确认','temperature_value':'','slope_direction':'待确认',
    'slope_value':'','slope_unit':'待确认', 'function':'手动预览（14信号样例）',
    'start':'0','end':'10','cursor':'5','ratio':'2:1 横向特写','legend_width':'480',
    'save_directory':'','preview_mode':'公共 Axis 1'}

PRESET_KEYS=('door','temperature_label','temperature_value','slope_direction',
             'slope_value','slope_unit','ratio','legend_width','save_directory','preview_mode','signals')

def preset_settings(settings):
    return {k:settings.get(k,[] if k=='signals' else DEFAULTS[k]) for k in PRESET_KEYS}

def preset_path(name):
    if not re.fullmatch(r'[\w\-\u4e00-\u9fff %℃°]{1,100}',name) or name in ('.','..') or name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(10)],*[f'LPT{i}' for i in range(10)]}:
        raise ValueError('模板名仅允许中文、字母、数字、空格、下划线、短横线和工况单位，最多100字')
    return APP/'presets'/(name+'.json')

def archive_preset(name):
    source=preset_path(name)
    archive=source.parent/'archive';archive.mkdir(exist_ok=True)
    target=archive/(source.stem+'_'+uuid.uuid4().hex[:8]+'.json')
    source.rename(target)
    return target


def condition_filename(settings):
    def clean(value):
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]','-',str(value)).strip(' .')[:50] or '待确认'
    temperature=str(settings.get('temperature_value','')).strip()
    slope=str(settings.get('slope_value','')).strip()
    unit={'degree':'度','percent':'%'}.get(settings.get('slope_unit'),'单位待确认')
    parts=[settings.get('door','待确认'),settings.get('temperature_label','待确认'),
           temperature+'℃' if temperature else '温度待确认',
           settings.get('slope_direction','待确认'),slope+unit if slope else '坡度待确认']
    return '_'.join(clean(p) for p in parts)+'.png'


def save_image_copy(source,directory,filename):
    folder=Path(directory)
    if not folder.is_dir():raise ValueError('图片保存目录不存在，请重新选择')
    if Path(filename).name!=filename or not filename.endswith('.png'):
        raise ValueError('图片文件名无效')
    # Exclusive creation prevents overwriting even when another export races us.
    for index in range(10000):
        target=folder/(filename if index==0 else f'{Path(filename).stem}_{index:03d}.png')
        try:output=target.open('xb')
        except FileExistsError:continue
        try:
            with output,Path(source).open('rb') as incoming:shutil.copyfileobj(incoming,output)
        except Exception:
            target.unlink(missing_ok=True);raise
        return target
    raise ValueError('同名文件过多，请更换目录')


def profiles():
    return json.loads((BACKEND/'profiles.json').read_text(encoding='utf-8'))


def default_settings():
    return {**DEFAULTS,'signals':list(profiles()['power_cycle']['signals'])}


def optional_number(value, name):
    if not str(value).strip():
        return None
    result=float(value)
    if not math.isfinite(result):
        raise ValueError(name+'必须是有限数值')
    return result


def build_case(settings):
    source=Path(settings['mf4']).expanduser().resolve()
    if not source.is_file() or source.suffix.lower()!='.mf4':
        raise ValueError('请选择存在的 MF4 文件')
    project=Path(settings['project']).expanduser().resolve()
    if not project.is_dir():
        raise ValueError('CANape 工程目录不存在')
    segment={'id':'detail', **{k:float(settings[k]) for k in ('start','end','cursor')}}
    segment_values(segment)
    if settings['function'] not in FUNCTIONS or settings['ratio'] not in RATIOS:
        raise ValueError('未知功能或图片比例')
    if settings['door'] not in ('DPOD','PPOD','LPOD','RPOD'):
        raise ValueError('请选择车门')
    if settings['slope_unit'] not in ('待确认','degree','percent') or settings['slope_direction'] not in ('待确认','平地','上坡','下坡','左倾','右倾'):
        raise ValueError('坡向或单位无效')
    slope=optional_number(settings['slope_value'],'坡度')
    temperature=optional_number(settings['temperature_value'],'温度')
    if slope is not None:
        if slope<0 or (settings['slope_unit']=='degree' and slope>=90):
            raise ValueError('坡度应非负，角度应小于90°')
        if settings['slope_direction']=='平地' and slope!=0:
            raise ValueError('平地的坡度应为0')
    names=settings.get('signals',[])
    if not isinstance(names,list):
        raise ValueError('观测量列表无效')
    display={'signals':names,'axis_mode':'common','y_range':'auto',
        'paper_mm':RATIOS[settings['ratio']], 'legend_width':int(settings['legend_width']),
        'require_black_background':True}
    validate_profile(display)
    # A manually selected function is a label, not approval of its pending report specification.
    condition={'door':settings['door'],'temperature':settings['temperature_label'],
        'temperature_value_c':temperature,'slope_value':slope,'slope_unit':settings['slope_unit'],
        'slope_direction':settings['slope_direction'],'confirmation':'preview_only'}
    return {'purpose':'preview','project':str(project),
        'probe':str(BACKEND/'tools/bin/Asap3ReadOnlyProbe.exe'),
        'jobs':[{'id':'gui_preview','mf4':str(source),'profile':'power_cycle',
            'requested_function':FUNCTIONS[settings['function']], 'condition':condition,
            'display':display,'segments':[segment],
            'report_slot':'界面手动预览；未匹配正式报告条目'}]}


def load_channels(path):
    from asammdf import MDF
    source=Path(path)
    if not source.is_file() or source.suffix.lower()!='.mf4':
        raise ValueError('请选择 MF4 文件')
    with MDF(source) as mdf:
        # channels_db also includes source-prefixed aliases; show actual stored
        # names only, so one measured channel is not offered twice.
        return sorted({channel.name for group in mdf.groups for channel in group.channels},
                      key=str.casefold)


def save_preset(name, settings):
    folder=APP/'presets';folder.mkdir(exist_ok=True)
    path=preset_path(name)
    payload={'schema_version':2,'purpose':'preview','settings':preset_settings(settings)}
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)
    return path


def load_preset(path):
    value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if value.get('schema_version') not in (1,2) or value.get('purpose')!='preview' or not isinstance(value.get('settings'),dict):
        raise ValueError('不是本软件的预览模板')
    settings=preset_settings(value['settings'])
    settings['signals']=value['settings'].get('signals',[])
    if not isinstance(settings['signals'],list) or any(not isinstance(n,str) for n in settings['signals']):
        raise ValueError('模板观测量格式错误')
    return settings


def prepare_run(case):
    folder=APP/'runs'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True,exist_ok=False)
    case_path=folder/'case.json'
    case_path.write_text(json.dumps(case,ensure_ascii=False,indent=2),encoding='utf-8')
    return folder, case_path, folder/'output'
