#!/usr/bin/python3 -I
from pathlib import Path
import os,subprocess
if os.geteuid()!=0:raise SystemExit('请使用 sudo 运行')
base=Path('/sys/bus/wmi/devices/B60BFB48-3E5B-49E4-A0E9-8CFFE1B3434B/jiaolong')
if (base/'fan_auto').exists():(base/'fan_auto').write_text('1\n')
subprocess.run(['/usr/sbin/modprobe','-r','jiaolong_wmi'],check=True)
for p in [Path('/lib/modules')/os.uname().release/'updates/jiaolong_wmi.ko',Path('/usr/local/libexec/jiaolong-control'),Path('/usr/local/libexec/jiaolong-native.py'),Path('/usr/local/share/jiaolong-console/hardware-info.json'),Path('/usr/share/polkit-1/actions/io.local.jiaolong-control.policy')]:p.unlink(missing_ok=True)
subprocess.run(['/usr/sbin/depmod','-a'],check=True)
print('驱动已卸载，风扇恢复固件自动管理。')
