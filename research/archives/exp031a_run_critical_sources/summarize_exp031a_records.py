from __future__ import annotations
import collections
import datetime as dt
import json
from pathlib import Path

ROOT = Path('.codex_tmp/exp031a-record-summaries')
def load(rel): return json.loads((ROOT / rel).read_text(encoding='utf-8'))
def ts(value): return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))

live = load('heldout_validation/live_full_field/validation_summary.json')
selection = load('heldout_validation/live_full_field/checkpoint_selection.json')
instant = load('deployment_field/instant_add_report.json')
first = load('first37/final_summary.json')
static = load('runtime/static_counts.json')
training = load('joint_training/training_summary.json')
zero = load('joint_training/zero_policy_nll_summary.json')
manifest = load('run_manifest.json')
rows = [json.loads(line) for line in (ROOT / 'attempts.jsonl').read_text(encoding='utf-8').splitlines() if line]
by_id = collections.defaultdict(dict)
for row in rows: by_id[row['attempt_id']][row['event']] = row
attempts=[]
phase_seconds=collections.defaultdict(float)
phase_attempts=collections.Counter()
for attempt_id, events in by_id.items():
    start=events.get('start'); end=events.get('end')
    duration=(ts(end['end_timestamp_utc'])-ts(start['start_timestamp_utc'])).total_seconds() if start and end else None
    phase=(start or end).get('phase')
    if duration is not None:
        phase_seconds[phase]+=duration
    phase_attempts[phase]+=1
    attempts.append({'attempt_id':attempt_id,'phase':phase,'duration_seconds':duration,'exit_code':None if not end else end.get('exit_code'),'stop_reason':None if not end else end.get('stop_reason')})
compact_live=[]
for report in live['reports']:
    compact_live.append({k:report[k] for k in report if k not in {'metrics'}} | {
        'metrics': {condition:{name:value for name,value in metrics.items() if name != 'per_state_correct_minus_zero'} for condition,metrics in report['metrics'].items() if condition.startswith('L')}
    })
compact_first={
  'success_count':first['success_count'], 'D1_minus_D0':first['D1_minus_D0'], 'D1_minus_D2':first['D1_minus_D2'],
  'interpretation':first['interpretation'],'decision_branch':first['decision_branch'],
  'conditions':{c:{k:v for k,v in s.items() if k in {'success_count','success_ids','total_steps','total_prompt_tokens','total_generated_tokens','total_wall_seconds','counts','mean_field_read_seconds','mean_query_seconds','mean_reader_delta_norm'}} for c,s in first['summaries'].items()}
}
result={
 'static_counts':static,
 'training':training,
 'zero_cache':zero,
 'live_reports':compact_live,
 'selection':selection,
 'instant_add':instant,
 'first37':compact_first,
 'attempts':sorted(attempts,key=lambda x:x['attempt_id']),
 'phase_seconds':dict(sorted(phase_seconds.items())),
 'phase_attempt_counts':dict(sorted(phase_attempts.items())),
 'attempt_count':len(by_id),
 'open_attempts':[k for k,v in by_id.items() if 'start' not in v or 'end' not in v],
 'source_commits':{'starting':manifest['starting_head'],'preparation':manifest['preparation_head']},
}
print(json.dumps(result,indent=2,sort_keys=True))