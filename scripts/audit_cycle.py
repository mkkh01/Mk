import json
from collections import Counter
from pathlib import Path

payload = json.loads(Path('/tmp/root_audit_cycle.json').read_text())
diag = payload.get('diagnostics') or {}
obs = diag.get('quality_observations') or []
print('counts', {k: payload.get(k) for k in ['pairs_analyzed','analyses_executed','signals_found','approved','rejected','database_writes','errors','warnings']})
print('pre_timing_eligible', diag.get('pre_timing_eligible'), 'signal_quality_passed', diag.get('signal_quality_passed'), 'timing_checked', diag.get('entry_timing_checked'))
print('pre_timing_blocks', diag.get('pre_timing_block_reasons'))
print('quality_blocks', diag.get('signal_quality_failure_reasons'))
print('timing_rejections', diag.get('timing_rejection_reasons'))
for name, key in [('direction','primary_direction'),('htf_bias','htf_bias'),('volume_state','volume_state'),('regime','regime'),('confidence_ok','confidence_ok'),('signal_quality_ok','signal_quality_ok')]:
    print(name, Counter(o.get(key) for o in obs))
for key in ['confidence','score','momentum_score','volume_score','volume_confirmation','volume_ratio','cvd_slope','delta']:
    vals=[float(o[key]) for o in obs if o.get(key) is not None]
    if vals:
        print(key, {'min':min(vals),'max':max(vals),'avg':sum(vals)/len(vals)})
print('latest')
print(json.dumps(obs[-5:], indent=2))
