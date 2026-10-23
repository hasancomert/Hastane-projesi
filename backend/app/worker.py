"""Çözücü işçi süreci (spawn): motorun önbelleklerini (yapı, model, Lagrange çarpanları, önceki çözümler) elinde tutar ve
işleri SIRAYLA çalıştırır. FastAPI süreci yanıt vermeye devam eder (Lagrange döngüsü GIL'i tutar; çözüm sunucu
sürecinde çalışmaz — Faz 3 raporu §11.8).

İletişim (multiprocessing kuyrukları, hepsi bu işçi örneğine özgü):
  req_q  sunucu → işçi: ("job", iş_kimliği, tür, yük) | ("shutdown",)
  ctl_q  sunucu → işçi: ("cancel", iş_kimliği) | ("shutdown",)      — denetim dinleyici iş parçacığı okur
  evt_q  işçi → sunucu: ("ready", pid, zaman) | ("hb", zaman, iş_kimliği) | ("started", iş, zaman)
                        | ("progress", iş, olay) | ("result", iş, sonuç, iptal_edildi_mi) | ("error", iş, mesaj, ayrıntı)
                        | ("bye", pid)

Sahipsiz kalmama: bekçi iş parçacığı ebeveyni (multiprocessing.parent_process(); Windows'ta süreç tutamağı) izler,
ebeveyn ölürse os._exit ile hemen çıkar. Motorun kendi alt süreçleri (çekirdek MIP / tam MILP) de aynı yöntemle bu
süreci izler (backend/model/milp.py), yani zincir aşağı doğru kendiliğinden kapanır. İşçi daemon DEĞİLDİR: daemon
süreçler alt süreç açamaz, motor ise açar.

Ctrl+C (Windows'ta konsoldaki tüm süreçlere gider) işçide yok sayılır; kapatmayı sunucu ("shutdown") yönetir.

İletiler evt_q'ya pickle'lanmış BAYT olarak konur (pickle bu iş parçacığında, eşzamanlı yapılır): multiprocessing.Queue
nesneyi besleyici iş parçacığında pickle'lar ve hata olursa ileti sessizce kaybolur (iş 'running' kalır, kuyruk bekçi
süresine dek durur — Faz 4 incelemesi, bulgu 5). Sonuç ayrıca JSON'a çevrilebilirlik için denetlenir (önbellek ve HTTP
yanıtı orjson kullanır); aktarılamayan sonuç Türkçe bir 'error' iletisine dönüşür.

Isınma: her iş türünün warmup()'ı 'hazır'dan önce çalışır ve isteğe bağlı olarak ertelenmiş adımlar (çağrılabilirler)
döndürür; bunlar işçi boşken birer birer çalışır (ör. 5 ve 2 km yapıları). Yeni iş en çok bir adımın süresi kadar bekler.
"""
from __future__ import annotations

import os
import pickle
import queue as _queue
import signal
import sys
import threading
import time
import traceback

import orjson

RESULT_FAIL_TR = ("Sonuç sunucuya aktarılamadı: iş türü JSON'a çevrilemeyen bir değer döndürdü ({err}). Bu bir "
                  "program hatasıdır; lütfen bildirin.")


def encode(msg: tuple) -> bytes:
    """İletiyi bu iş parçacığında pickle'lar (hata çağırana yükselir)."""
    return pickle.dumps(msg, protocol=pickle.HIGHEST_PROTOCOL)


def decode(raw) -> tuple:
    """Sunucu tarafı: işçinin bayt iletisini çözer (eski biçim: doğrudan demet)."""
    return pickle.loads(raw) if isinstance(raw, (bytes, bytearray)) else raw


