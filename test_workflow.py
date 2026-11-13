import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import workflow


class WorkflowTests(unittest.TestCase):
    def test_segment_bounds(self):
        self.assertEqual(workflow.segment_values({'start': 12, 'end': 25, 'cursor': 19}), (12,25,19))
        for segment in ({'start': 0, 'end': 1, 'cursor': 2}, {'start': 1, 'end': 1, 'cursor': 1},
                        {'start': 0, 'end': float('nan'), 'cursor': 0}):
            with self.assertRaises(ValueError):
                workflow.segment_values(segment)

    def test_reserved_features_fail_explicitly(self):
        for change in ({'axis_mode':'separate'}, {'selection_mode':'auto'}, {'signals':['x','x']},
                       {'legend_width':10}, {'y_range':[10,0]}, {'paper_mm':[0,100]}, {'paper_mm':[100,200]}):
            with self.assertRaises(ValueError):
                workflow.validate_profile({'signals':['x'], **change})

    def test_repeat_has_no_toggle_or_load(self):
        job = {'mf4':'C:/常温.MF4','title':'unique','cna':'C:/layout.cna','layout':'test', 'segment':{'cursor':19}}
        repeat = workflow.make_script(job, Path('C:/result'), False, 'token')
        first = workflow.make_script(job, Path('C:/result'), True, 'token')
        self.assertNotIn('ExecuteWindowMenuCommand', repeat)
        self.assertNotIn('LoadConfiguration', repeat)
        self.assertIn('ReplaceMeasurementFile', repeat)
        self.assertEqual(first.count('CM_MEASURE_POINT'), 1)
        self.assertIn('INITIALIZED token', first)
        self.assertIn('常温.MF4', repeat.encode('gbk').decode('gbk'))

    def test_native_config_subset_and_range(self):
        import configparser
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'display.cna'
            workflow.native_config(Path('C:/测试.MF4'), ['VfADC_MainMotoCurrent_MA','DoorLatPosnINP'],
                'unique','print', {'start':20,'end':24,'cursor':22}, {'legend_width':400,'paper_mm':[297,148.5]}, [-5,9000], target)
            cfg=configparser.RawConfigParser(); cfg.optionxform=str
            cfg.read(target,encoding='gbk')
            self.assertEqual(cfg['MDF_LIST']['Count'],'2')
            self.assertFalse(cfg.has_section('MDF_OBJECT_3'))
            self.assertEqual(cfg['WINDOW_1']['XMin'],'20000')
            self.assertEqual(cfg['WINDOW_1']['LegendPixWidth'],'400')
            self.assertEqual(cfg['MDF_OBJECT_1']['ChannelName'],'VfADC_MainMotoCurrent_MA')
            self.assertNotEqual(cfg['MDF_OBJECT_1_DISPLAY_1']['Color'],cfg['MDF_OBJECT_2_DISPLAY_1']['Color'])
            self.assertEqual(cfg['PRINT_LAYOUT_0']['ShowDisplayIndex'],'0')
            self.assertEqual(cfg['PRINT_LAYOUT_0']['PaperLength'],'148.5')

    def test_background_check(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.png'
            Image.new('RGB',(400,300),'white').save(path)
            self.assertFalse(workflow.image_check(path)['ok'])
            Image.new('RGB',(400,300),'black').save(path)
            self.assertTrue(workflow.image_check(path)['ok'])
            # Uniform black passes this one heuristic, not visual/data acceptance.

    def test_missing_channel_rejected(self):
        class MissingMdf:
            def whereis(self, name):
                return ()
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
            workflow.analyze(MissingMdf(), ['missing'], {'start':0,'end':1,'cursor':0})

    def test_analysis_reuses_samples_and_checks_range(self):
        import numpy as np
        from asammdf import Signal
        class FakeMdf:
            calls = 0
            def whereis(self, name):
                return ((0,0),)
            def get(self, name, raw):
                self.calls += 1
                return Signal(samples=np.array([0.,3.,5.]), timestamps=np.array([0.,1.,2.]), name=name)
        mdf=FakeMdf(); cache={}
        for _ in range(2):
            rows, limits=workflow.analyze(mdf,['x'],{'start':0,'end':2,'cursor':1},cache)
            self.assertEqual(rows[0]['max'],5)
            self.assertLessEqual(limits[0],0)
        self.assertEqual(mdf.calls,1)
        with self.assertRaisesRegex(ValueError,'outside recorded'):
            workflow.analyze(mdf,['x'],{'start':0,'end':3,'cursor':1},cache)

    def test_uncertain_initialization_never_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); source=folder/'a.mf4'; source.write_bytes(b'test')
            cna=folder/'display.cna'; cna.write_bytes(b'test')
            manifest={'jobs':[{'id':'a','mf4':str(source),'source_sha256':workflow.fingerprint(source),
                'cna':str(cna),'cna_sha256':workflow.fingerprint(cna)}]}
            workflow.save_json(folder/'manifest.json',manifest)
            workflow.save_json(folder/'state.json',{'initialization_pending':True,'attempt':str(folder/'absent'),'token':'x'})
            with patch.object(workflow,'probe') as probe:
                with self.assertRaisesRegex(RuntimeError,'uncertain'):
                    workflow.run_case(folder/'manifest.json')
                probe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
