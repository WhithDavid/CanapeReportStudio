import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import model


class ConfigTests(unittest.TestCase):
    def test_condition_name_and_no_overwrite(self):
        settings={**model.default_settings(),'temperature_label':'常温','temperature_value':'25',
                  'slope_direction':'平地','slope_value':'0','slope_unit':'degree'}
        name=model.condition_filename(settings)
        self.assertEqual(name,'DPOD_常温_25℃_平地_0度.png')
        first=model.save_image_copy(self.source,self.folder,name)
        second=model.save_image_copy(self.source,self.folder,name)
        self.assertNotEqual(first,second)
        self.assertEqual(first.read_bytes(),b'test')
        self.assertEqual(second.read_bytes(),b'test')
        self.assertIn('待确认',model.condition_filename(model.default_settings()))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.source=self.folder/'中文工况.mf4'
        self.source.write_bytes(b'test')
        self.settings={**model.default_settings(),'mf4':str(self.source),'project':str(self.folder)}

    def test_preview_and_default_style(self):
        case=model.build_case(self.settings)
        self.assertEqual(case['purpose'],'preview')
        job=case['jobs'][0]
        self.assertEqual(len(job['display']['signals']),14)
        self.assertEqual(job['display']['axis_mode'],'common')
        self.assertEqual(job['display']['paper_mm'],[297,148.5])
        self.assertNotIn('report_item_id',job)
        self.assertNotIn('temperature_c',job['condition'])

    def test_pending_function_never_becomes_formal(self):
        case=model.build_case({**self.settings,'function':'防夹力','signals':['my_signal']})
        self.assertEqual(case['purpose'],'preview')
        self.assertEqual(case['jobs'][0]['requested_function'],'antipinch')
        self.assertEqual(case['jobs'][0]['display']['signals'],['my_signal'])

    def test_invalid_settings(self):
        for change in ({'signals':[]},{'signals':['a','a']},{'cursor':'11'},
                       {'end':'nan'},{'legend_width':'1'},{'mf4':'missing.mf4'},
                       {'slope_direction':'平地','slope_value':'5'},
                       {'temperature_value':'inf'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                model.build_case({**self.settings,**change})

    def test_preset_roundtrip_and_path_safety(self):
        with patch.object(model,'APP',self.folder):
            path=model.save_preset('左前门 常温',self.settings)
            self.assertEqual(model.load_preset(path),model.preset_settings(self.settings))
            payload=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(payload['schema_version'],2)
            for key in ('mf4','start','end','cursor','function'):
                self.assertNotIn(key,payload['settings'])
            with self.assertRaises(ValueError):model.save_preset('../escape',self.settings)

    def test_legacy_does_not_restore_file_or_time(self):
        path=self.folder/'legacy.json'
        path.write_text(json.dumps({'schema_version':1,'purpose':'preview','settings':self.settings}),encoding='utf-8')
        loaded=model.load_preset(path)
        current={**self.settings,'mf4':'new.mf4','start':'71','end':'100','cursor':'85'}
        merged={**current,**loaded}
        for key in ('mf4','start','end','cursor'):self.assertEqual(merged[key],current[key])

    def test_archive_recoverable(self):
        with patch.object(model,'APP',self.folder):
            path=model.save_preset('test',self.settings)
            original=path.read_bytes();archived=model.archive_preset('test')
            self.assertFalse(path.exists());self.assertEqual(archived.read_bytes(),original)

    def test_unique_runs(self):
        with patch.object(model,'APP',self.folder):
            case=model.build_case(self.settings)
            a,case_path,out=model.prepare_run(case)
            b,_,_=model.prepare_run(case)
            self.assertNotEqual(a,b)
            self.assertFalse(out.exists())
            self.assertEqual(json.loads(case_path.read_text(encoding='utf-8')),case)


if __name__=='__main__':unittest.main()
