"""System monitor on the badge (Ctrl+Alt+U): clock, CPU (overall + per-core bars), memory, disk,
network throughput, load average, uptime and CPU temperature. Host-rendered from psutil."""
from __future__ import annotations

import time

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, HOSTED, OK, WARN, BAD, _ttf

W, H = C.OUT_W, C.OUT_H


def _rate(bps):
    for unit, div in (("GB/s", 1e9), ("MB/s", 1e6), ("kB/s", 1e3)):
        if bps >= div:
            return f"{bps / div:.1f} {unit}"
    return f"{bps:.0f} B/s"


def _bar(d, x, y, w, h, frac, col):
    d.rectangle([x, y, x + w, y + h], outline=GRID)
    fw = int(w * max(0.0, min(1.0, frac)))
    if fw > 0:
        d.rectangle([x, y, x + fw, y + h], fill=col)


def _heat(frac):
    return OK if frac < 0.6 else WARN if frac < 0.85 else BAD


class SystemView:
    def __init__(self):
        self.f_big, self.f, self.f_s = _ttf(15, bold=True), _ttf(13), _ttf(11)
        self._net = None
        self._net_t = 0.0
        self._rx = self._tx = 0.0

    def render(self, now=None) -> np.ndarray:
        import psutil
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        t = time.localtime()
        d.text((8, 4), time.strftime("%H:%M:%S", t), font=self.f_big, fill=INK)
        d.text((W - 8 - d.textlength(time.strftime("%a %d %b", t), font=self.f_s), 8),
               time.strftime("%a %d %b", t), font=self.f_s, fill=INK2)
        d.line([0, 26, W, 26], fill=GRID)

        # CPU: overall % + per-core mini bars
        cpu = psutil.cpu_percent() / 100.0
        per = [p / 100.0 for p in psutil.cpu_percent(percpu=True)]
        d.text((8, 32), f"CPU {cpu * 100:4.0f}%", font=self.f, fill=INK)
        cx = 92
        bw = (W - cx - 8) / max(1, len(per))
        for i, p in enumerate(per):
            x = int(cx + i * bw)
            _bar(d, x, 34, int(bw) - 2, 14, p, _heat(p))
        try:
            la = psutil.getloadavg()
            d.text((8, 50), f"load {la[0]:.2f} {la[1]:.2f} {la[2]:.2f}", font=self.f_s, fill=MUTED)
        except (OSError, AttributeError):
            pass

        # Memory + swap
        vm = psutil.virtual_memory()
        d.text((8, 70), f"RAM {vm.percent:3.0f}%  {vm.used / 1e9:.1f}/{vm.total / 1e9:.1f} GB", font=self.f, fill=INK)
        _bar(d, 8, 88, W - 16, 12, vm.percent / 100.0, _heat(vm.percent / 100.0))

        # Disk (root)
        du = psutil.disk_usage("/")
        d.text((8, 108), f"Disk {du.percent:3.0f}%  {du.free / 1e9:.0f} GB free", font=self.f, fill=INK)
        _bar(d, 8, 126, W - 16, 12, du.percent / 100.0, _heat(du.percent / 100.0))

        # Network throughput (delta since last render)
        io = psutil.net_io_counters()
        nt = time.time()
        if self._net is not None and nt > self._net_t:
            dt = nt - self._net_t
            self._rx = (io.bytes_recv - self._net.bytes_recv) / dt
            self._tx = (io.bytes_sent - self._net.bytes_sent) / dt
        self._net, self._net_t = io, nt
        d.text((8, 148), f"net  down {_rate(self._rx)}   up {_rate(self._tx)}", font=self.f, fill=INK)

        # CPU temperature (if exposed)
        temp = None
        try:
            temps = psutil.sensors_temperatures()
            for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
                if temps.get(key):
                    temp = temps[key][0].current
                    break
            if temp is None and temps:
                temp = next(iter(temps.values()))[0].current
        except (AttributeError, OSError):
            pass
        if temp is not None:
            col = OK if temp < 65 else WARN if temp < 85 else BAD
            d.text((8, 170), f"CPU temp {temp:.0f} °C", font=self.f, fill=col)

        up = time.time() - psutil.boot_time()
        days, rem = divmod(int(up), 86400)
        hh, mm = divmod(rem // 60, 60)
        d.text((8, 192), f"uptime {days}d {hh}h {mm}m", font=self.f_s, fill=MUTED)
        d.text((8, 224), "Ctrl+Alt+U  ·  B to close", font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()
