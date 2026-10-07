"""Factory-protocol access via the restricted MRID6 kernel adapter."""
import json,subprocess
from pathlib import Path
BASE=Path('/sys/bus/wmi/devices/B60BFB48-3E5B-49E4-A0E9-8CFFE1B3434B/jiaolong')
HELPER='/usr/local/libexec/jiaolong-control'
PROFILES={0:'游戏',1:'狂飙',2:'办公'}
FAN_LIMITS={0:(3500,5000),1:(5000,5800),2:(2200,3500)}
CUSTOM_FAN_LIMITS=(2200,5800)
def state():
 if not (BASE/'state').exists():return {'connected':False,'error':'原厂通信驱动未加载'}
 try:
  s=json.loads((BASE/'state').read_text());s['connected']=True;s['fan_custom_supported']=(BASE/'fan_custom_rpm').exists();return s
 except (OSError,ValueError) as e:return {'connected':False,'error':str(e)}
def control(action,value):
 if action not in {'profile','gpu_mode','kb_brightness','kb_mode','kb_color','fn_lock','touchpad_lock','fan_auto','fan_rpm','fan_custom_rpm','ambient_light','cpu_limits','cpu_custom_mode','num_lock','caps_lock','gpu_offset'}:
  raise ValueError('不支持的固件操作')
 r=subprocess.run(['/usr/bin/pkexec',HELPER,action,str(value)],text=True,capture_output=True,timeout=180)
 if r.returncode:raise RuntimeError(r.stderr.strip() or '管理员认证已取消')
 return json.loads(r.stdout)
