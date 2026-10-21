"""Create a personal demo account. Password entered locally, never as CLI argument."""
import argparse
import getpass
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import repository
from access import hash_password


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('login');p.add_argument('--name',required=True);p.add_argument('--role',choices=['operator','supervisor','admin'],required=True)
    args=p.parse_args()
    password=getpass.getpass('Password: ')
    if password!=getpass.getpass('Repeat password: '):raise SystemExit('Passwords differ.')
    digest=hash_password(password)
    repository.migrate()
    with repository.connect() as con:
        con.execute('INSERT INTO operators(id,display_name,role,password_hash) VALUES(%s,%s,%s,%s)',(args.login,args.name,args.role,digest))
    print('Account created. Set ARM_DEMO_MODE=false before personal-access acceptance tests.')
