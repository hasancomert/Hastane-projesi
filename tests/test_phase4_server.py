"""Faz 4 — gerçek sunucu (python -m backend.run, uvicorn, 127.0.0.1) üzerinden: SSE'nin gerçek HTTP'de akışla gelmesi
ve olay aralıkları, PMTiles Range (206), ham yol ile dizin dışına çıkma denemeleri, kibar kapanışta ve sunucu sert
öldürüldüğünde hiçbir alt sürecin (çözücü işçisi, HiGHS alt süreçleri, kaynak izleyici) kalmaması.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase4_server.py
"""
from __future__ import annotations

import http.client
import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

from .test_phase4_common import ROOT, descendants, kill_hard, parse_sse, pid_alive, wait_for, wait_ready

LONG = {"grid_km": 5, "time_limit": 60, "gap": 1e-6, "use_cache": False}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, tmp_path) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        env = {**os.environ, "HASTANE_CACHE_DIR": str(tmp_path / "cache"), "PYTHONUNBUFFERED": "1"}
        self.log = open(tmp_path / "server.log", "w+")
        self.proc = subprocess.Popen([sys.executable, "-m", "backend.run", "--port", str(self.port), "--no-browser",
                                      "--log-level", "warning"], cwd=str(ROOT), env=env, stdout=self.log,
                                     stderr=subprocess.STDOUT)
        self.http = httpx.Client(base_url=self.base, timeout=30)

    def get(self, path: str) -> dict:
        try:
            return self.http.get(path).json()
        except httpx.HTTPError:
            return {"state": "?", "solver_ready": False}

    def logtext(self) -> str:
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def close(self) -> None:
        self.http.close()
        if self.proc.poll() is None:
            kill_hard(self.proc.pid)
            self.proc.wait(10)
        self.log.close()


@pytest.fixture
def server(tmp_path):
    srv = Server(tmp_path)
    try:
        wait_ready(srv.get, 120)
        yield srv
    finally:
        srv.close()


def test_real_server_sse_range_traversal(server):
    h = server.get("/api/health")
    assert h["worker"]["pid"] in descendants(server.proc.pid)
    # SSE gerçek HTTP'de akışla
    jid = server.http.post("/api/solve", json={"grid_km": 5, "time_limit": 6, "use_cache": False}).json()["job_id"]
    t0 = time.monotonic()
    with server.http.stream("GET", f"/api/jobs/{jid}/events", timeout=60) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        evs = parse_sse(r.iter_lines())
    names = [e["event"] for e in evs]
    assert names[-1] == "result" and evs[-1]["data"]["result"]["check"]["ok"]
    first_prog = next(e["t"] for e in evs if e["event"] == "progress")
    assert evs[-1]["t"] - first_prog > 1.0 and first_prog - t0 < 1.5
    gaps = [b["t"] - a["t"] for a, b in zip(evs, evs[1:])]
    assert max(gaps) < 2.5                                            # ilerleme ya da 2 sn'lik kalp atışı
    # PMTiles Range
    r = server.http.get("/basemap/ankara.pmtiles", headers={"Range": "bytes=0-15"})
    assert r.status_code == 206 and r.content[:7] == b"PMTiles" and len(r.content) == 16
    # ham yolla dizin dışına çıkma (istemci yolu normalleştirmez)
    for raw in ("/basemap/../../requirements.txt", "/../../requirements.txt", "/basemap/fonts/../../../backend/run.py",
                "/basemap/..%2f..%2frequirements.txt", "/basemap/fonts/..\\..\\..\\requirements.txt",
                "/api/precomputed/../../requirements"):
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        conn.putrequest("GET", raw, skip_accept_encoding=True)
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        assert resp.status in (400, 404), (raw, resp.status)
        assert b"highspy" not in body and b"import" not in body, raw


def _long_job_with_children(server) -> tuple[str, list[int]]:
    """Uzun bir çözüm başlatır; çözücü işçisi ve (varsa) motorun HiGHS alt süreci görünene dek bekler."""
    jid = server.http.post("/api/solve", json=LONG).json()["job_id"]
    wpid = server.get("/api/health")["worker"]["pid"]
    wait_for(lambda: descendants(wpid), 30.0, 0.2)                    # çekirdek MIP alt süreci (≈3 sn sonra)
    tree = descendants(server.proc.pid)
    assert wpid in tree
    return jid, tree


@pytest.mark.skipif(sys.platform == "win32", reason="Windows'ta SIGTERM yok; sert öldürme testi aynı yolu denetler")
def test_graceful_shutdown_leaves_no_processes(server):
    jid, tree = _long_job_with_children(server)
    assert len(tree) >= 2, tree
    server.proc.send_signal(signal.SIGTERM)
    server.proc.wait(20)
    alive = [p for p in tree if not wait_for(lambda p=p: not pid_alive(p), 5.0)]
    assert not alive, f"kibar kapanıştan sonra kalan süreçler: {alive}\n{server.logtext()[-2000:]}"
    assert "leaked semaphore" not in server.logtext()


def test_hard_killed_server_leaves_no_processes(server):
    jid, tree = _long_job_with_children(server)
    kill_hard(server.proc.pid)
    server.proc.wait(10)
    alive = [p for p in tree if not wait_for(lambda p=p: not pid_alive(p), 5.0)]
    assert not alive, f"sunucu öldürüldükten sonra kalan süreçler: {alive}"


def test_binds_loopback_only(server):
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    try:                                    # varsayılan yolun yerel adresi (paket gönderilmez)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
            u.connect(("10.255.255.255", 1))
            ips.add(u.getsockname()[0])
    except OSError:
        pass
    ips = {ip for ip in ips if not ip.startswith("127.") and ip != "0.0.0.0"}
    if not ips:
        pytest.skip("döngü dışı IPv4 adresi yok")
    for ip in ips:
        with socket.socket() as s:
            s.settimeout(2)
            assert s.connect_ex((ip, server.port)) != 0, ip
