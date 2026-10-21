"""IT-only sandbox document transport. Signature is produced externally."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config import settings
from integrations.true_api import TrueApi,IntegrationError
from integrations.submissions import submit
import repository


def main():
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--submit',type=Path,help='Exact UTF-8 JSON bytes signed with detached CMS')
    group.add_argument('--status',help='External document ID from CRPT')
    p.add_argument('--signature',type=Path,help='Base64 detached CMS without line breaks')
    args=p.parse_args()
    api=TrueApi(settings())
    try:
        if args.status:result=api.document_info(args.status)
        else:
            if not args.signature:p.error('--signature is required with --submit')
            repository.migrate()
            result=submit(api,args.submit.read_bytes(),args.signature.read_text(encoding='ascii').strip())
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except IntegrationError as exc:raise SystemExit(str(exc)) from None
    finally:api.close()


if __name__=='__main__':main()
