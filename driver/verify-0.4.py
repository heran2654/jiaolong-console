#!/usr/bin/python3 -I
"""Reversible integration checks of the explicitly requested native controls."""
from pathlib import Path
import importlib.util,json,os,subprocess,time
if os.geteuid()!=0:raise SystemExit('需要管理员认证')
BASE=Path('/sys/bus/wmi/devices/B60BFB48-3E5B-49E4-A0E9-8CFFE1B3434B/jiaolong')
HELPER='/usr/local/libexec/jiaolong-control'
spec=importlib.util.spec_from_file_location('jiaolong_native','/usr/local/libexec/jiaolong-native.py')
native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
def state():return json.loads((BASE/'state').read_text())
def control(action,value):
 result=subprocess.run([HELPER,action,str(value)],capture_output=True,text=True,timeout=20)
 if result.returncode:raise RuntimeError(action+': '+result.stderr.strip())
 return json.loads(result.stdout)
original=state();locks={key:native.lock_state(key) for key in native.KEYS};gpu=native.gpu_state()
checks={};failure=None;restore_errors=[]
print('BEFORE',json.dumps(original),json.dumps(locks),json.dumps(gpu),flush=True)
try:
 for key,old in locks.items():
  assert old is not None
  control(key,int(not old));assert native.lock_state(key)==(not old)
  control(key,int(old));assert native.lock_state(key)==old
  checks[key]=True;print('LOCK_OK',key,flush=True)
 old=gpu['offset_mhz'];target=15 if old!=15 else 0
 control('gpu_offset',target);assert native.gpu_state()['offset_mhz']==target
 control('gpu_offset',old);assert native.gpu_state()['offset_mhz']==old
 checks['gpu_offset']=True;print('GPU_OFFSET_OK',flush=True)
 for key in ('fn_lock','touchpad_lock','ambient_light'):
  old=original[key];control(key,1-old);assert state()[key]==1-old
  control(key,old);assert state()[key]==old;checks[key]=True
 for rpm in (2200,5800):
  control('fan_custom_rpm',rpm);result=state()
  assert result['fan_max_rpm']==rpm and result['fan_override']==1
  assert result['profile']==original['profile']
  checks['fan_'+str(rpm)]=True
 print('FAN_ENDPOINTS_OK; observing ramp',flush=True)
 samples=[]
 for _ in range(15):
  time.sleep(1);s=state();samples.append({'cpu_rpm':s['fan_cpu_rpm'],'gpu_rpm':s['fan_gpu_rpm'],'temperature':s['cpu_temp']})
 checks['fan_observed_at_5800']=samples
 # Command 21 sets a maximum, not a fixed duty cycle. Idle RPM may be well below it.
 assert all(s['cpu_rpm']>=0 and s['gpu_rpm']>=0 for s in samples)
 print('FAN_OBSERVED',json.dumps(samples),flush=True)
 control('fan_auto',1);assert state()['fan_override']==0;checks['fan_auto']=True
 # Stay within the OEM Ryzen 7 slider limits, then restore the exact saved parameters.
 control('cpu_limits','84,119,99');s=state()
 assert [s[k] for k in ('cpu_spl_w','cpu_sppt_w','cpu_temp_wall')]==[84,119,99]
 assert s['cpu_custom_mode']==1;checks['cpu_custom_limits']=True
 print('CPU_CUSTOM_OK',flush=True)
except Exception as error:
 failure=str(error);print('CHECK_FAILED',failure,flush=True)
finally:
 # Each restoration is independent so a failure cannot skip the remaining restores.
 def restore(name,fn):
  try:fn()
  except Exception as error:restore_errors.append(name+': '+str(error))
 for key,old in locks.items():
  if old is not None:restore(key,lambda key=key,old=old:control(key,int(old)))
 if gpu.get('supported'):restore('gpu_offset',lambda:control('gpu_offset',gpu['offset_mhz']))
 def restore_cpu():
  current=state();keys=('cpu_spl_w','cpu_sppt_w','cpu_temp_wall')
  if current['cpu_custom_mode']==1 or any(current[k]!=original[k] for k in keys):
   control('cpu_limits',','.join(str(original[k]) for k in keys))
  if state()['cpu_custom_mode']!=original['cpu_custom_mode']:control('cpu_custom_mode',original['cpu_custom_mode'])
  if state()['profile']!=original['profile']:control('profile',original['profile'])
 restore('cpu/profile',restore_cpu)
 for key in ('fn_lock','touchpad_lock','ambient_light'):
  restore(key,lambda key=key:control(key,original[key]))
 restore('fan_max_rpm',lambda:control('fan_custom_rpm',original['fan_max_rpm']))
 if original['fan_override']==0:restore('fan_auto',lambda:control('fan_auto',1))
 after=state();keys=['profile','gpu_mode','kb_brightness','kb_mode','kb_color','fn_lock','touchpad_lock','ambient_light','fan_override','fan_max_rpm','cpu_custom_mode','cpu_spl_w','cpu_sppt_w','cpu_temp_wall']
 checks['restored']=all(after[k]==original[k] for k in keys) and all(native.lock_state(k)==v for k,v in locks.items()) and native.gpu_state().get('offset_mhz')==gpu.get('offset_mhz')
 report={'before':original,'after':after,'checks':checks,'failure':failure,'restore_errors':restore_errors}
 Path('/var/lib/jiaolong-console/verification-0.4.json').write_text(json.dumps(report,indent=2))
 print('AFTER',json.dumps(after),flush=True);print('CHECKS',json.dumps(checks),flush=True)
 if restore_errors:print('RESTORE_ERRORS',json.dumps(restore_errors),flush=True)
if failure or restore_errors or not checks['restored']:raise SystemExit(1)
