"""Open an existing demo, or start one server and then open the browser."""
import json
import runpy
import threading
import urllib.request
import webbrowser
from pathlib import Path
import subprocess
import sys
from config import settings

URL = f"http://127.0.0.1:{settings().arm_port}"
try:
    with urllib.request.urlopen(URL + "/api/health", timeout=2) as response:
        state = json.load(response)
    if state.get("version") != "0.2.0" or state.get("database") != "postgresql":
        raise RuntimeError("Port 8765 is used by another application.")
except (OSError, ValueError):
    root=Path(__file__).resolve().parent
    if (root/'data'/'pg18'/'PG_VERSION').exists():
        subprocess.run([sys.executable,str(root/'scripts'/'setup_local_postgres.py')],check=True)
    def open_when_ready():
        import time
        for _ in range(60):
            try:
                with urllib.request.urlopen(URL+'/api/health',timeout=1) as result:
                    if result.status==200:
                        webbrowser.open(URL)
                        return
            except OSError:time.sleep(.5)
    threading.Thread(target=open_when_ready,daemon=True).start()
    runpy.run_path(str(Path(__file__).with_name("server.py")), run_name="__main__")
else:
    webbrowser.open(URL)
