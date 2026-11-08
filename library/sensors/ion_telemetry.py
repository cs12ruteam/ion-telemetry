"""Custom sources for Ion Telemetry on Turing System Monitor 3.10.0."""
import json
import logging
import math
import os
import re
import time
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import psutil
from .sensors_custom import CustomDataSource

GIB = 1024 ** 3
MIB = 1024 ** 2
GPU_QUERY = "SELECT Name, UtilizationPercentage FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine"
DISK_QUERY = "SELECT Name, PercentIdleTime FROM Win32_PerfFormattedData_PerfDisk_PhysicalDisk"
GPU_NAME = re.compile(r"pid_(\d+)_(luid_.*?_phys_\d+_eng_\d+)_engtype_", re.I)
LOG = logging.getLogger(__name__)
MAX_IO_AGE = 10
SORT = "AUTO"  # AUTO, CPU, GPU, DISK, NET
ENABLE_GPU = True  # Set to False for a CPU-only process sampler


@lru_cache(maxsize=1)
def management_api():
    import clr
    clr.AddReference("System.Management")
    from System import TimeSpan
    from System.Management import ManagementObjectSearcher
    return ManagementObjectSearcher, TimeSpan


def windows_counter(query, field):
    searcher_type, span = management_api()
    searcher = searcher_type(query)
    try:
        searcher.Options.Timeout = span.FromSeconds(2)
        collection = searcher.Get()
        try:
            rows = []
            for item in collection:
                try:
                    name, value = item["Name"], item[field]
                    if name is not None and value is not None:
                        value = float(value)
                        if math.isfinite(value):
                            rows.append((str(name),value))
                finally:
                    item.Dispose()
            return rows
        finally:
            collection.Dispose()
    finally:
        searcher.Dispose()


def aggregate_gpu(rows):
    engines = defaultdict(float)
    for name,value in rows:
        match = GPU_NAME.match(name)
        if match and value>=0:
            pid,engine=match.groups()
            engines[int(pid),engine.lower()]+=value
    if not engines:
        return None,None
    by_pid={}
    by_engine=defaultdict(float)
    for (pid,engine),value in engines.items():
        by_pid[pid]=max(by_pid.get(pid,0),min(100,value))
        by_engine[engine]+=value
    return min(100,max(by_engine.values())),by_pid


def busiest_disk(rows):
    idle=[value for name,value in rows if name.lower()!="_total" and 0<=value<=100]
    return 100-min(idle) if idle else None


