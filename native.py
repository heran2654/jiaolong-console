"""Native Linux replacements for the factory's Win32 lock keys and NVIDIA NVAPI.

uinput: https://docs.kernel.org/input/uinput.html
NVIDIA ABI: nvmlClockOffset_v1 / GetClockOffsets / SetClockOffsets, graphics clock, P0.
Only Num Lock and Caps Lock can be generated; no arbitrary input interface is exposed.
"""
import ctypes
import fcntl
import os
from pathlib import Path
import struct
import time

KEYS = {'num_lock':(69,'numlock',0), 'caps_lock':(58,'capslock',1)}


def lock_state(action):
    if action not in KEYS: raise ValueError('不支持的键盘操作')
    values = []
    for path in Path('/sys/class/leds').glob('input*::'+KEYS[action][1]+'/brightness'):
        try: values.append(int(path.read_text().strip()))
        except (OSError,ValueError): pass
    return any(values) if values else None


def set_lock(action, enabled):
    if os.geteuid()!=0: raise PermissionError('键盘控制需要管理员认证')
    if action not in KEYS or enabled not in (0,1): raise ValueError('键盘参数错误')
    before = lock_state(action)
    if before is None: raise RuntimeError('无法读取键盘锁定状态')
    if before==bool(enabled): return {'before':before,'after':before}
    key,_,led = KEYS[action]
    # _IOW constants and struct uinput_setup ABI from the running x86-64 kernel headers.
    fd = os.open('/dev/uinput',os.O_RDWR|os.O_NONBLOCK|os.O_CLOEXEC)
    created = False
    def emit(value):
        os.write(fd,struct.pack('@llHHi',0,0,1,key,value))
        os.write(fd,struct.pack('@llHHi',0,0,0,0,0))
    try:
        for event in (1,17): fcntl.ioctl(fd,0x40045564,event) # EV_KEY, EV_LED
        # Q–Y capability bits identify this device as a keyboard to udev/libinput.
        # The helper never emits those keys: its only output codes are 58 and 69.
        for code in (16,17,18,19,20,21,58,69): fcntl.ioctl(fd,0x40045565,code)
        for code in (0,1): fcntl.ioctl(fd,0x40045569,code)
        setup = struct.pack('@HHHH80sI',3,0x1209,0x4a4c,1,b'Jiaolong Lock Keys',0)
        fcntl.ioctl(fd,0x405c5503,setup); fcntl.ioctl(fd,0x5501); created = True
        # Let udev and the active compositor attach, and apply their existing lock state.
        time.sleep(1)
        if lock_state(action)!=bool(enabled):
            emit(1); time.sleep(.03); emit(0)
        deadline = time.monotonic()+3
        while time.monotonic()<deadline:
            if lock_state(action)==bool(enabled):
                return {'before':before,'after':bool(enabled)}
            time.sleep(.05)
        raise RuntimeError('桌面未确认键盘锁定状态，未能应用')
    finally:
        if created:
            try: emit(0); fcntl.ioctl(fd,0x5502)
            except OSError: pass
        os.close(fd)


class ClockOffset(ctypes.Structure):
    _fields_ = [('version',ctypes.c_uint),('type',ctypes.c_uint),('pstate',ctypes.c_uint),
                ('clockOffsetMHz',ctypes.c_int),('minClockOffsetMHz',ctypes.c_int),('maxClockOffsetMHz',ctypes.c_int)]


class Nvidia:
    def __init__(self):
        self.lib = ctypes.CDLL('/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1')
        self.lib.nvmlErrorString.argtypes = [ctypes.c_int]; self.lib.nvmlErrorString.restype = ctypes.c_char_p
        self.check(self.lib.nvmlInit_v2())
        try:
            self.device = ctypes.c_void_p()
            self.lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint,ctypes.POINTER(ctypes.c_void_p)]
            self.check(self.lib.nvmlDeviceGetHandleByIndex_v2(0,ctypes.byref(self.device)))
            name = ctypes.create_string_buffer(96)
            self.lib.nvmlDeviceGetName.argtypes = [ctypes.c_void_p,ctypes.c_char_p,ctypes.c_uint]
            self.check(self.lib.nvmlDeviceGetName(self.device,name,len(name)))
            if name.value.decode()!='NVIDIA GeForce RTX 4070 Laptop GPU': raise RuntimeError('显卡与当前适配版本不符')
            self.lib.nvmlDeviceGetClockOffsets.argtypes = [ctypes.c_void_p,ctypes.POINTER(ClockOffset)]
            self.lib.nvmlDeviceSetClockOffsets.argtypes = [ctypes.c_void_p,ctypes.POINTER(ClockOffset)]
        except Exception:
            self.close(); raise

    def check(self, result):
        if result: raise RuntimeError('NVIDIA：'+self.lib.nvmlErrorString(result).decode())

    def info(self):
        info = ClockOffset(); info.version = ctypes.sizeof(ClockOffset)|(1<<24)
        info.type = 0; info.pstate = 0 # NVML_CLOCK_GRAPHICS, P0
        self.check(self.lib.nvmlDeviceGetClockOffsets(self.device,ctypes.byref(info)))
        return info

    def close(self): self.lib.nvmlShutdown()


def gpu_state():
    device = None
    try:
        device = Nvidia(); info = device.info()
        return {'supported':True,'offset_mhz':info.clockOffsetMHz,
                'minimum_mhz':max(0,info.minClockOffsetMHz),'maximum_mhz':min(200,info.maxClockOffsetMHz)}
    except (OSError,AttributeError,RuntimeError) as error: return {'supported':False,'error':str(error)}
    finally:
        if device: device.close()


def set_gpu_offset(value):
    if os.geteuid()!=0: raise PermissionError('显卡设置需要管理员认证')
    if not isinstance(value,int) or not 0<=value<=200: raise ValueError('显卡超频范围为 0–200 MHz')
    device = Nvidia()
    try:
        before = device.info(); target = device.info()
        if not target.minClockOffsetMHz<=value<=target.maxClockOffsetMHz: raise ValueError('超出显卡驱动支持范围')
        if before.clockOffsetMHz!=value:
            try:
                target.clockOffsetMHz = value
                device.check(device.lib.nvmlDeviceSetClockOffsets(device.device,ctypes.byref(target)))
                if device.info().clockOffsetMHz!=value: raise RuntimeError('显卡超频参数读回不符')
            except Exception:
                device.check(device.lib.nvmlDeviceSetClockOffsets(device.device,ctypes.byref(before)))
                raise
        return {'before':before.clockOffsetMHz,'after':value}
    finally: device.close()


def memory_info():
    speeds = []
    for path in Path('/sys/firmware/dmi/entries').glob('17-*/raw'):
        try:
            data = path.read_bytes()
            if len(data)>=34 and data[1]>=34:
                speed = int.from_bytes(data[32:34],'little') or int.from_bytes(data[21:23],'little')
                if 0<speed<65535: speeds.append(speed)
        except OSError: pass
    return {'memory_freq_mhz':min(speeds) if speeds else None,
            'board':Path('/sys/class/dmi/id/board_name').read_text().strip(),
            'bios':Path('/sys/class/dmi/id/bios_version').read_text().strip()}
