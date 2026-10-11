"""Reject credential/storage-state evidence before private CI artifact upload."""
import json,os,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();private=Path(os.environ['PILOT_E2E_CREDENTIALS']).resolve();values=[]
def secrets_in(obj):
 if isinstance(obj,dict):
  for k,v in obj.items():
   if k in ('password','value','draft_key','token') and isinstance(v,str) and len(v)>=12:values.append(v.encode())
   else:secrets_in(v)
 elif isinstance(obj,list):
  for v in obj:secrets_in(v)
for p in private.parent.glob('pilot-private*'):
 if p.is_file():
  try:secrets_in(json.loads(p.read_text()))
  except (ValueError,UnicodeError):pass
if os.getenv('PREVIEW_SECRET'):values.append(os.environ['PREVIEW_SECRET'].encode())
for f in root.rglob('*'):
 if not f.is_file():continue
 assert not any(x in f.name.lower() for x in ('credential','storage-state','owner-state','private-secret')),str(f)
 raw=f.read_bytes();assert not any(v in raw for v in values),'Secret in artifact '+str(f)
print('PRIVATE_DAILY_ARTIFACT_GUARD_PASS')
