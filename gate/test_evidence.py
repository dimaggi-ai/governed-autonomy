"""Full authenticated gate regressions, distinct from declaration-only tests."""
import hashlib
import hmac
import json
import secrets
import tempfile
import unittest
import copy
from pathlib import Path
from .evidence import canonical, record_digest, verify_receipts
from .validate_promotion import validate
from .test_gate import GOOD_L2


class EvidenceTests(unittest.TestCase):
    def test_higher_level_signed_observations(self):
        rec=dict(GOOD_L2,level='L4',receipts=['receipt.json'],implementation_sha256='1'*64)
        key=secrets.token_bytes(32)
        limits=dict(abort_never_missed_deadline_ms=100,rollback_deadline_ms=100,
                    max_relative_regression=.05,min_canary_samples=10,budget_gpu_seconds=100,
                    min_dark_duration_ms=1000)
        trust={'runner':dict(key_hex=key.hex(),allowed_actions=[rec['action']],
                            allowed_domains=[rec['domain']],allowed_regions=[rec['region']],
                            execution_kinds=['simulation'],predicate_limits=limits)}
        observations=dict(abort_never_missed=[dict(triggered=True,completed_ms=5,deadline_ms=100,safe_state=True)],
                          rollback=[dict(triggered=True,completed_ms=5,deadline_ms=100,safe_state=True)],
                          reversible=[dict(action_applied=True,before_state_sha256='2'*64,restored_state_sha256='2'*64)],
                          quality_canary=[dict(baseline_goodput=100,canary_goodput=99,max_relative_regression=.05,samples=20)],
                          gpu_second_budget=[dict(wasted_gpu_seconds=10,budget_gpu_seconds=100)],
                          control_plane_dark=[dict(control_plane_available=False,local_safety_held=True,duration_ms=1000)])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def submit(obs):
                result=dict(schema='infra-experiment-result/v1',implementation_sha256=rec['implementation_sha256'],
                            experiment=rec['certified_by'][0],execution_kind='simulation',observations=obs)
                data=canonical(result);(root/'result.json').write_bytes(data)
                payload=dict(schema='infra-evidence-receipt/v1',issuer='runner',record_sha256=record_digest(rec),
                             implementation_sha256=rec['implementation_sha256'],experiment=rec['certified_by'][0],
                             execution_kind='simulation',checks={k:True for k in obs},
                             artifacts=[dict(path='result.json',sha256=hashlib.sha256(data).hexdigest())])
                (root/'receipt.json').write_text(json.dumps(dict(payload=payload,hmac_sha256=hmac.new(key,canonical(payload),hashlib.sha256).hexdigest())))
                return verify_receipts(rec,root,trust)
            self.assertEqual(submit(observations),[])
            for predicate in observations:
                with self.subTest(missing=predicate):
                    bad=copy.deepcopy(observations);del bad[predicate];self.assertTrue(submit(bad))
            for predicate,field,value in [('quality_canary','canary_goodput',90),
                ('quality_canary','max_relative_regression',.9),('quality_canary','samples',1),
                ('quality_canary','samples',10.5),
                ('gpu_second_budget','wasted_gpu_seconds',101),('gpu_second_budget','budget_gpu_seconds',1000),
                ('control_plane_dark','control_plane_available',True),('control_plane_dark','duration_ms',1),
                ('rollback','completed_ms',101),('abort_never_missed','safe_state',False)]:
                with self.subTest(predicate=predicate,field=field):
                    bad=copy.deepcopy(observations);bad[predicate][0][field]=value;self.assertTrue(submit(bad))

    def test_fabrication_valid_tampering_and_scope(self):
        rec = dict(GOOD_L2, receipts=['receipt.json'], implementation_sha256='1'*64)
        self.assertTrue(validate(rec, 'fabricated'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = secrets.token_bytes(32)
            trust = {'test-runner': {'key_hex': key.hex(), 'allowed_actions': [rec['action']],
                     'allowed_domains': [rec['domain']], 'allowed_regions': [rec['region']],
                     'execution_kinds': ['simulation'], 'predicate_limits': {'abort_never_missed_deadline_ms':100}}}
            self.assertTrue(validate(rec,'missing',evidence_root=root,trust=trust))
            result=dict(schema='infra-experiment-result/v1', implementation_sha256=rec['implementation_sha256'],
                        experiment=rec['certified_by'][0],execution_kind='simulation',observations={
                        'abort_never_missed':[dict(triggered=True,completed_ms=5,deadline_ms=100,safe_state=True)],
                        'reversible':[dict(action_applied=True,before_state_sha256='2'*64,restored_state_sha256='2'*64)]})
            artifact = canonical(result)
            (root/'result.json').write_bytes(artifact)
            payload = dict(schema='infra-evidence-receipt/v1', issuer='test-runner',
                           record_sha256=record_digest(rec), execution_kind='simulation', implementation_sha256=rec['implementation_sha256'],
                           experiment=rec['certified_by'][0], checks={'abort_never_missed':True,'reversible':True},
                           artifacts=[{'path':'result.json','sha256':hashlib.sha256(artifact).hexdigest()}])
            receipt = {'payload':payload, 'hmac_sha256':hmac.new(key,canonical(payload),hashlib.sha256).hexdigest()}
            (root/'receipt.json').write_text(json.dumps(receipt))
            self.assertEqual(validate(rec,'valid',evidence_root=root,trust=trust), [])
            # Re-sign invalid contents: integrity must not substitute for semantics.
            for mutation in ('late','missing','metadata','implementation','state','loose-policy'):
                bad=copy.deepcopy(result)
                if mutation=='late': bad['observations']['abort_never_missed'][0]['completed_ms']=101
                if mutation=='missing': del bad['observations']['reversible']
                if mutation=='metadata': bad['execution_kind']='hardware'
                if mutation=='implementation': bad['implementation_sha256']='3'*64
                if mutation=='state': bad['observations']['reversible'][0]['restored_state_sha256']='4'*64
                if mutation=='loose-policy': bad['observations']['abort_never_missed'][0]['deadline_ms']=1000
                data=canonical(bad);(root/'result.json').write_bytes(data)
                altered=copy.deepcopy(payload);altered['artifacts'][0]['sha256']=hashlib.sha256(data).hexdigest()
                (root/'receipt.json').write_text(json.dumps(dict(payload=altered,hmac_sha256=hmac.new(key,canonical(altered),hashlib.sha256).hexdigest())))
                self.assertTrue(validate(rec,mutation,evidence_root=root,trust=trust),mutation)
            (root/'result.json').write_bytes(artifact)
            (root/'receipt.json').write_text(json.dumps(receipt))
            self.assertTrue(validate(dict(rec, action='different'), 'replay', evidence_root=root,trust=trust))
            (root/'result.json').write_bytes(b'tampered')
            self.assertTrue(validate(rec,'tampered',evidence_root=root,trust=trust))
            (root/'result.json').write_bytes(artifact)
            payload['checks']['quality_canary'] = True
            (root/'receipt.json').write_text(json.dumps(receipt))
            self.assertTrue(validate(rec,'forged',evidence_root=root,trust=trust))
