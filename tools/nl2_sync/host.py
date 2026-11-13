"""Best-effort local host metadata, collected OUTSIDE timed processing."""
from __future__ import annotations
import json
import os
from pathlib import Path
import platform
from tools.nl2_timing_study.system_info import os_details
import shutil
import subprocess
import time


def host_details():
    info={**os_details(),'python':platform.python_version(),'machine':platform.machine(),
          'logical_cpus':os.cpu_count(),'processor':platform.processor(),
          'perf_counter_resolution_s':time.get_clock_info('perf_counter').resolution,
          'monotonic_resolution_s':time.get_clock_info('monotonic').resolution}
    import numpy as np
    info['numpy_version']=np.__version__
    info['thread_environment']={k:os.environ.get(k) for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS')}
    if hasattr(os,'sched_getaffinity'):
        try:info['cpu_affinity']=sorted(os.sched_getaffinity(0))
        except OSError:pass
    if os.name=='nt':
        cmd='Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,CurrentHorizontalResolution,CurrentVerticalResolution | ConvertTo-Json -Compress'
        try:
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',cmd],capture_output=True,text=True,timeout=8,check=True)
            info['display_controllers']=json.loads(result.stdout)
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            info['display_query_error']=str(exc)
    else:
        for name,path in [('cpuinfo','/proc/cpuinfo'),('cpu_governor','/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor'),
                          ('cpu_frequency_khz','/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq'),
                          ('temperature_millideg_c','/sys/class/thermal/thermal_zone0/temp')]:
            try:
                text=Path(path).read_text().strip()
                if name=='cpuinfo':text='\n'.join(dict.fromkeys(line for line in text.splitlines() if line.startswith(('Model','model name','Hardware'))))
                info[name]=text
            except OSError:pass
        vc=shutil.which('vcgencmd')
        if vc:
            try:info['pi_throttled']=subprocess.check_output([vc,'get_throttled'],text=True,timeout=2).strip()
            except (OSError,subprocess.SubprocessError):pass
    return info
