"""Read metrics used by the original console through Linux read-only interfaces."""
from pathlib import Path
import csv
import json
import os
import re
import subprocess
import time
import firmware
import native

SYS = Path('/sys')


def read(path, default=None):
    try: return Path(path).read_text().strip()
    except (OSError,UnicodeError): return default


def number(path, scale=1):
    try: return float(read(path))/scale
    except (TypeError,ValueError): return None


class Backend:
    def __init__(self):
        self.last_cpu = None
        self.cpu_name = next((line.split(':',1)[1].strip() for line in read('/proc/cpuinfo','').splitlines() if line.startswith('model name')),None)
        cores = set()
        for cpu in (SYS/'devices/system/cpu').glob('cpu[0-9]*'):
            package,core = read(cpu/'topology/physical_package_id'),read(cpu/'topology/core_id')
            if package is not None and core is not None: cores.add((package,core))
        self.cpu_cores = len(cores) or None

    def keyboard_lock(self, name):
        return native.lock_state({'numlock':'num_lock','capslock':'caps_lock'}[name])

    def memory_frequency(self):
        # SMBIOS type 17 is normally root-only. Do not request privileges for telemetry.
        speeds = []
        for path in (SYS/'firmware/dmi/entries').glob('17-*/raw'):
            try:
                data = path.read_bytes()
                if len(data)>=34 and data[1]>=34:
                    speed = int.from_bytes(data[32:34],'little') or int.from_bytes(data[21:23],'little')
                    if 0<speed<65535: speeds.append(speed)
            except OSError: pass
        if speeds: return min(speeds)
        try:
            info=json.loads(Path('/usr/local/share/jiaolong-console/hardware-info.json').read_text())
            if info['board']==read(SYS/'class/dmi/id/board_name') and info['bios']==read(SYS/'class/dmi/id/bios_version'):
                return info.get('memory_freq_mhz')
        except (OSError,ValueError,KeyError): pass
        return None

    def disk_capacity(self):
        # OEM reports physical drive 0, with free space summed across its logical volumes.
        # Ubuntu's corresponding device is the disk containing the running root filesystem.
        device = os.stat('/').st_dev
        root_node = (SYS/f'dev/block/{os.major(device)}:{os.minor(device)}').resolve()
        system_disk = root_node.parent if (root_node/'partition').exists() else root_node
        physical_sectors = number(system_disk/'size')
        free = total = 0
        seen = set()
        mounts = []
        for line in read('/proc/self/mountinfo','').splitlines():
            fields = line.split(); separator = fields.index('-')
            if fields[separator+1] not in ('ext4','btrfs','xfs','ntfs','ntfs3'): continue
            node = (SYS/'dev/block'/fields[2]).resolve()
            disk = node.parent if (node/'partition').exists() else node
            if disk!=system_disk or fields[2] in seen: continue
            mount = re.sub(r'\\([0-7]{3})',lambda match:chr(int(match[1],8)),fields[4])
            try: stats = os.statvfs(mount)
            except OSError: continue
            seen.add(fields[2]); mounts.append(mount)
            total += stats.f_blocks*stats.f_frsize
            free += stats.f_bavail*stats.f_frsize
        if not total:
            stats = os.statvfs('/'); total = stats.f_blocks*stats.f_frsize; free = stats.f_bavail*stats.f_frsize; mounts = ['/']
        physical = physical_sectors*512 if physical_sectors else total
        note = '系统硬盘 '+system_disk.name+' · 可用容量与使用率来自已挂载的数据分区：'+', '.join(mounts)
        return physical/1024**3, free/1024**3, (1-free/total)*100, note

    def sample(self):
        load = None
        try:
            parts = [int(value) for value in read('/proc/stat').splitlines()[0].split()[1:9]]
            total,idle = sum(parts),parts[3]+parts[4]
            if self.last_cpu:
                delta,idle_delta = total-self.last_cpu[0],idle-self.last_cpu[1]
                load = max(0,min(100,(delta-idle_delta)/delta*100)) if delta else 0
            self.last_cpu = total,idle
        except (TypeError,ValueError,IndexError): pass
        mem = {}
        for line in read('/proc/meminfo','').splitlines():
            key,value = line.split(':',1); mem[key] = int(value.split()[0])
        total = mem.get('MemTotal',0); used = total-mem.get('MemAvailable',total)
        temps = []
        for hwmon in (SYS/'class/hwmon').glob('*'):
            if read(hwmon/'name')=='k10temp':
                for path in hwmon.glob('temp*_input'):
                    value = number(path,1000)
                    if value is not None: temps.append((read(hwmon/path.name.replace('_input','_label')),value))
        cpu_temp = next((value for title,value in temps if title=='Tctl'),None)
        frequencies = [number(path,1e6) for path in (SYS/'devices/system/cpu').glob('cpu[0-9]*/cpufreq/scaling_cur_freq')]
        frequencies = [value for value in frequencies if value is not None]
        gpu,gpu_error = None,None
        try:
            result = subprocess.run(['nvidia-smi','--query-gpu=name,temperature.gpu,utilization.gpu,memory.total,clocks.current.graphics','--format=csv,noheader,nounits'],text=True,capture_output=True,timeout=3,check=True)
            row = next(csv.reader(result.stdout.splitlines()))
            def value(index):
                try: return float(row[index].strip())
                except ValueError: return None
            frequency = value(4)
            gpu = dict(name=row[0].strip(),temp=value(1),load=value(2),mem_total=value(3),freq_ghz=frequency/1000 if frequency is not None else None)
        except (OSError,subprocess.SubprocessError,StopIteration,IndexError): gpu_error = '独立显卡数据当前不可用'
        disk_total,disk_free,disk_load,disk_note = None,None,None,'系统硬盘数据当前不可用'
        try:
            disk_total,disk_free,disk_load,disk_note = self.disk_capacity()
        except OSError: pass
        fw = firmware.state()
        if cpu_temp is None and fw.get('connected'): cpu_temp = fw.get('cpu_temp')
        return dict(timestamp=time.time(),cpu_name=self.cpu_name,cpu_cores=self.cpu_cores,
            cpu_freq_ghz=sum(frequencies)/len(frequencies) if frequencies else None,
            cpu_load=load,cpu_temp=cpu_temp,gpu=gpu,gpu_error=gpu_error,
            memory_total_gib=total/1048576,memory_used_gib=used/1048576,
            memory_load=used/total*100 if total else None,memory_freq_mhz=self.memory_frequency(),
            disk_total_gib=disk_total,disk_free_gib=disk_free,disk_load=disk_load,disk_note=disk_note,
            num_lock=self.keyboard_lock('numlock'),caps_lock=self.keyboard_lock('capslock'),firmware=fw,
            native_ready=Path('/usr/local/libexec/jiaolong-native.py').is_file(),gpu_clock=native.gpu_state())
