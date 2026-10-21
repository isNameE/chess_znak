"""Independent DEMO queue worker. Does not connect to CRPT or process real documents."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import repository
from plant import process_simulator_batch


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loop',action='store_true')
    parser.add_argument('--interval',type=int,default=5,choices=range(2,61))
    args=parser.parse_args();repository.migrate()
    try:
        while True:
            result=process_simulator_batch(args.interval*3 if args.loop else 0)
            if result['processed'] or not args.loop:print(json.dumps(result),flush=True)
            if not args.loop:break
            time.sleep(args.interval)
    except KeyboardInterrupt:pass
