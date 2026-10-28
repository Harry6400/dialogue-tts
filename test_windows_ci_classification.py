"""CI outcome semantics: an OS confirmation gate is never end-to-end success."""
import copy,unittest,tempfile
from unittest.mock import patch
from pathlib import Path
from tools.windows_upgrade_smoke import expected_security_gate, wait_file, confirm_owned_prompt

class ClassificationTests(unittest.TestCase):
    def test_receipt_sharing_violation_retries_without_extending_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt=Path(directory)/'receipt.json';receipt.write_text('{}')
            with patch.object(Path,'read_text',side_effect=[PermissionError('sharing violation'),'{"state":"ready","pid":123}']),patch('tools.windows_upgrade_smoke.time.sleep'):
                self.assertEqual(wait_file(receipt,1),{'state':'ready','pid':123})
            with patch.object(Path,'read_text',side_effect=PermissionError('still locked')),patch('tools.windows_upgrade_smoke.time.sleep'),patch('tools.windows_upgrade_smoke.time.monotonic',side_effect=[0,.1,2]):
                with self.assertRaises(TimeoutError):wait_file(receipt,1)
    def fixture(self):
        return ({'cases':{key:True for key in ('bad_hash_preserves_old','waits_for_old_pid','cancel_no_late_install','preferences_receipts_cache_preserved','old_backup_retained')},'owned_prompt_windows':[{'title':'Open File - Security Warning','class':'#32770','verified_target_binding':'exact-plan-helper-hashes-and-unique-OS-suffix','exact_helper_owner':True,'run_and_cancel_visible':True}]},{'state':'needs_attention'},{'state':'waiting'},{'state':'needs_attention','shell_returned':False})
    def check(self,args,**kwargs):return expected_security_gate(*args,**{'correct_target':True,'old_processes_exited':True,'helper_exited':True,**kwargs})
    def test_exact_expected_prompt(self):self.assertTrue(self.check(self.fixture()))
    def test_shell_returned_but_not_ready_is_failure(self):
        args=self.fixture();args[3]['shell_returned']=True;self.assertFalse(self.check(args))
    def test_unbound_or_unknown_prompt_is_failure(self):
        for key,value in [('verified_target_binding',None),('exact_helper_owner',False),('title','Unknown Error'),('run_and_cancel_visible',False)]:
            args=self.fixture();args[0]['owned_prompt_windows'][0][key]=value;self.assertFalse(self.check(args))
    def test_safety_case_or_target_failure(self):
        args=self.fixture();args[0]['cases']['preferences_receipts_cache_preserved']=False;self.assertFalse(self.check(args))
        self.assertFalse(self.check(self.fixture(),correct_target=False));self.assertFalse(self.check(self.fixture(),old_processes_exited=False));self.assertFalse(self.check(self.fixture(),helper_exited=False))
    def test_confirmation_rejects_unbound_or_ambiguous_windows_before_os_calls(self):
        for field,value in [('verified_target_binding',None),('verified_target_binding','unknown'),('exact_helper_owner',False),('title','Unknown Error'),('class','Unknown'),('run_and_cancel_visible',False)]:
            window=self.fixture()[0]['owned_prompt_windows'][0]
            window[field]=value
            self.assertFalse(confirm_owned_prompt([window]))
        window=self.fixture()[0]['owned_prompt_windows'][0]
        self.assertFalse(confirm_owned_prompt([window,window]))
        self.assertFalse(confirm_owned_prompt([]))

if __name__=='__main__':unittest.main()
