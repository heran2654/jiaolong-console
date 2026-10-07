#!/usr/bin/python3 -I
"""Explicit, reversible hardware verification. Never changes GPU or touchpad mode."""
import os,json,subprocess,time
from pathlib import Path
BASE=Path('/sys/bus/wmi/devices/B60BFB48-3E5B-49E4-A0E9-8CFFE1B3434B/jiaolong')
if os.geteuid()!=0:raise SystemExit('需要管理员认证')
def state():return json.loads((BASE/'state').read_text())
def write(key,value):(BASE/key).write_text(str(value)+'\n')
original=state();checks={};print('BEFORE',json.dumps(original),flush=True)
try:
 target=0 if original['profile']!=0 else 2
 write('profile',target);time.sleep(.5);checks['factory_profile']=state()['profile']==target
 write('profile',original['profile'])
 bright=2 if original['kb_brightness']!=2 else 1
 write('kb_brightness',bright);time.sleep(.2);checks['keyboard_brightness']=state()['kb_brightness']==bright
 write('kb_brightness',original['kb_brightness'])
 write('kb_mode',2);time.sleep(.5)
 write('kb_color','64 140 242');time.sleep(.2);checks['keyboard_rgb']=state()['kb_color']==[64,140,242]
 write('kb_color',' '.join(map(str,original['kb_color'])));write('kb_mode',original['kb_mode'])
 # Exercise factory SET20/SET21 using the upper factory target for the original mode.
 rpm={0:5000,1:5800,2:3500}[original['profile']]
 write('fan_rpm',rpm);time.sleep(8);fan=state()
 checks['fan_observed']={'target_rpm':rpm,'cpu_rpm':fan['fan_cpu_rpm'],'gpu_rpm':fan['fan_gpu_rpm']}
 print('FAN',json.dumps(checks['fan_observed']),flush=True)
finally:
 write('fan_auto',1)
 write('profile',original['profile']);write('kb_brightness',original['kb_brightness'])
 write('kb_color',' '.join(map(str,original['kb_color'])));write('kb_mode',original['kb_mode'])
 restored=state();checks['restored']=all(restored[k]==original[k] for k in ['profile','kb_brightness','kb_mode','kb_color','gpu_mode','fn_lock','touchpad_lock'])
 print('AFTER',json.dumps(restored),flush=True)
 print('CHECKS',json.dumps(checks),flush=True)
 Path('/var/lib/jiaolong-console/verification.json').write_text(json.dumps(checks,indent=2))
 if not all(checks[k] for k in ['factory_profile','keyboard_brightness','keyboard_rgb','restored']):raise SystemExit('部分读写验证失败')
