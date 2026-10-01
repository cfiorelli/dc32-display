"""Locate the terminal dashboard that reports GitHub self-hosted runner cost/status."""
from __future__ import annotations

import os
import re
import sys

from . import config as CFG

FAV = "GitHub Runner Dashboard"
WORDS = {"cost": 30, "billing": 25, "dashboard": 25, "spend": 15, "usage": 8, "minutes": 5,
         "runner": 20, "actions": 6, "github": 10, "gha": 8}
SCRIPT_EXT = (".py", ".js", ".mjs", ".ts", ".sh", ".ps1", ".go", ".rb", ".rs", ".bat", ".cmd")
# Python dashboards run on the host's venv interpreter, which ships a current `rich` (Ubuntu 22.04's
# python3-rich 11.2 lacks Table.add_section and crashes the runner cost dashboard).
INTERP = {".py": [sys.executable if "python" in os.path.basename(sys.executable).lower() else "python"],
          ".js": ["node"], ".mjs": ["node"], ".ts": ["npx", "tsx"], ".sh": ["bash"],
          ".ps1": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"], ".rb": ["ruby"],
          ".bat": ["cmd", "/c"], ".cmd": ["cmd", "/c"]}


def _score(text: str) -> int:
    t = text.lower()
    s = sum(v for k, v in WORDS.items() if k in t)
    # the dashboard should talk about cost-ish things AND runners
    if not any(k in t for k in ("cost", "billing", "spend", "dashboard", "usage")):
        s //= 3
    return s


def _process_candidates():
    out = []
    try:
        import psutil
    except ImportError:
        return out
    me = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            if p.pid == me:
                continue
            cmd = p.info["cmdline"] or []
            text = " ".join(cmd)
            if not text or "dc32host" in text:
                continue
            if re.search(r"Runner\.(Listener|Worker)", text):
                out.append({"kind": "runner", "score": 1, "desc": f"runner process pid {p.pid}: {text[:120]}",
                            "pid": p.pid, "cwd": p.info.get("cwd")})
                continue
            sc = _score(text)
            if sc >= 40:
                script = next((a for a in cmd[1:] if a.lower().endswith(SCRIPT_EXT)), None)
                rx = re.escape(os.path.basename(script)) if script else re.escape(os.path.basename(cmd[0]))
                out.append({"kind": "process", "score": sc + 20, "desc": f"pid {p.pid}: {text[:140]}",
                            "match": {"cmdline": rx},
                            "launch": {"cmd": cmd, "cwd": p.info.get("cwd"), "title": FAV, "terminal": True}})
        except Exception:
            continue
    return out


def _window_candidates():
    out = []
    try:
        from .daemon import make_backend
        be = make_backend(CFG.load())
        for w in be.list_windows():
            sc = _score(w.title)
            if sc >= 40:
                out.append({"kind": "window", "score": sc + 10, "desc": f"window '{w.title}' ({w.app})",
                            "match": {"title": "^" + re.escape(w.title) + "$"}})
    except Exception:
        pass
    return out


def _search_roots(runner_dirs):
    home = os.path.expanduser("~")
    roots = [home] + [os.path.join(home, d) for d in ("src", "code", "repos", "projects", "dev", "git", "Documents", "Documents/GitHub", "work")]
    roots += list(runner_dirs)
    if sys.platform == "win32":
        roots += ["C:\\actions-runner", "C:\\runners", "D:\\actions-runner", "C:\\src", "C:\\dev"]
    else:
        roots += ["/opt", "/srv", "/home/runner"]
    seen, out = set(), []
    for r in roots:
        r = os.path.abspath(r)
        if r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


def _file_candidates(runner_dirs, max_files=40000):
    out, n = [], 0
    skip = {"node_modules", ".git", "venv", ".venv", "__pycache__", "AppData", "Library", ".cache", "_work", "externals", "bin", "obj", "dist"}
    seen = set()
    for root in _search_roots(runner_dirs):
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in skip and not d.startswith(".")]
            if dp.count(os.sep) - base_depth >= 4:
                dns[:] = []
            for fn in fns:
                n += 1
                if n > max_files:
                    return out
                low = fn.lower()
                if not low.endswith(SCRIPT_EXT) or not re.search(r"cost|billing|dashboard|spend|runner|usage", low):
                    continue
                path = os.path.join(dp, fn)
                if path in seen:
                    continue
                seen.add(path)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        body = f.read(200_000).lower()
                except OSError:
                    continue
                if "runner" not in body or not re.search(r"cost|billing|spend|\$|minutes", body):
                    continue
                sc = _score(fn) + min(30, body.count("runner")) + (15 if re.search(r"curses|rich|blessed|textual|tui|clear|\\x1b\[", body) else 0)
                ext = os.path.splitext(low)[1]
                launch = None
                if ext in INTERP:
                    launch = {"cmd": INTERP[ext] + [path], "cwd": dp, "title": FAV, "terminal": True}
                out.append({"kind": "file", "score": sc, "desc": path,
                            "match": {"title": "^" + re.escape(FAV) + "$", "cmdline": re.escape(fn)}, "launch": launch})
    return out


def find_candidates():
    procs = _process_candidates()
    runner_dirs = [c["cwd"] for c in procs if c["kind"] == "runner" and c.get("cwd")]
    cands = [c for c in procs if c["kind"] != "runner"] + _window_candidates() + _file_candidates(runner_dirs)
    cands.sort(key=lambda c: -c["score"])
    return cands + [c for c in procs if c["kind"] == "runner"]


def write_favorite(c: dict):
    cfg = CFG.load()
    favs = cfg.setdefault("favorites", [])
    fav = next((f for f in favs if f.get("name") == FAV), None)
    if fav is None:
        fav = {"name": FAV}
        favs.append(fav)
    m = {"title": None, "process": None, "cmdline": None}
    m.update(c.get("match") or {})
    # keep matching windows we launch ourselves (they carry the favorite's title)
    if not m.get("title"):
        m["title"] = "^" + re.escape(FAV) + "$"
    fav["match"] = m
    if c.get("launch"):
        fav["launch"] = c["launch"]
    CFG.save(cfg)
