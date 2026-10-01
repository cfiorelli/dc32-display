"""USB transport: find the badge, open the vendor interface, read events on a thread, reconnect."""
from __future__ import annotations

import logging
import queue
import threading
import time

from . import protocol as P

log = logging.getLogger("dc32.usb")

EP_OUT = 0x01
EP_IN = 0x81
ITF = 0


def _backend():
    """libusb backend. On Windows the DLL ships with the `libusb-package` wheel."""
    try:
        import libusb_package  # type: ignore
        return libusb_package.get_libusb1_backend()
    except Exception:
        return None


class Disconnected(Exception):
    pass


class Badge:
    def __init__(self):
        self.dev = None
        self.info: P.Info | None = None
        self.events: "queue.Queue" = queue.Queue()
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._wlock = threading.Lock()
        self.bytes_out = 0
        self.last_rx = 0.0

    # ------------------------------------------------------------ discovery
    @staticmethod
    def find():
        import usb.core
        import usb.util
        be = _backend()
        for d in usb.core.find(find_all=True, idVendor=P.USB_VID, idProduct=P.USB_PID, backend=be) or []:
            try:
                prod = usb.util.get_string(d, d.iProduct)
            except Exception:
                prod = None
            if prod is None or prod == P.USB_PRODUCT:
                return d
        return None

    @property
    def connected(self) -> bool:
        return self.dev is not None

    def open(self, timeout=3.0) -> bool:
        import usb.util
        d = self.find()
        if d is None:
            return False
        try:
            try:
                if d.is_kernel_driver_active(ITF):
                    d.detach_kernel_driver(ITF)
            except Exception:
                pass
            try:
                d.set_configuration()
            except Exception:
                pass  # already configured (normal on Windows/WinUSB)
            usb.util.claim_interface(d, ITF)
            # out-of-band: flush badge RX + resync decoder, then SYNC in-band
            d.ctrl_transfer(0x40, P.CTRL_RESET_STREAM, 0, 0, None, timeout=1000)
            self.dev = d
            self._drain_in()
            self._stop.clear()
            self._reader = threading.Thread(target=self._read_loop, name="dc32-usb-rx", daemon=True)
            self._reader.start()
            self.write(P.SYNC_BYTES + P.hello())
            t0 = time.time()
            while time.time() - t0 < timeout:
                try:
                    ev = self.events.get(timeout=0.1)
                except queue.Empty:
                    continue
                if isinstance(ev, P.Info):
                    self.info = ev
                    log.info("badge connected: fw %s proto %d %dx%d", ev.fw, ev.proto, ev.width, ev.height)
                    if ev.proto != P.PROTO_VERSION:
                        log.warning("protocol mismatch: host %d badge %d", P.PROTO_VERSION, ev.proto)
                    return True
            raise Disconnected("no INFO reply")
        except Exception as e:
            log.warning("open failed: %s", e)
            self.close()
            return False

    def _drain_in(self):
        for _ in range(16):
            try:
                self.dev.read(EP_IN, 512, timeout=5)
            except Exception:
                break

    def close(self):
        self._stop.set()
        d, self.dev = self.dev, None
        if d is not None:
            try:
                import usb.util
                usb.util.release_interface(d, ITF)
                usb.util.dispose_resources(d)
            except Exception:
                pass
        if self._reader and self._reader is not threading.current_thread():
            self._reader.join(timeout=1)
        self._reader = None

    # ------------------------------------------------------------ io
    def write(self, data: bytes, timeout_ms: int = 2000):
        if self.dev is None:
            raise Disconnected()
        with self._wlock:
            try:
                n = self.dev.write(EP_OUT, data, timeout=timeout_ms)
            except Exception as e:
                self.events.put(("disconnected", str(e)))
                raise Disconnected(str(e))
        self.bytes_out += n
        return n

    def _read_loop(self):
        sp = P.StreamParser()
        while not self._stop.is_set():
            d = self.dev
            if d is None:
                break
            try:
                data = bytes(d.read(EP_IN, 512, timeout=200))
            except Exception as e:
                import usb.core
                if isinstance(e, usb.core.USBTimeoutError) or "timed out" in str(e).lower() or getattr(e, "errno", None) in (110, 10060):
                    continue
                if not self._stop.is_set():
                    self.events.put(("disconnected", str(e)))
                break
            self.last_rx = time.time()
            for mtype, payload in sp.feed(data):
                obj = P.parse(mtype, payload)
                if obj is not None:
                    self.events.put(obj)
