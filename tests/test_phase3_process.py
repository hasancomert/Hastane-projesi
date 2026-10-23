"""Faz 3 — alt süreç güvenliği (doğrulama F1): solve() çalıştıran ebeveyn süreç öldürülünce HiGHS alt süreci sahipsiz
kalmamalı; kibar durdurma son çözümü getirmeli; izole çözüm her durumda süreci biçmeli.

Ebeveyn, ayrı bir Python sürecinde (spawn ile uyumlu, giriş noktası korumalı betik) solve() çağırır; çekirdek MIP ya
da tam MILP alt süreci başlayınca ebeveyn SIGKILL (Windows'ta TerminateProcess) ile öldürülür ve alt sürecin birkaç
saniye içinde kalmadığı denetlenir.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase3_process.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from backend.model import instance, milp
from backend.model.heuristics import heuristic

ROOT = Path(__file__).resolve().parents[1]

PARENT_SCRIPT = r'''
import sys
sys.path.insert(0, {root!r})


def main():
    import multiprocessing as mp
    from backend.model.solve import solve
    seen = []

    def cb(ev):
        if seen:
            return
        pids = [p.pid for p in mp.active_children()]
        if pids:
            seen.append(1)
            print("CHILD " + " ".join(str(p) for p in pids), flush=True)

    solve({km}, time_limit=90, gap=1e-5, progress_cb=cb, **{kwargs!r})
    print("FINISHED", flush=True)


if __name__ == "__main__":
    main()
'''


def _pid_alive(pid: int) -> bool:
    """Süreç yaşıyor mu (zombi sayılmaz). Windows'ta os.kill(pid, 0) süreci öldürür; tasklist kullanılır."""
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("State:"):
                    return "Z" not in line.split()[1]
    except OSError:
        return False
    return True


@pytest.mark.parametrize("label,kwargs", [
    ("cekirdek_mip", {}),                                       # canlı akış: çekirdek MIP alt süreci
    ("tam_milp_kok_lp", {"mip": "full", "use_lagrange": False}),  # tam MILP: kök LP sırasında (kesme geri çağrısı yok)
])
def test_killed_parent_leaves_no_highs_child(tmp_path, label, kwargs):
    script = tmp_path / "ebeveyn.py"
    script.write_text(PARENT_SCRIPT.format(root=str(ROOT), km=5, kwargs=kwargs), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=str(tmp_path))
    pids: list[int] = []
    try:
        lines: list[str] = []

        def reader():
            for line in proc.stdout:
                lines.append(line)

        th = threading.Thread(target=reader, daemon=True)
        th.start()
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 90 and not any(ln.startswith("CHILD") for ln in lines):
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        child = [ln for ln in lines if ln.startswith("CHILD")]
        assert child, (label, lines, proc.stderr.read() if proc.poll() is not None else "")
        pids = [int(p) for p in child[0].split()[1:]]
        time.sleep(1.0)                                     # alt süreç çözümün ortasında
        assert all(_pid_alive(p) for p in pids)
        proc.kill()                                         # SIGKILL / TerminateProcess: temizlik kodu çalışmaz
        proc.wait(timeout=10)
        t_kill = time.perf_counter()
        while time.perf_counter() - t_kill < 5.0 and any(_pid_alive(p) for p in pids):
            time.sleep(0.05)
        alive = [p for p in pids if _pid_alive(p)]
        assert not alive, f"{label}: ebeveyn öldükten 5 sn sonra HiGHS alt süreci hâlâ çalışıyor: {alive}"
    finally:
        if proc.poll() is None:
            proc.kill()
        for p in pids:                                      # test başarısızsa bile artık bırakma
            if _pid_alive(p) and sys.platform != "win32":
                try:
                    os.kill(p, 9)
                except OSError:
                    pass


def test_graceful_stop_returns_incumbent_and_reaps():
    """Kök LP sırasında durdurma (HiGHS kesmeye yanıt vermez): bekçi son incumbent'la biter; süreç biçilir."""
    inst = instance.build_instance(5)
    h = heuristic(inst, time_budget=1.0)
    m = milp.MilpModel(inst)
    t = time.perf_counter()
    r = m.solve(time_limit=60, gap=1e-5, start=h.solution, isolate=True, deadline=time.perf_counter() + 1.5)
    wall = time.perf_counter() - t
    assert wall < 1.5 + 1.0, wall
    assert r.status == "Time limit reached" and r.solution is not None
    assert r.primal_tl == pytest.approx(h.objective_tl, rel=1e-9)
    run = m.start_isolated(time_limit=60, gap=1e-5, start=h.solution)
    pid = run.pid
    run.close()                                             # bitmeden kapatma: süreç her durumda sonlanır
    assert not run.proc.is_alive() and not _pid_alive(pid)
