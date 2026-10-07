#!/usr/bin/python3 -I
"""Install/load the reviewed adapter for the exact running kernel. Probe performs reads only."""
from pathlib import Path
import os,subprocess,shutil,json,importlib.util
ROOT=Path(__file__).resolve().parent
EXPECTED={'sys_vendor':'MECHREVO','board_name':'MRID6-23','bios_version':'MRID6_23_P_V36'}
BASE=Path('/sys/bus/wmi/devices/B60BFB48-3E5B-49E4-A0E9-8CFFE1B3434B/jiaolong')
def run(args):return subprocess.run(args,check=True,text=True,capture_output=True).stdout
if os.geteuid()!=0:raise SystemExit('请使用 pkexec 或 sudo 运行此安装器')
for k,v in EXPECTED.items():
 if (Path('/sys/class/dmi/id')/k).read_text().strip()!=v:raise SystemExit('拒绝安装：机型 / BIOS 不匹配')
kernel=os.uname().release
if run(['/usr/sbin/modinfo','-F','vermagic',str(ROOT/'jiaolong_wmi.ko')]).split()[0]!=kernel:
 raise SystemExit('驱动与当前内核版本不符，请先重新编译')
backup=Path('/var/lib/jiaolong-console');backup.mkdir(mode=0o700,parents=True,exist_ok=True)
files=[(ROOT/'jiaolong_wmi.ko',Path('/lib/modules')/kernel/'updates/jiaolong_wmi.ko'),
 (ROOT.parent/'native.py',Path('/usr/local/libexec/jiaolong-native.py')),
 (ROOT/'jiaolong-control',Path('/usr/local/libexec/jiaolong-control')),
 (ROOT/'io.local.jiaolong-control.policy',Path('/usr/share/polkit-1/actions/io.local.jiaolong-control.policy'))]
for src,dst in files:
 dst.parent.mkdir(parents=True,exist_ok=True)
 if dst.exists():shutil.copy2(dst,backup/(dst.name+'.previous'))
 shutil.copyfile(src,dst);dst.chmod(0o755 if dst.name=='jiaolong-control' else 0o644)
try:
 run(['/usr/sbin/depmod','-a',kernel])
 if Path('/sys/module/jiaolong_wmi').exists():run(['/usr/sbin/modprobe','-r','jiaolong_wmi'])
 run(['/usr/sbin/modprobe','jiaolong_wmi'])
except Exception:
 for src,dst in files:
  previous=backup/(dst.name+'.previous')
  if previous.exists():shutil.copy2(previous,dst)
 run(['/usr/sbin/depmod','-a',kernel]);run(['/usr/sbin/modprobe','jiaolong_wmi'])
 raise
if not BASE.is_dir():
 log=run(['/usr/bin/journalctl','-k','-n','25','--no-pager'])
 raise SystemExit('驱动加载后未通过固件握手\n'+log)
data=json.loads((BASE/'state').read_text())
if not (BASE/'fan_custom_rpm').exists():raise SystemExit('自定义风扇接口未加载，请检查驱动版本')
spec=importlib.util.spec_from_file_location('jiaolong_native','/usr/local/libexec/jiaolong-native.py')
native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
info=Path('/usr/local/share/jiaolong-console/hardware-info.json');info.parent.mkdir(parents=True,exist_ok=True)
info.write_text(json.dumps(native.memory_info()));info.chmod(0o644)
if not (backup/'initial-state.json').exists():
 (backup/'initial-state.json').write_text(json.dumps(data,indent=2));(backup/'initial-state.json').chmod(0o600)
print('HARDWARE_HANDSHAKE_OK',json.dumps(data))
print('未改变任何固件设置；内核升级后需重新编译加载驱动。')
