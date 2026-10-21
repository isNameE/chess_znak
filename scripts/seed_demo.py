"""Create a full demonstration pallet only in an empty application database."""
import sys
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import repository


if __name__=='__main__':
    repository.migrate()
    s=repository.read_state()
    if s['boxes'] or s['draft'] or s['palletDraft']:
        raise SystemExit('Database is not empty; no changes made.')
    for action in ('fill-pallet','close-pallet','send-all'):
        s,error=repository.command(action,{},str(uuid4()),s['revision'])
        if error:raise SystemExit(error)
    print('Created 50 demo boxes, one pallet and 51 simulator documents.')
