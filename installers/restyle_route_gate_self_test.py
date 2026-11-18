from pathlib import Path
import hashlib
import json
import sys
import os
import unittest
PIN=Path(os.environ['RESTYLE_TEST_OLD_PIN']) if os.environ.get('RESTYLE_TEST_OLD_PIN') else Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PIN/'shared/task-routing'));sys.path.insert(0,str(PIN/'installers'))
from task_runtime import protocol_gate
from project_framework import manifest_protocol
from restyle_route_gate import *

def digest(data):
    return hashlib.sha256(data).hexdigest()

@unittest.skipUnless(os.environ.get('RESTYLE_TEST_OLD_PIN'), 'set RESTYLE_TEST_OLD_PIN for original-runtime compatibility tests')
class RouteGateTest(unittest.TestCase):
    def setUp(self):
        self.root=sorted(Path('/tmp').glob('restyle-daily-fixture.*'))[-1]
        self.original=(self.root/MANIFEST).read_bytes()
        self.route={
            LIFECYCLE:b'{"state":"harmony-restyle-production","defaultFramework":"harmonyos-app-reimplementation","dailyFramework":"parked-until-acceptance"}',
            ROUTING:b'Only the HarmonyOS rebrand Skill until device acceptance.',
            SELECTION:b'{"enabled":true,"sourceReadOnly":true,"workflowPhase":"rebrand-production-before-acceptance","dailyHandoff":"explicit-after-acceptance"}',
            SKILL:b'Test-only Skill bytes'}
        self.transaction='7'*32; self.plan='a'*64
        self.final=self.make('complete')
    def make(self,phase):
        return marker(phase,self.transaction,self.plan,self.original,self.route)
    def check(self, data):
        return verify_final(data,digest(self.final),self.transaction,self.plan,self.original,self.route)
    def test_only_approved_complete_route(self):
        self.assertTrue(self.check(self.final)['dailyParked'])
        for state in (self.make('prepared'), self.original):
            with self.assertRaises(ValueError): self.check(state)
    def test_change_in_bound_bytes_fails(self):
        for path in tuple(self.route):
            before=self.route[path]
            self.route[path]=before+b'\n'
            with self.assertRaises(ValueError):self.check(self.final)
            self.route[path]=before
    def test_legacy_guard_real_version(self):
        target=self.root/MANIFEST
        try:
            for phase in ('prepared','complete'):
                current=self.make(phase)
                target.write_bytes(current)
                with self.assertRaises(ValueError): protocol_gate(self.root)
                with self.assertRaises(ValueError): manifest_protocol(json.loads(current))
        finally:target.write_bytes(self.original)
        protocol_gate(self.root)
    def test_invalid_role_and_duplicate_members(self):
        self.route[SELECTION]=self.route[SELECTION].replace(b'"enabled":true',b'"enabled":false')
        self.final=self.make('complete')
        with self.assertRaises(ValueError):self.check(self.final)
        self.route[SELECTION]=b'{"enabled":false,"enabled":true}'
        self.final=self.make('complete')
        with self.assertRaises(ValueError):self.check(self.final)
    def test_unknown_bytes_refused(self):
        self.route['app.ets']=b'not framework governance'
        with self.assertRaises(ValueError):self.make('complete')
if __name__=='__main__':unittest.main(verbosity=2)
