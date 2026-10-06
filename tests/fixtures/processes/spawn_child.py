import json
import os
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid]))
if len(sys.argv) < 3:
    time.sleep(60)
