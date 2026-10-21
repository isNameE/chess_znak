"""Create/start a task-owned PostgreSQL cluster; never touch the system service."""
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    import psycopg
    from psycopg import sql
    from dotenv import dotenv_values
    if os.name != "nt":
        raise SystemExit("Use docker compose or an existing PostgreSQL server on Linux.")
    candidates=list(Path(os.environ.get("ProgramFiles","C:/Program Files")).glob("PostgreSQL/*/bin/pg_ctl.exe"))
    found=shutil.which("pg_ctl")
    if not found and not candidates:
        raise SystemExit("PostgreSQL not found. Install PostgreSQL or use docker compose.")
    bin_dir=Path(found).parent if found else sorted(candidates,reverse=True)[0].parent
    data=ROOT/"data"
    data.mkdir(exist_ok=True)
    cluster=(data/"pg18").resolve()
    if cluster.parent!=data.resolve():
        raise SystemExit("Unexpected cluster path")
    env=ROOT/".env"
    if not env.exists():
        password=secrets.token_urlsafe(32)
        env.write_text("DATABASE_URL=postgresql://arm_demo:"+quote(password,safe="")+"@127.0.0.1:55432/arm_demo\n"
                       "ARM_HOST=127.0.0.1\nARM_PORT=8765\nTRUE_API_ENABLED=false\n",encoding="utf-8")
    values=dotenv_values(env)
    from urllib.parse import urlparse,unquote
    parsed=urlparse(values["DATABASE_URL"])
    if parsed.hostname!="127.0.0.1" or parsed.port!=55432 or parsed.username!="arm_demo" or parsed.path!="/arm_demo":
        raise SystemExit("Existing .env uses another database. No changes made; manage it manually.")
    password=unquote(parsed.password or "")
    if not password:
        raise SystemExit("Set a database password in .env before setup.")
    if not (cluster/"PG_VERSION").exists():
        secret_file=data/".init-password"
        secret_file.write_text(password,encoding="utf-8")
        try:
            subprocess.run([str(bin_dir/"initdb.exe"),"-D",str(cluster),"-U","arm_demo","--auth=scram-sha-256",
                            "--pwfile",str(secret_file),"--encoding=UTF8","--locale=C"],check=True)
        finally:
            secret_file.unlink(missing_ok=True)
    running=subprocess.run([str(bin_dir/"pg_ctl.exe"),"-D",str(cluster),"status"],capture_output=True).returncode==0
    if not running:
        subprocess.run([str(bin_dir/"pg_ctl.exe"),"-D",str(cluster),"-l",str(data/"postgres.log"),
                        "-o","-p 55432 -h 127.0.0.1","-w","start"],check=True)
    # Safe also for the initially created trust-auth demo cluster: immediately require SCRAM afterwards.
    with psycopg.connect(host="127.0.0.1",port=55432,user="arm_demo",password=password,dbname="postgres",autocommit=True) as con:
        con.execute(sql.SQL("ALTER ROLE arm_demo PASSWORD {} ").format(sql.Literal(password)))
        if not con.execute("SELECT 1 FROM pg_database WHERE datname='arm_demo'").fetchone():
            con.execute("CREATE DATABASE arm_demo OWNER arm_demo")
    (cluster/"pg_hba.conf").write_text("host all all 127.0.0.1/32 scram-sha-256\n",encoding="utf-8")
    subprocess.run([str(bin_dir/"pg_ctl.exe"),"-D",str(cluster),"reload"],check=True)
    print("Local PostgreSQL ready on 127.0.0.1:55432. Credentials stored only in ignored .env.")


if __name__=="__main__":
    main()