def io_snapshot():
    root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    path = root / "TuringIonTelemetry" / "io.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - float(doc["timestamp"])
        if not 0 <= age <= MAX_IO_AGE:
            return None
        return {int(pid): (float(rates[0]),float(rates[1])) for pid,rates in doc["rates"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        return None


def process_name(raw,limit=17):
    raw=raw or ""
    if raw.lower().endswith(".exe"):
        raw=raw[:-4]
    # Leave one mono cell per character even for unusual executable names.
    plain="".join(c if c.isprintable() and not unicodedata.combining(c)
                  and unicodedata.east_asian_width(c) not in {"W","F"} else "?" for c in raw)
    return plain if len(plain)<=limit else plain[:limit-1]+"…"


def percent(value):
    if value is None:
        return "—"
    return "<1%" if 0<value<1 else f"{value:.0f}%"


def rate(value):
    if value is None:
        return "—"
    if value>=9999.95:
        return ">9999"
    return "<0.1" if 0<value<0.05 else f"{value:.1f}"


def format_row(name,cpu,gpu,disk,net):
    return f"{process_name(name):<17}{percent(gpu):>4}{percent(cpu):>6}{rate(disk):>10}{rate(net):>10}"


def select_rows(rows,mode="AUTO",loads=None):
    """Rank by the selected column, or by the most active comparable resource."""
    indices={"CPU":1,"GPU":2,"DISK":3,"NET":4}
    if mode=="AUTO":
        available={name:value for name,value in (loads or {}).items() if value is not None}
        mode=max(available,key=available.get) if available else "CPU"
    primary=indices.get(mode,1)
    result=sorted(rows,key=lambda row:(row[primary] or 0,row[1] or 0,row[2] or 0),reverse=True)
    return [row for row in result if any(value and value>0 for value in row[1:])][:3]


class ProcessSampler:
    def __init__(self):
        self.previous={}
        self.previous_time=None
        self.cpus=psutil.cpu_count() or 1
        self.gpu_retry_at=0
        self.warned=False
        self.disk_retry_at=0

    def sample(self):
        now=time.monotonic()
        gpu_load,gpu=None,None
        disk_load=None
        cpu_load=psutil.cpu_percent()
        if ENABLE_GPU and os.name=="nt" and now>=self.gpu_retry_at:
            try:
                gpu_load,gpu=aggregate_gpu(windows_counter(GPU_QUERY,"UtilizationPercentage"))
            except Exception as exc:
                self.gpu_retry_at=now+30
                if not self.warned:
                    LOG.warning("Ion: Windows GPU process counters unavailable: %s",exc)
                    self.warned=True
        io=io_snapshot()
        if io is not None and os.name=="nt" and now>=self.disk_retry_at:
            try:
                disk_load=busiest_disk(windows_counter(DISK_QUERY,"PercentIdleTime"))
            except Exception as exc:
                self.disk_retry_at=now+60
                LOG.debug("Ion: disk activity counter unavailable: %s",exc)
        now=time.monotonic()
        elapsed=0 if self.previous_time is None else now-self.previous_time
        current={}
        rows=[]
        for proc in psutil.process_iter(["name","create_time","cpu_times"],ad_value=None):
            if proc.pid==0:
                continue
            info=proc.info
            cpu=None
            if info["create_time"] is not None and info["cpu_times"] is not None:
                key=(proc.pid,info["create_time"])
                times=info["cpu_times"]
                seconds=times.user+times.system
                current[key]=seconds
                if elapsed>0 and key in self.previous:
                    cpu=min(100,max(0,(seconds-self.previous[key])*100/elapsed/self.cpus))
            gpu_value=None if gpu is None else gpu.get(proc.pid,0)
            disk,net=(None,None) if io is None else io.get(proc.pid,(0,0))
            rows.append((info["name"] or f"PID {proc.pid}",cpu,gpu_value,disk,net))
        self.previous,self.previous_time=current,now
        return select_rows(rows,SORT,{"CPU":cpu_load,"GPU":gpu_load,"DISK":disk_load})


def delta_value(values,steps=15):
    values=tuple(values)
    if len(values)<=steps:
        return "Δ —"
    latest, earlier=values[-1],values[-1-steps]
    if not (math.isfinite(latest) and math.isfinite(earlier)):
        return "Δ —"
    return f"Δ {round(latest-earlier):+d}°"


def round_gib(number):
    return f"{number/GIB:.0f}"


_sampler=ProcessSampler()
_rows=[]


class _Text(CustomDataSource):
    def as_numeric(self):
        return None

    def last_values(self):
        return None


class IonRamTotal(_Text):
    def as_string(self):
        memory=psutil.virtual_memory()
        return f"{round_gib(memory.total-memory.available)}/{round_gib(memory.total)}"


class IonCpuDelta(_Text):
    def as_string(self):
        from library.stats import CPU
        return delta_value(CPU.last_values_cpu_temperature)


class IonGpuDelta(_Text):
    def as_string(self):
        from library.stats import Gpu
        return delta_value(Gpu.last_values_gpu_temperature)


class IonProcessOne(_Text):
    def as_string(self):
        global _rows
        try:
            _rows=_sampler.sample()
        except (OSError,psutil.Error):
            _rows=[]
        return format_row(*_rows[0]) if _rows else "—"


class IonProcessTwo(_Text):
    def as_string(self):
        return format_row(*_rows[1]) if len(_rows)>1 else "—"


class IonProcessThree(_Text):
    def as_string(self):
        return format_row(*_rows[2]) if len(_rows)>2 else "—"
