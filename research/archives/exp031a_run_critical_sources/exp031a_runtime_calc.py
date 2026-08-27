import datetime as dt, json
from pathlib import Path
rows=[json.loads(x) for x in Path('.codex_tmp/exp031a-record-summaries/attempts.jsonl').read_text().splitlines() if x]
by={}
for r in rows: by.setdefault(r['attempt_id'],{})[r['event']]=r
def t(s): return dt.datetime.fromisoformat(s.replace('Z','+00:00'))
gpu_prefixes=('joint_full_bank_preflight','joint_full_bank_smoke','joint_full_bank_zero-cache','joint_full_bank_train','joint_full_bank_teacher','joint_full_bank_validate','joint_full_bank_instant-add','joint_full_bank_first37_smoke','joint_full_bank_first37_run')
gpu=0
for a,e in by.items():
 if 'start' in e and 'end' in e and e['start']['phase'].startswith(gpu_prefixes): gpu+=(t(e['end']['end_timestamp_utc'])-t(e['start']['start_timestamp_utc'])).total_seconds()
science=[e for a,e in by.items() if a not in {'prepare-001','audit-export-001','audit-export-002','audit-export-003','audit-export-004','audit-export-005','audit-export-006','audit-export-007','audit-export-008'}]
starts=[t(e['start']['start_timestamp_utc']) for e in science if 'start' in e]
ends=[t(e['end']['end_timestamp_utc']) for e in science if 'end' in e]
print(json.dumps({'gpu_attempt_seconds':gpu,'gpu_attempt_hours':gpu/3600,'scientific_wall_start_utc':min(starts).isoformat(),'scientific_wall_end_utc':max(ends).isoformat(),'scientific_wall_seconds':(max(ends)-min(starts)).total_seconds(),'scientific_wall_hours':(max(ends)-min(starts)).total_seconds()/3600},indent=2))