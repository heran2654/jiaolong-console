#!/usr/bin/python3
"""Install only this application's user-local files. No elevated permissions."""
from pathlib import Path
import shutil,shlex
src=Path(__file__).resolve().parent
home=Path.home()
target=home/'.local/share/jiaolong-console'
if target.exists():
 backup=target.with_name('jiaolong-console.previous')
 if backup.exists(): shutil.rmtree(backup)
 shutil.copytree(target,backup)
target.mkdir(parents=True,exist_ok=True)
for name in ('app.py','backend.py','firmware.py','native.py','io.local.JiaolongConsole.svg','README.md'):
 shutil.copy2(src/name,target/name)
shutil.copytree(src/'driver',target/'driver',dirs_exist_ok=True,
 ignore=shutil.ignore_patterns('__pycache__','*.o','*.mod','*.mod.c','*.cmd','modules.order','Module.symvers'))
launcher=home/'.local/bin/jiaolong-console'; launcher.parent.mkdir(parents=True,exist_ok=True)
launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 '+shlex.quote(str(target/'app.py'))+' "$@"\n');launcher.chmod(0o755)
icon=home/'.local/share/icons/hicolor/scalable/apps/io.local.JiaolongConsole.svg'
icon.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src/icon.name,icon)
desktop=home/'.local/share/applications/io.local.JiaolongConsole.desktop';desktop.parent.mkdir(parents=True,exist_ok=True)
desktop.write_text('[Desktop Entry]\nType=Application\nName=蛟龙控制中心\nComment=蛟龙原厂布局与硬件控制\nExec="'+str(launcher)+'"\nIcon=io.local.JiaolongConsole\nTerminal=false\nCategories=Settings;HardwareSettings;\nKeywords=蛟龙;机械革命;温度;性能;Jiaolong;Mechrevo;\nStartupNotify=true\nStartupWMClass=io.local.JiaolongConsole\n')
print('Installed:',target)
print('Launcher:',launcher)
