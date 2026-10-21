"""PostgreSQL custom-format backup, no credentials in command line arguments."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime
from urllib.parse import urlparse,unquote,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config import settings,ROOT


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path)
    args=p.parse_args()
    executable=shutil.which('pg_dump')
    if not executable and os.name=='nt':
        found=sorted(Path(os.environ.get('ProgramFiles','C:/Program Files')).glob('PostgreSQL/*/bin/pg_dump.exe'),reverse=True)
        executable=str(found[0]) if found else None
    if not executable:raise SystemExit('pg_dump not found; install PostgreSQL client tools matching the server version.')
    target=(args.out or ROOT/'backups'/datetime.now().strftime('arm-%Y%m%d-%H%M%S.dump')).resolve()
    if target.exists():raise SystemExit('Backup file already exists. Choose another name.')
    target.parent.mkdir(parents=True,exist_ok=True)
    url=urlparse(settings().database_url)
    env=os.environ.copy()
    env.update(PGHOST=url.hostname or '127.0.0.1',PGPORT=str(url.port or 5432),PGUSER=unquote(url.username or ''),
               PGPASSWORD=unquote(url.password or ''),PGDATABASE=unquote(url.path.lstrip('/')))
    query=parse_qs(url.query)
    if 'sslmode' in query:env['PGSSLMODE']=query['sslmode'][0]
    if 'sslrootcert' in query:env['PGSSLROOTCERT']=query['sslrootcert'][0]
    subprocess.run([executable,'--format=custom','--file',str(target)],env=env,check=True)
    print('Backup created:',target)


if __name__=='__main__':main()
