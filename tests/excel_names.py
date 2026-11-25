"""Excel application naming contract through the real data-format pipeline.

Synthetic electrical notes, no model, no existing user-library writes.
"""
from pathlib import Path
import copy
import json
import shutil
import sys
import time
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.excel_names import sheet_name, validate_exported_names
from app.storage import Store, digest
from app.materials import Materials
from app.registry import Registry
from app.formats import FormatGateway
from app.data_workbooks import DataWorkbooks
import openpyxl


class ExcelExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = ROOT / 'qa/work' / ('excel-export-' + str(time.time_ns()))
        cls.store = Store(cls.work)
        materials = Materials(cls.store)
        old_directory = cls.work / 'old-packages'
        shutil.copytree(ROOT / 'formats/data-1.1.0', old_directory / 'data')
        old_registry = Registry(old_directory, cls.store.authority / 'packages')
        old_gateway = FormatGateway(cls.store, old_registry)
        service = DataWorkbooks(cls.store, old_gateway, materials)
        cls.raw = '电压1.00V\n电压2.00V\n'
        job = materials.queue('伏安测量_自编机制样本.txt', cls.raw.encode('utf-8'))
        deadline = time.monotonic() + 20
        while True:
            row = cls.store.one('SELECT status,result,error FROM jobs WHERE id=?', (job['id'],))
            if row['status'] not in ('queued', 'running'):
                break
            if time.monotonic() >= deadline:
                raise AssertionError('Synthetic import did not finish')
            time.sleep(.02)
        assert row['status'] == 'succeeded', row
        source = json.loads(row['result'])['source_id']
        cls.body = {'title': '自编伏安记录', 'records': [
            {'id': 'u1', 'section': '伏安法：电压／对照', 'quantity': '电压',
             'values': ['1.00'], 'unit': 'V', 'status': 'recorded',
             'source_line': 1, 'source_quote': '电压1.00V', 'note': ''},
            {'id': 'u2', 'section': '伏安法:电压/对照', 'quantity': '电压',
             'values': ['2.00'], 'unit': 'V', 'status': 'recorded',
             'source_line': 2, 'source_quote': '电压2.00V', 'note': ''}],
            'observations': [], 'issues': []}
        cls.old = service.submit(source, copy.deepcopy(cls.body))
        cls.old_bytes = cls.asset(cls.old, 'data.xlsx').read_bytes()
        # This regression exercises the released full-width name fix (1.1.1).
        # Keep its explicit upgrade pair even when newer data packs are shipped.
        upgrade_packages = cls.work / 'upgrade-packages'
        shutil.copytree(ROOT / 'formats/data-1.1.1', upgrade_packages / 'data')
        cls.registry = Registry(upgrade_packages, cls.store.authority / 'packages')
        cls.gateway = FormatGateway(cls.store, cls.registry)
        plan = cls.gateway.plan_upgrade(cls.old['id'])
        assert plan['can_upgrade'] and plan['target_version'] == '1.1.1', plan
        cls.new = cls.gateway.upgrade(cls.old['id'], plan)

    @classmethod
    def asset(cls, obj, name):
        return cls.store.asset_path(next(a['path'] for a in obj['assets'] if a['name'] == name))

    def test_real_format_upgrade_keeps_source_and_fixed_old_assets(self):
        self.assertEqual(self.old['pack_version'], '1.1.0')
        self.assertEqual(self.new['pack_version'], '1.1.1')
        self.assertEqual(self.new['revision'], self.old['revision'] + 1)
        self.assertEqual(self.new['content'], self.old['content'])
        self.assertEqual(self.asset(self.old, 'data.xlsx').read_bytes(), self.old_bytes)
        self.assertEqual(self.gateway.read(self.old['id'], self.old['revision'])['pack_digest'], self.old['pack_digest'])

    def test_exported_labels_and_cells_preserve_meaning(self):
        path = self.asset(self.new, 'data.xlsx')
        names = validate_exported_names(path)
        self.assertEqual(names[:2], ['伏安法_电压_对照', '伏安法_电压_对照（2）'])
        book = openpyxl.load_workbook(path)
        try:
            self.assertEqual(book[names[0]]['C2'].value, '1.00')
            self.assertEqual(book[names[0]]['D2'].value, 1)
            self.assertEqual(book[names[1]]['C2'].value, '2.00')
            self.assertEqual(book['原始记录']['B2'].value, '电压1.00V')
            self.assertEqual(book[names[0]]['G2'].hyperlink.target, "#'原始记录'!A2")
        finally:
            book.close()
        content = json.loads(self.asset(self.new, 'normalized.json').read_text(encoding='utf-8'))
        self.assertEqual(content['records'][0]['section'], '伏安法：电压／对照')
        self.assertEqual(content['source_sha'], digest(self.raw.encode('utf-8')))

    def test_output_validation_catches_a_file_the_producer_reader_accepts(self):
        bad = self.work / 'invalid-label.xlsx'
        with ZipFile(self.asset(self.new, 'data.xlsx')) as source, ZipFile(bad, 'w', ZIP_DEFLATED) as output:
            for member in source.infolist():
                data = source.read(member.filename)
                if member.filename == 'xl/workbook.xml':
                    data = data.replace('伏安法_电压_对照'.encode('utf-8'), '伏安法：电压／对照'.encode('utf-8'))
                output.writestr(member, data)
        loaded = openpyxl.load_workbook(bad)
        loaded.close()
        with self.assertRaisesRegex(ValueError, 'Excel工作表标签不兼容'):
            validate_exported_names(bad)

    def test_documented_label_constraints_keep_other_unicode(self):
        used = set()
        label = sheet_name("＇λ²（电阻）：［样品］＼／？＊＇", used)
        self.assertEqual(label, 'λ²（电阻）__样品_____')
        long = sheet_name('🧲' * 20, set())
        self.assertLessEqual(len(long.encode('utf-16-le')) // 2, 31)
        self.assertEqual(long, '🧲' * 15)
        self.assertEqual(sheet_name('History', set()), 'History（2）')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ExcelExport)
    start = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    (ROOT / 'qa/excel-export-regression-0.6.2.json').write_text(json.dumps({
        'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'seconds': time.monotonic() - start, 'scope': 'synthetic electrical notes; real format upgrade and output checks; no live model or Excel UI; not full acceptance'
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    sys.exit(not result.wasSuccessful())
