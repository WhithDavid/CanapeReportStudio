import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from xml.sax.saxutils import escape
import report_catalog as catalog
import workflow


class ReportCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.source=self.root/'report.docx'
        texts=['DPOD车门运行平顺性测试数据：(25℃平地)',
               'DPOD车门防夹力测试数据：(-40℃上坡30%)']
        xml='<w:document xmlns:w="'+catalog.NS['w']+'"><w:body><w:tbl><w:tr><w:tc>'
        xml+='<w:p><w:r><w:t>25℃平地</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        xml+=''.join('<w:p><w:r><w:t>'+escape(t)+'</w:t></w:r></w:p>' for t in texts)
        xml+='</w:body></w:document>'
        with ZipFile(self.source,'w') as archive:
            archive.writestr('word/document.xml',xml)
        report,items=catalog.extract_report({'door':'DPOD','path':str(self.source)})
        self.data={'reports':[report],'items':items}
        self.item=items[0]
        self.profile={'status':'confirmed','function_id':'smoothness','confirmation_note':'test specification'}
        self.job={'id':'one','report_item_id':self.item['item_id'],'condition':self.item['condition']}

    def test_inventory_preserves_conflict_and_original(self):
        self.assertEqual(len(self.data['items']),2)
        self.assertEqual(self.data['items'][1]['condition']['slope']['value'],30)
        self.assertIn('condition_not_found_verbatim_in_tables',self.data['items'][1]['mapping_issues'])
        self.assertEqual(self.data['reports'][0]['sha256'],workflow.fingerprint(self.source))

    def test_degree_percent_never_equal(self):
        value=json.loads(json.dumps(self.job))
        value['condition']['slope']={'direction':'uphill','unit':'degree','value':3}
        with self.assertRaisesRegex(ValueError,'condition mismatch'):
            catalog.validate_mapping(value,self.profile,self.data)

    def test_missing_temperature_or_direction_rejected(self):
        for condition in ({'door':'DPOD','temperature':'常温'},
            {'door':'DPOD','temperature_c':25,'slope':{'value':3,'unit':'degree','direction':'待确认'}}):
            with self.assertRaises(ValueError):
                catalog.condition_key(condition)

    def test_confirmed_mapping_and_source_change(self):
        self.assertEqual(catalog.validate_mapping(self.job,self.profile,self.data),self.item)
        with self.source.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError,'Source report changed'):
            catalog.validate_mapping(self.job,self.profile,self.data)

    def test_sample_cannot_fill_report(self):
        with self.assertRaisesRegex(ValueError,'Confirmed function-specific'):
            catalog.validate_mapping(self.job,{'status':'sample_only'},self.data)

    def test_pending_profile_cannot_be_overridden(self):
        case={'purpose':'preview','jobs':[{'id':'one','profile':'smoothness',
            'display':{'status':'confirmed','signals':['x']}}]}
        path=self.root/'case.json'; path.write_text(json.dumps(case))
        with self.assertRaisesRegex(ValueError,'pending user specification'):
            workflow.prepare_case(path,self.root/'out')
        self.assertFalse((self.root/'out').exists())

    def test_coverage_not_a_test_verdict(self):
        result=catalog.coverage(self.data,[],{})
        self.assertEqual(result['blocker_counts']['NO_MAPPED_JOB'],2)
        self.assertEqual(result['blocker_counts']['FUNCTION_SPEC_PENDING'],2)
        self.assertEqual(result['items'][0]['export_status'],'NOT_ASSESSED')

    def test_report_preflight_before_large_file_parse(self):
        path=self.root/'case.json'
        path.write_text(json.dumps({'purpose':'report','jobs':[]}))
        with self.assertRaisesRegex(ValueError,'require catalog'):
            workflow.prepare_case(path,self.root/'out')
        self.assertFalse((self.root/'out').exists())

    def test_preview_does_not_cover_formal_item(self):
        job={**self.job,'profile':'smoothness'}
        result=catalog.coverage(self.data,[{'purpose':'preview','jobs':[job]}],{'smoothness':self.profile})
        self.assertEqual(result['blocker_counts']['NO_MAPPED_JOB'],2)
        self.assertEqual(result['unmapped_case_jobs'],['one'])

    def test_real_mf4_format_prepare_with_confirmed_fixture(self):
        # Synthetic samples test format/API compatibility, not calibration results.
        import numpy as np
        from asammdf import MDF, Signal
        mf4=self.root/'synthetic.MF4'
        with MDF(version='4.10') as mdf:
            mdf.append([Signal(samples=np.array([1.,2.,3.,4.]),timestamps=np.array([0.,1.,2.,3.]),name='DoorLatPosnINP')])
            mdf.save(mf4,overwrite=True)
        catalog_path=self.root/'catalog.json'; catalog_path.write_text(json.dumps(self.data))
        job={**self.job,'profile':'fixture','mf4':str(mf4),'segments':[{'id':'detail','start':0,'end':3,'cursor':1}]}
        case={'purpose':'report','catalog':str(catalog_path),'project':'test','probe':'test','jobs':[job]}
        case_path=self.root/'case.json'; case_path.write_text(json.dumps(case))
        profile={**self.profile,'signals':['DoorLatPosnINP'],'axis_mode':'common'}
        original_read=workflow.read_json
        def fixture_read(path):
            return {'fixture':profile} if Path(path)==workflow.ROOT/'profiles.json' else original_read(path)
        with patch.object(workflow,'read_json',side_effect=fixture_read), patch.object(workflow,'probe') as probe:
            manifest_path=workflow.prepare_case(case_path,self.root/'out')
            probe.assert_not_called()
        manifest=original_read(manifest_path)
        self.assertEqual(manifest['purpose'],'report')
        self.assertEqual(manifest['jobs'][0]['report_item_id'],self.item['item_id'])
        self.assertEqual(manifest['jobs'][0]['analysis'][0]['max'],4)


if __name__=='__main__':
    unittest.main()
