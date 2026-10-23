"""Faz 4 incelemesi bulgu 5: işçinin aktaramadığı sonuç sessizce kaybolmamalı. multiprocessing.Queue nesneyi besleyici
iş parçacığında pickle'lar; hata olursa ileti kaybolur, iş 'running' kalır ve kuyruk bekçi süresine dek durur. İşçi artık
iletiyi kendi iş parçacığında pickle'lar ve sonucu JSON'a çevrilebilirlik için denetler; aktarılamayan sonuç Türkçe bir
'error' iletisi olur ve işçi sıradaki işe geçer.

İşçi gerçek bir spawn alt sürecinde çalışır; test iş türleri alt süreçte bu modül içe aktarılınca kaydedilir.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase4_worker.py
"""
from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time

from pydantic import BaseModel

from backend.app import tasks
from backend.app.worker import decode, worker_main


class _In(BaseModel):
    pass


def _prepare(req):
    return tasks.Prepared(payload={})


def _bad_pickle(ctx, payload):
    return {"ok": True, "f": threading.Lock()}            # pickle'lanamaz


def _bad_json(ctx, payload):
    return {"ok": True, "n": 10 ** 30}                     # pickle'lanır ama JSON'a (orjson) çevrilemez


def _good(ctx, payload):
    return {"ok": True, "n": 1}


def _bad_progress(ctx, payload):
    ctx.emit({"phase": "x", "lock": threading.Lock()}, force=True)     # yalnız bu olay düşer
    ctx.emit({"phase": "y"}, force=True)
    return {"ok": True}


def _entry(req_q, ctl_q, evt_q, opts):
    for name, fn in (("t_bad_pickle", _bad_pickle), ("t_bad_json", _bad_json), ("t_good", _good),
                     ("t_bad_progress", _bad_progress)):
        tasks.register(tasks.JobType(name=name, request_model=_In, prepare=_prepare, run=fn, description_tr="test"))
    worker_main(req_q, ctl_q, evt_q, opts)


def _next(evt_q, kinds, timeout=60.0):
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        try:
            msg = decode(evt_q.get(timeout=0.5))
        except queue.Empty:
            continue
        if msg[0] in kinds:
            return msg
    raise AssertionError(f"{kinds} iletisi gelmedi")


def test_untransferable_result_becomes_error_and_queue_moves_on():
    ctx = mp.get_context("spawn")
    req_q, ctl_q, evt_q = ctx.Queue(), ctx.Queue(), ctx.Queue()
    p = ctx.Process(target=_entry, args=(req_q, ctl_q, evt_q, {"heartbeat_s": 0.5}), daemon=False)
    p.start()
    try:
        assert _next(evt_q, ("ready",), 120)[0] == "ready"
        for jid, typ in (("j1", "t_bad_pickle"), ("j2", "t_bad_json"), ("j3", "t_good"), ("j4", "t_bad_progress")):
            req_q.put(("job", jid, typ, {}))
        m1 = _next(evt_q, ("result", "error"))
        assert m1[0] == "error" and m1[1] == "j1" and "aktarılamadı" in m1[2]
        m2 = _next(evt_q, ("result", "error"))
        assert m2[0] == "error" and m2[1] == "j2" and "aktarılamadı" in m2[2]
        m3 = _next(evt_q, ("result", "error"))
        assert m3[:3] == ("result", "j3", {"ok": True, "n": 1})
        phases = []
        while True:
            m = _next(evt_q, ("progress", "result", "error"))
            if m[0] == "progress":
                phases.append(m[2]["phase"])
                continue
            assert m[0] == "result" and m[1] == "j4"
            break
        assert phases == ["y"]
    finally:
        for q in (ctl_q, req_q):
            q.put(("shutdown",))
        p.join(10)
        if p.is_alive():
            p.kill()
            p.join(5)
    assert not p.is_alive()
