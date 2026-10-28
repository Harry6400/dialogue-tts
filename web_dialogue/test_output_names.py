"""Fictional name and real filesystem regressions; no speech-service requests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from .app import normalize_source
from .batch_import import preview_batch
from .output_names import pair_filename, valid_output_name
from .normalize import InputError
from .test_batch_import import f, xml
from . import test_native_folders as folder_tests


class NameTests(unittest.TestCase):
    def files(self, prefix='00090071992547409931234567890', ref='fictional', reverse_roles=False):
        return [f(xml(role,ref),prefix+suffix+'.XML') for role,suffix in
                (('USER','0'),('AGENT','1')) if reverse_roles] or [f(xml('AGENT',ref),prefix+'0.xml'),f(xml('USER',ref),prefix+'1.xml')]

    def test_long_strings_leading_zeros_reverse_files_and_roles(self):
        files=self.files(reverse_roles=True)
        first=normalize_source({'format':'xml_pair','xml_files':files})
        files.reverse()
        second=normalize_source({'format':'xml_pair','xml_files':files})
        self.assertEqual(first['filename'],'00090071992547409931234567890.mp3')
        self.assertEqual(first['filename'],second['filename'])
        self.assertEqual(first['fingerprint'],second['fingerprint'])
        self.assertEqual(first['rows'][0]['speaker'],'AGENT')
        changed=copy.deepcopy(files)
        for item in changed:item['name']='7'+item['name']
        renamed=normalize_source({'format':'xml_pair','xml_files':changed})
        self.assertNotEqual(first['filename'],renamed['filename'])
        self.assertEqual(first['fingerprint'],renamed['fingerprint'])

    def test_names_are_ascii_basenames_with_two_distinct_endings(self):
        for names in [('0.xml','1.xml'),('10.xml','21.xml'),('10.xml','10.xml'),
                      ('a0.xml','a1.xml'),('../10.xml','11.xml'),('１０.xml','１１.xml'),
                      ('10.xml.exe','11.xml'),('1 0.xml','1 1.xml')]:
            with self.subTest(names=names),self.assertRaises(ValueError):
                pair_filename([{'name':n} for n in names])
        self.assertEqual(pair_filename([{'name':'00.xml'},{'name':'01.XML'}]),'0.mp3')
        self.assertFalse(valid_output_name('../001.mp3'))
        self.assertFalse(valid_output_name('1'*252+'.mp3'))

    def test_xml_identity_is_checked_before_names(self):
        files=[f(xml('AGENT','first'),'invalid.xml'),f(xml('USER','second'),'invalid.xml')]
        with self.assertRaisesRegex(InputError,'对话标识不一致'):
            normalize_source({'format':'xml_pair','xml_files':files})
        result=preview_batch({'files':files})
        self.assertTrue(all(g['error_code']=='missing_track' for g in result['groups']))

    def test_duplicate_and_invalid_groups_skip_without_blocking_other_calls(self):
        files=self.files('123','first')+self.files('123','second')+self.files('456','third')+self.files('789','fourth')
        files[-1]['name']='invalid.xml'
        result=preview_batch({'files':files})
        self.assertEqual(result['counts'],{'ready':1,'error':3})
        self.assertEqual([g.get('error_code') for g in result['groups']],['duplicate_filename','duplicate_filename',None,'invalid_filename'])
        self.assertEqual(result['groups'][2]['filename'],'456.mp3')


class SaveNameTests(unittest.TestCase):
    setUp = folder_tests.FolderTests.setUp
    tearDown = folder_tests.FolderTests.tearDown
    response = folder_tests.FolderTests.response
    save = folder_tests.FolderTests.save
    def test_old_generic_receipt_does_not_complete_new_name(self):
        audio=(Path(__file__).parent/'silence.mp3').read_bytes()
        (self.output/'dialogue.mp3').write_bytes(audio)
        record={'name':'dialogue.mp3','fingerprint':'a'*64,'size':len(audio),'sha256':hashlib.sha256(audio).hexdigest()}
        # A genuine pre-ledger preference snapshot is imported by a fresh host.
        # A generic old result must remain verifiable without completing a new
        # fixed numeric target, even when both use the same audio fingerprint.
        (self.root/'prefs.json').write_text(json.dumps({'version':1,'paths':{},'voices':{},
            'receipts':{str(self.output):{'a'*64:record}},'exports':{}}))
        self.host=self.new_host()
        self.out=self.host._authorize(self.output,'output')['folder_id']
        self.assertEqual(len(self.host.check_completed(self.out,['a'*64])['completed']),1)
        checked=self.host.check_completed(self.out,['a'*64],{'a'*64:'001.mp3'})
        self.assertEqual(checked['completed'],[]);self.assertEqual(checked['conflicts'],[])
        self.assertTrue((self.output/'dialogue.mp3').exists())

    def test_conflicting_fingerprint_or_symlink_is_never_replaced(self):
        self.assertTrue(self.save()['ok'])
        before=(self.output/'001.mp3').read_bytes()
        self.assertFalse(self.save('/api/jobs/'+'c'*32+'/audio','b'*64)['ok'])
        self.assertEqual((self.output/'001.mp3').read_bytes(),before)
        self.assertEqual(len(list(self.output.glob('*.mp3'))),1)
        checked=self.host.check_completed(self.out,['b'*64],{'b'*64:'001.mp3'})
        self.assertEqual(len(checked['conflicts']),1)

    def test_save_race_never_overwrites_competing_file(self):
        target=self.output/'001.mp3'
        def compete(*args,**kwargs):
            target.write_bytes(b'competing writer')
            raise FileExistsError('race')
        publisher='native_windows_folders.os.rename' if os.name=='nt' else 'native_windows_folders.os.link'
        with patch(publisher,side_effect=compete):
            result=self.save()
        self.assertFalse(result['ok']);self.assertTrue(result['conflict'])
        self.assertEqual(target.read_bytes(),b'competing writer')
        self.assertEqual(list(self.output.iterdir()),[target])
