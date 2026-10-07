#!/usr/bin/python3 -I
"""Install the reviewed local components and run reversible verification in one authentication."""
from pathlib import Path
import os,subprocess
if os.geteuid()!=0:raise SystemExit('需要管理员认证')
root=Path(__file__).resolve().parent
for name in ('install-driver.py','verify-0.4.py'):
 subprocess.run(['/usr/bin/python3','-I',str(root/name)],check=True)