def _console_utf8() -> None:
    """Çıktı yönlendirilmiş ve kod sayfası Türkçe değilse (ör. cp1252) yazdırma hatası olmasın (bulgu 10)."""
    for st in (sys.stdout, sys.stderr):
        try:
            if st is not None and not st.isatty() and (st.encoding or "").lower().replace("-", "") != "utf8":
                st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def worker_main(req_q, ctl_q, evt_q, opts: dict) -> None:
    """İşçi sürecinin giriş noktası (modül düzeyinde: spawn ile içe aktarılabilir)."""
    import multiprocessing as mp

    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass
    _console_utf8()
    parent = mp.parent_process()
    hb_s = float(opts.get("heartbeat_s", 1.0))
    min_interval = float(opts.get("progress_min_interval_s", 0.2))
    lock = threading.Lock()
    cur: dict = {"job": None, "cancel": None, "ctx": None}
    precancel: set[str] = set()
    stopping = threading.Event()

    def put_raw(data: bytes) -> None:
        try:
            evt_q.put(data)
        except (ValueError, OSError, AssertionError):
            pass

    def put(msg: tuple) -> None:
        try:
            data = encode(msg)
        except Exception:
            traceback.print_exc()
            if msg[0] != "error":           # pickle'lanamayan ilerleme olayı: yalnız o olay düşer (günlüğe yazılır)
                return
            data = encode(("error", msg[1], str(msg[2]), repr(msg[3])[:2000]))   # hata iletisi kaybolmamalı
        put_raw(data)

    def parent_watch() -> None:
        while True:
            if parent is None:
                time.sleep(0.5)
                continue
            parent.join(0.25)               # ebeveyn ölürse hemen döner
            if not parent.is_alive():
                try:
                    evt_q.cancel_join_thread()
                finally:
                    os._exit(3)             # sahipsiz kalma: hemen çık (motorun alt süreçleri de kendini kapatır)

    def control() -> None:
        while True:
            try:
                msg = ctl_q.get()
            except (EOFError, OSError, ValueError):
                return
            kind = msg[0]
            if kind == "cancel":
                with lock:
                    if cur["job"] == msg[1] and cur["cancel"] is not None:
                        cur["cancel"].set()
                    else:
                        precancel.add(msg[1])
            elif kind == "shutdown":
                stopping.set()
                with lock:
                    if cur["cancel"] is not None:
                        cur["cancel"].set()
                return

    def heartbeat() -> None:
        """Kalp atışı (hb_s'de bir) ve bekleyen ilerleme olaylarının gecikmesiz gönderimi (min_interval'de bir)."""
        last_hb = 0.0
        while not stopping.is_set():
            with lock:
                j, ctx = cur["job"], cur.get("ctx")
            if ctx is not None:
                ctx.flush_stale()
            now = time.monotonic()
            if now - last_hb >= hb_s:
                put(("hb", time.time(), j))
                last_hb = now
            stopping.wait(min(min_interval, hb_s))

    for target, name in ((parent_watch, "ebeveyn-bekcisi"), (control, "denetim"), (heartbeat, "kalp-atisi")):
        threading.Thread(target=target, name=name, daemon=True).start()

    # motor modüllerini baştan yükle: ilk iş içe aktarma süresini ödemesin (Windows'ta soğuk içe aktarma birkaç sn)
    from . import tasks
    deferred: list = []
    for jt in tasks.load_all().values():
        if jt.warmup is not None:
            try:
                more = jt.warmup()
                if more:
                    deferred.extend(more)
            except Exception:
                traceback.print_exc()
    if opts.get("prespawn_highs"):
        try:
            from backend.model import milp
            milp.set_prespawn(True)         # yedek HiGHS alt süreci (çekirdek MIP açılış gecikmesi ödenmez)
        except Exception:
            traceback.print_exc()
    put(("ready", os.getpid(), time.time()))

    while not stopping.is_set():
        try:
            msg = req_q.get(timeout=0.05 if deferred else 0.5)
        except _queue.Empty:
            if deferred and not stopping.is_set():
                step = deferred.pop(0)
                try:
                    step()
                except Exception:
                    traceback.print_exc()
            continue
        except (EOFError, OSError, ValueError):
            break
        if msg[0] == "shutdown":
            break
        if msg[0] != "job":
            continue
        _, job_id, type_name, payload = msg
        ev = threading.Event()
        with lock:
            cur["job"], cur["cancel"] = job_id, ev
            if job_id in precancel:
                precancel.discard(job_id)
                ev.set()
        put(("started", job_id, time.time()))
        ctx = tasks.JobContext(job_id=job_id, cancel_event=ev, put=put, min_interval_s=min_interval)
        with lock:
            cur["ctx"] = ctx
        try:
            jt = tasks.get(type_name)
            result = jt.run(ctx, payload)
            ctx.flush()
            try:
                orjson.dumps(result, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS)
                blob = encode(("result", job_id, result, ev.is_set()))
            except Exception as e:
                put(("error", job_id, RESULT_FAIL_TR.format(err=f"{type(e).__name__}: {e}"), traceback.format_exc()))
            else:
                put_raw(blob)
        except tasks.JobError as e:
            put(("error", job_id, e.message_tr, e.detail))
        except Exception as e:
            put(("error", job_id, f"Beklenmeyen hata ({type(e).__name__}): {e}", traceback.format_exc()))
        finally:
            with lock:
                cur["job"], cur["cancel"], cur["ctx"] = None, None, None
    stopping.set()
    if opts.get("prespawn_highs"):
        try:
            from backend.model import milp
            milp.set_prespawn(False)        # yedek alt süreç ucunu kapatınca kendiliğinden çıkar
        except Exception:
            pass
    put(("bye", os.getpid()))
    try:
        evt_q.close()
        evt_q.join_thread()
    except (ValueError, OSError, AssertionError):
        pass
