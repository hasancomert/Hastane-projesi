"""Sunucu tarafı iş yöneticisi: kuyruk, tek işçi sürecinin denetimi, ilerleme olaylarının SSE abonelerine dağıtımı.

  * İşler sunucuda kuyrukta bekler; işçiye her seferinde tek iş gönderilir (işçi boşalınca sıradaki). Böylece kuyruktaki
    bir işin iptali yalnız sunucuda olur ve işçi yeniden başlatılınca bekleyen işler kaybolmaz.
  * "En son istek kazanır": replace=true ile gelen istek aynı istemcinin aynı gruptaki (ör. solve) bekleyen ve çalışan
    işlerini iptal eder.
  * Kuyruk sınırı (toplam ve istemci başına): aşılırsa QueueFullError (HTTP 429).
  * Bekçi: işçi ölürse, kalp atışı kesilirse, iş beklenen süresini aşıp iptale de yanıt vermezse işçi öldürülür ve
    yeniden başlatılır; o anki iş Türkçe bir mesajla 'failed' olur, kuyruktaki işler yeni işçide sürer.
  * Okuyucu iş parçacığı işçinin olay kuyruğunu okur; olaylar iş geçmişine yazılır ve her SSE abonesinin asyncio
    kuyruğuna loop.call_soon_threadsafe ile iletilir.

İş durumları: queued → running → done | failed | cancelled.
"""
from __future__ import annotations

import asyncio
import atexit
import collections
import copy
import gc
import logging
import multiprocessing as mp
import queue as _queue
import threading
import time
import uuid

from pydantic import BaseModel

from . import tasks
from .cache import ResultCache
from .config import Settings
from .worker import decode, worker_main

log = logging.getLogger("hastane.jobs")

FINAL = ("done", "failed", "cancelled")
MAX_EVENTS_PER_JOB = 3000
STATE_TR = {"queued": "kuyrukta", "running": "çalışıyor", "done": "tamamlandı", "failed": "başarısız",
            "cancelled": "iptal edildi"}


class QueueFullError(Exception):
    def __init__(self, message_tr: str) -> None:
        super().__init__(message_tr)
        self.message_tr = message_tr


class NotAvailableError(Exception):
    """İş türü kayıtlı ama henüz hesaplanamıyor (ör. Faz 6'da doldurulacak taslak) → HTTP 501."""

    def __init__(self, message_tr: str) -> None:
        super().__init__(message_tr)
        self.message_tr = message_tr


class Job:
    def __init__(self, jt: tasks.JobType, prepared: tasks.Prepared, client_id: str | None) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.type = jt.name
        self.jt = jt
        self.prepared = prepared
        self.client_id = client_id
        self.state = "queued"
        self.created = time.time()
        self.started: float | None = None
        self.finished: float | None = None
        self.result: dict | None = None
        self.error: dict | None = None
        self.from_cache = False
        self.cache_entry: str | None = None
        self.cancel_requested = False
        self.cancel_reason_tr: str | None = None
        self.replaced_by: str | None = None
        self.deadline: float | None = None          # bekçi (monotonic)
        self.cancel_deadline: float | None = None   # iptal isteğine yanıt süresi (monotonic)
        self.watchdog_tripped = False
        self.progress: dict | None = None
        self.n_progress = 0
        self.result_evicted = False
        self.events: collections.deque = collections.deque(maxlen=MAX_EVENTS_PER_JOB)
        self.seq = 0
        self.subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []

    @property
    def final(self) -> bool:
        return self.state in FINAL

    def snapshot(self, *, include_result: bool = True, position: int | None = None) -> dict:
        now = time.time()
        end = self.finished or now
        out = {
            "job_id": self.id, "type": self.type, "state": self.state, "state_tr": STATE_TR.get(self.state),
            "client_id": self.client_id, "created_at": self.created, "started_at": self.started,
            "finished_at": self.finished,
            "queued_s": round(((self.started or end) - self.created), 3),
            "run_s": round(end - self.started, 3) if self.started else None,
            "from_cache": self.from_cache, "cancel_requested": self.cancel_requested,
            "cancel_reason_tr": self.cancel_reason_tr, "replaced_by": self.replaced_by,
            "position": position, "progress": self.progress, "n_progress": self.n_progress,
            "info": self.prepared.info, "warnings": list(self.prepared.warnings), "error": self.error,
            "has_result": self.result is not None, "result_evicted": self.result_evicted,
            "events_url": f"/api/jobs/{self.id}/events", "cancel_url": f"/api/jobs/{self.id}/cancel",
        }
        if include_result:
            out["result"] = self.result
        return out


class _Worker:
    """Bir işçi süreci örneği (yeniden başlatmada yenisi kurulur; kuyruklar örneğe özgüdür)."""

    def __init__(self, incarnation: int, settings: Settings) -> None:
        ctx = mp.get_context("spawn")
        self.incarnation = incarnation
        self.req_q = ctx.Queue()
        self.ctl_q = ctx.Queue()
        self.evt_q = ctx.Queue()
        opts = {"heartbeat_s": settings.heartbeat_s, "progress_min_interval_s": settings.sse_progress_min_interval_s,
                "prespawn_highs": settings.prespawn_highs}
        self.proc = ctx.Process(target=worker_main, args=(self.req_q, self.ctl_q, self.evt_q, opts),
                                name=f"hastane-cozucu-{incarnation}", daemon=False)
        self.ready = False
        self.pid: int | None = None
        self.current: str | None = None
        self.started_at = time.monotonic()
        self.ready_at: float | None = None
        self.last_hb = time.monotonic()
        self.reader: threading.Thread | None = None
        self.closed = False

    def start(self) -> None:
        self.proc.start()
        self.pid = self.proc.pid

    def send_job(self, job: Job) -> None:
        self.req_q.put(("job", job.id, job.type, job.prepared.payload))

    def send_cancel(self, job_id: str) -> None:
        try:
            self.ctl_q.put(("cancel", job_id))
        except (ValueError, OSError, AssertionError, AttributeError):
            pass

    def alive(self) -> bool:
        return self.proc.is_alive()

    def kill(self) -> None:
        """Süreci öldürür (Windows'ta TerminateProcess); motorun alt süreçleri ebeveyn bekçileriyle kendiliğinden kapanır."""
        try:
            if self.proc.is_alive():
                self.proc.kill()
            self.proc.join(5.0)
        except (ValueError, OSError, AssertionError):
            pass
        self.close_queues()

    def stop(self, grace: float) -> None:
        """Kibar kapanış: kapan iletisi, bekle, gerekirse öldür."""
        for q in (self.ctl_q, self.req_q):
            try:
                q.put(("shutdown",))
            except (ValueError, OSError, AssertionError, AttributeError):
                pass
        try:
            self.proc.join(grace)
        except (ValueError, OSError, AssertionError):
            pass
        self.kill()

    def close_queues(self) -> None:
        """Kuyrukları kapatır ve bırakır: semaforları çöp toplayıcıda serbest kalır (POSIX'te kaynak izleyici
        'sızan semafor' uyarısı vermez; uvicorn kapanışta sinyali yeniden yükselttiği için atexit çalışmayabilir)."""
        if self.closed:
            return
        self.closed = True
        if self.reader is not None and self.reader is not threading.current_thread():
            self.reader.join(1.0)
        for q in (self.req_q, self.ctl_q, self.evt_q):
            try:
                q.cancel_join_thread()
                q.close()
            except (ValueError, OSError, AssertionError, AttributeError):
                pass
        # besleyici iş parçacıkları kuyruğun kilit/semaforlarını tutar: bitmeden süreç (uvicorn'un yeniden yükselttiği
        # SIGTERM ile) ölürse semaforlar serbest kalmaz ve kaynak izleyici "leaked semaphore" uyarısı verir
        # (gözlem: kapanışta iptal iletisi giden ctl_q'nun _wlock/_sem'i). close() bitiş işaretini koyduğu için
        # iş parçacığı hemen çıkar; okuyan taraf ölmüşse yazma hatayla biter.
        for q in (self.req_q, self.ctl_q, self.evt_q):
            th = getattr(q, "_thread", None)
            if th is not None and th is not threading.current_thread():
                th.join(1.0)
        self.req_q = self.ctl_q = self.evt_q = None


class JobManager:
    def __init__(self, settings: Settings, cache: ResultCache | None) -> None:
        self.settings = settings
        self.cache = cache
        self._lock = threading.RLock()
        self.jobs: collections.OrderedDict[str, Job] = collections.OrderedDict()
        self.queue: collections.deque[str] = collections.deque()
        self.worker: _Worker | None = None
        self.incarnation = 0
        self.restarts = 0
        self.restart_log: list[dict] = []
        self._stopping = False
        self._monitor: threading.Thread | None = None
        self._started = time.time()
        tasks.load_all()

    # ------------------------------------------------------------------ yaşam döngüsü
    def start(self) -> None:
        if self.settings.start_worker:
            with self._lock:
                self._spawn_locked()
            self._monitor = threading.Thread(target=self._monitor_loop, name="is-bekcisi", daemon=True)
            self._monitor.start()
        atexit.register(self._atexit)

    def _spawn_locked(self) -> None:
        self.incarnation += 1
        w = _Worker(self.incarnation, self.settings)
        w.start()
        self.worker = w
        w.reader = threading.Thread(target=self._reader_loop, args=(w,), name=f"isci-okuyucu-{w.incarnation}",
                                    daemon=True)
        w.reader.start()
        log.info("çözücü işçisi başlatıldı (pid %s, örnek %d)", w.pid, w.incarnation)

    def stop(self) -> None:
        """Sunucu kapanırken: bekleyen işler iptal, çalışan iş durdurulur, işçi kibarca kapatılır (gerekirse öldürülür)."""
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
            for jid in list(self.queue):
                job = self.jobs.get(jid)
                if job is not None and not job.final:
                    self._finish_locked(job, "cancelled", error={"message_tr": "Sunucu kapanıyor; iş iptal edildi."})
            self.queue.clear()
            w = self.worker
            if w is not None and w.current is not None:
                job = self.jobs.get(w.current)
                if job is not None:
                    job.cancel_requested = True
                    job.cancel_reason_tr = "Sunucu kapanıyor; çalışan iş durduruldu."
                w.send_cancel(w.current)
        if w is not None:
            w.stop(self.settings.shutdown_grace_s)
            with self._lock:
                if w.current is not None:
                    job = self.jobs.get(w.current)
                    if job is not None and not job.final:
                        self._finish_locked(job, "cancelled",
                                            error={"message_tr": "Sunucu kapandı; çalışan iş durduruldu."})
                    w.current = None
        if self._monitor is not None:
            self._monitor.join(2.0)
        gc.collect()                                # bırakılan kuyrukların semaforları hemen serbest kalsın

    def _atexit(self) -> None:
        w = self.worker
        if w is not None:
            try:
                if w.proc.is_alive():
                    w.proc.kill()
                    w.proc.join(3.0)
            except Exception:
                pass

    # ------------------------------------------------------------------ iş gönderme
    def submit(self, type_name: str, request: BaseModel, *, client_id: str | None = None, replace: bool = False,
               use_cache: bool = True) -> tuple[Job, list[str]]:
        jt = tasks.get(type_name)
        if replace and not client_id:
            raise tasks.ApiValidationError(
                [{"loc": ["body", "client_id"], "key": None, "type": "eksik",
                  "message_tr": "replace=true için client_id gerekli (hangi istemcinin işinin yerine geçileceği)."}],
                "replace=true istemci kimliği olmadan kullanılamaz.")
        if not jt.available:
            raise NotAvailableError(jt.unavailable_tr or f"'{jt.name}' iş türü henüz kullanılamıyor.")
        prepared = jt.prepare(request)              # ApiValidationError → 422 (çağıran yakalar)
        cached = None
        if use_cache and prepared.cache_key and self.cache is not None:
            hit = self.cache.lookup(prepared.cache_key, prepared.variant, jt.satisfies, jt.rank)
            if hit is not None:
                res, entry = hit
                if jt.finalize is not None:
                    res = jt.finalize(res, self)
                cached = (res, entry)
        replaced: list[str] = []
        with self._lock:
            if self._stopping:
                raise QueueFullError("Sunucu kapanıyor; yeni iş kabul edilmiyor.")
            job = Job(jt, prepared, client_id)
            to_replace = [o for o in self.jobs.values() if replace and client_id and o.client_id == client_id
                          and o.jt.group == jt.group and not o.final]
            if cached is None:
                # sınır, yerini alacağı işler düşülerek denetlenir (reddedilen istek eski işleri iptal etmez)
                gone = {o.id for o in to_replace}
                queued = [self.jobs[j] for j in self.queue if j in self.jobs and self.jobs[j].state == "queued"
                          and j not in gone]
                if len(queued) >= self.settings.max_queued:
                    raise QueueFullError(f"Kuyruk dolu: en çok {self.settings.max_queued} iş bekleyebilir. "
                                         f"Lütfen biraz sonra yeniden deneyin ya da bekleyen bir işi iptal edin.")
                if client_id and sum(1 for j in queued if j.client_id == client_id) >= \
                        self.settings.max_queued_per_client:
                    raise QueueFullError(f"Bu istemcinin kuyrukta {self.settings.max_queued_per_client} işi var; "
                                         f"yeni iş için birinin bitmesini bekleyin ya da replace=true gönderin.")
            for other in to_replace:
                self._cancel_locked(other, "Aynı istemciden gelen yeni istek bu işin yerini aldı.", replaced_by=job.id)
                replaced.append(other.id)
            self.jobs[job.id] = job
            if cached is not None:
                res, entry = cached
                job.from_cache = True
                job.cache_entry = entry.name
                job.started = job.created
                self._publish_locked(job, "state", {"state": "queued"})
                self._finish_locked(job, "done", result=res)
            else:
                self.queue.append(job.id)
                self._publish_locked(job, "state", {"state": "queued", "position": self._position_locked(job)})
                self._dispatch_locked()
            self._prune_locked()
        return job, replaced

    def cancel(self, job_id: str) -> Job | None:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if not job.final:
                self._cancel_locked(job, "Kullanıcı iptal etti.")
            return job

    def _cancel_locked(self, job: Job, reason_tr: str, replaced_by: str | None = None) -> None:
        job.cancel_requested = True
        job.cancel_reason_tr = reason_tr
        if replaced_by:
            job.replaced_by = replaced_by
        if job.state == "queued":
            try:
                self.queue.remove(job.id)
            except ValueError:
                pass
            self._finish_locked(job, "cancelled", error={"message_tr": reason_tr})
        elif job.state == "running":
            w = self.worker
            if w is not None and w.current == job.id:
                w.send_cancel(job.id)
            now = time.monotonic()
            if job.jt.cooperative_cancel:
                job.cancel_deadline = now + self.settings.cancel_grace_s
            else:
                # iptali izlemeyen iş türü: işçiyi öldürüp tüm sıcak önbellekleri silmek yerine iş kendi bekçi süresine
                # dek bitebilir (Faz 4 incelemesi, bulgu 2)
                job.cancel_deadline = max(job.deadline or now, now + self.settings.cancel_grace_s)
            self._publish_locked(job, "state", {"state": "running", "cancel_requested": True,
                                                "message_tr": reason_tr})

    # ------------------------------------------------------------------ sorgu
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)

    def snapshot(self, job_id: str, include_result: bool = True) -> dict | None:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            return job.snapshot(include_result=include_result, position=self._position_locked(job))

    def _position_locked(self, job: Job) -> int | None:
        """Önündeki iş sayısı (çalışan iş dahil); kuyrukta değilse None."""
        if job.state != "queued":
            return None
        try:
            idx = self.queue.index(job.id)
        except ValueError:
            return None
        busy = 1 if (self.worker is not None and self.worker.current is not None) else 0
        return idx + busy

    def health(self) -> dict:
        with self._lock:
            w = self.worker
            now = time.monotonic()
            states = collections.Counter(j.state for j in self.jobs.values())
            return {
                "worker": None if w is None else {
                    "pid": w.pid, "alive": w.alive(), "ready": w.ready, "incarnation": w.incarnation,
                    "current_job": w.current, "uptime_s": round(now - w.started_at, 2),
                    "ready_after_s": round(w.ready_at - w.started_at, 2) if w.ready_at is not None else None,
                    "last_heartbeat_age_s": round(now - w.last_hb, 2),
                },
                "restarts": self.restarts, "restart_log": self.restart_log[-10:],
                "queue": {"queued": len(self.queue), "max_queued": self.settings.max_queued,
                          "max_queued_per_client": self.settings.max_queued_per_client},
                "jobs": dict(states),
                "job_types": sorted(tasks.REGISTRY),
                "cache": self.cache.info() if self.cache is not None else None,
            }

    # ------------------------------------------------------------------ SSE aboneliği
    def subscribe(self, job_id: str, loop: asyncio.AbstractEventLoop, after_id: int = 0
                  ) -> tuple[asyncio.Queue, list[dict], bool] | None:
        """(kuyruk, geçmiş olaylar, iş bitti mi). Geçmiş ve abonelik aynı kilit altında: olay kaçmaz."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            backlog = [e for e in job.events if e["id"] > after_id]
            q: asyncio.Queue = asyncio.Queue()
            if not job.final:
                job.subscribers.append((loop, q))
            return q, backlog, job.final

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is not None:
                job.subscribers = [(lp, qq) for lp, qq in job.subscribers if qq is not q]

    def _publish_locked(self, job: Job, event: str, data: dict) -> None:
        job.seq += 1
        item = {"id": job.seq, "event": event, "data": data}
        job.events.append(item)
        keep = []
        for loop, q in job.subscribers:
            try:
                loop.call_soon_threadsafe(q.put_nowait, item)
                keep.append((loop, q))
            except RuntimeError:                    # döngü kapanmış (istemci gitmiş)
                pass
        job.subscribers = keep

    # ------------------------------------------------------------------ dağıtım ve bitiş
    def _dispatch_locked(self) -> None:
        w = self.worker
        if self._stopping or w is None or not w.ready or w.current is not None:
            return
        while self.queue:
            jid = self.queue.popleft()
            job = self.jobs.get(jid)
            if job is None or job.state != "queued":
                continue
            job.state = "running"
            job.started = time.time()
            job.deadline = time.monotonic() + job.prepared.max_runtime_s + self.settings.watchdog_extra_s
            w.current = job.id
            try:
                w.send_job(job)
            except Exception as ex:
                w.current = None
                self._finish_locked(job, "failed", error={"message_tr": f"İş çözücüye gönderilemedi: {ex}"})
                continue
            self._publish_locked(job, "state", {"state": "running"})
            for other_id in self.queue:             # bekleyenlerin sırası değişti
                other = self.jobs.get(other_id)
                if other is not None and other.state == "queued":
                    self._publish_locked(other, "state", {"state": "queued", "position": self._position_locked(other)})
            return

    def _finish_locked(self, job: Job, state: str, *, result: dict | None = None, error: dict | None = None) -> None:
        job.state = state
        job.finished = time.time()
        if result is not None:
            job.result = result
        if error is not None:
            job.error = error
        if job.result is not None:
            data = {"state": state, "from_cache": job.from_cache, "job_id": job.id, "type": job.type,
                    "cancel_reason_tr": job.cancel_reason_tr if state == "cancelled" else None,
                    "error": job.error, "result": job.result}
            self._publish_locked(job, "result", data)
        else:
            self._publish_locked(job, "error", {"state": state, "job_id": job.id, "type": job.type,
                                                "message_tr": (job.error or {}).get("message_tr"),
                                                "detail": (job.error or {}).get("detail")})
        job.subscribers = []

    def _prune_locked(self) -> None:
        """Bellek sınırı: en eski bitmiş işler silinir; son max_results_kept dışındakilerin sonucu bırakılır
        (iş özeti kalır, result_evicted=true; sonuç gerekiyorsa aynı istek önbellekten anında döner)."""
        finals = sorted((j for j in self.jobs.values() if j.final), key=lambda j: j.finished or 0.0)
        extra = len(finals) - self.settings.max_jobs_kept
        for j in finals:
            if extra <= 0:
                break
            if not j.subscribers:
                del self.jobs[j.id]
                extra -= 1
        keep = self.settings.max_results_kept
        with_result = [j for j in finals if j.id in self.jobs and j.result is not None]
        for j in with_result[:max(0, len(with_result) - keep)]:
            if j.subscribers:
                continue
            j.result = None
            j.result_evicted = True
            for e in j.events:
                if e["event"] == "result":
                    e["data"] = {**e["data"], "result": None, "result_evicted": True}

    def list_jobs(self, client_id: str | None = None, limit: int = 50) -> list[dict]:
        with self._lock:
            jobs = [j for j in reversed(self.jobs.values()) if client_id is None or j.client_id == client_id]
            return [j.snapshot(include_result=False, position=self._position_locked(j)) for j in jobs[:limit]]

    # ------------------------------------------------------------------ işçi olayları
    def _reader_loop(self, w: _Worker) -> None:
        while True:
            if self.worker is not w or w.closed:
                return
            try:
                raw = w.evt_q.get(timeout=0.25)
            except _queue.Empty:
                continue
            except (EOFError, OSError, ValueError, AttributeError):
                return
            try:
                msg = decode(raw)
            except Exception:
                log.exception("işçi iletisi çözülemedi")
                continue
            try:
                self._on_message(w, msg)
            except Exception:
                log.exception("işçi iletisi işlenemedi: %r", msg[:2] if isinstance(msg, tuple) else msg)

    def _on_message(self, w: _Worker, msg: tuple) -> None:
        kind = msg[0]
        if kind == "result":
            _, job_id, result, cancelled = msg
            self._on_result(w, job_id, result, cancelled)
            return
        with self._lock:
            if self.worker is not w:
                return
            w.last_hb = time.monotonic()
            if kind == "ready":
                w.ready, w.ready_at = True, time.monotonic()
                self._dispatch_locked()
            elif kind == "progress":
                job = self.jobs.get(msg[1])
                if job is not None and job.state == "running":
                    job.progress = msg[2]
                    job.n_progress += 1
                    self._publish_locked(job, "progress", msg[2])
            elif kind == "error":
                _, job_id, message_tr, detail = msg
                job = self.jobs.get(job_id)
                if w.current == job_id:
                    w.current = None
                if job is not None and not job.final:
                    state = "cancelled" if job.cancel_requested and not job.watchdog_tripped else "failed"
                    self._finish_locked(job, state, error={"message_tr": message_tr, "detail": detail})
                self._dispatch_locked()
            elif kind == "bye":
                w.ready = False

    def _on_result(self, w: _Worker, job_id: str, result: dict, cancelled: bool) -> None:
        with self._lock:
            job = self.jobs.get(job_id)
        if job is None:
            res2 = result
        else:
            jt = job.jt
            stopped = cancelled or result.get("status") == "durduruldu"
            # önbellek (durdurulan ya da başarısız sonuçlar yazılmaz) ve sunucu tarafı son işlem — kilit dışında
            if (self.cache is not None and job.prepared.cache_key and not stopped
                    and _safe_bool(jt.cache_result, result)):
                variant = dict(job.prepared.variant)
                try:
                    variant.update(jt.summarize(result) or {})
                except Exception:
                    pass
                self.cache.put(job.prepared.cache_key, variant, result, job_type=jt.name)
            res2 = result
            if jt.finalize is not None:
                try:
                    res2 = jt.finalize(copy.copy(result), self)
                except Exception:
                    log.exception("sonuç son işlemi başarısız")
        with self._lock:
            if self.worker is w:
                w.last_hb = time.monotonic()
                if w.current == job_id:
                    w.current = None
            if job is not None and not job.final:
                stopped = cancelled or res2.get("status") == "durduruldu"
                if job.watchdog_tripped:
                    job.error = {"message_tr": "İş beklenen süreyi aştı ve bekçi tarafından durduruldu; en iyi "
                                               "bulunan çözüm gösteriliyor."}
                self._finish_locked(job, "cancelled" if (stopped and job.cancel_requested) or
                                    (stopped and job.watchdog_tripped) else "done", result=res2)
            if self.worker is w:
                self._dispatch_locked()
            self._prune_locked()

    # ------------------------------------------------------------------ bekçi
    def _monitor_loop(self) -> None:
        while not self._stopping:
            time.sleep(0.25)
            try:
                self._check()
            except Exception:
                log.exception("bekçi denetimi başarısız")

    def _check(self) -> None:
        reason = None
        with self._lock:
            w = self.worker
            if self._stopping or w is None:
                return
            now = time.monotonic()
            if not w.alive():
                code = w.proc.exitcode
                reason = f"Çözücü süreci beklenmedik biçimde kapandı (çıkış kodu {code})."
            elif not w.ready and now - w.started_at > self.settings.worker_start_timeout_s:
                reason = "Çözücü süreci zamanında başlayamadı."
            elif w.ready and now - w.last_hb > self.settings.heartbeat_timeout_s:
                reason = (f"Çözücü süreci {self.settings.heartbeat_timeout_s:g} sn boyunca yanıt vermedi "
                          f"(kalp atışı yok).")
            elif w.current is not None:
                job = self.jobs.get(w.current)
                if job is not None:
                    if job.deadline is not None and now > job.deadline and not job.watchdog_tripped:
                        job.watchdog_tripped = True
                        job.cancel_deadline = now + self.settings.cancel_grace_s
                        w.send_cancel(job.id)
                        self._publish_locked(job, "state", {
                            "state": "running", "cancel_requested": True,
                            "message_tr": "İş beklenen süreyi aştı; bekçi durdurma istedi."})
                    elif job.cancel_deadline is not None and now > job.cancel_deadline:
                        reason = ("İş beklenen süreyi aştı ve durdurma isteğine yanıt vermedi."
                                  if job.watchdog_tripped else "İptal edilen iş durdurma isteğine yanıt vermedi.")
        if reason is not None:
            self._restart(reason)

    def _restart(self, reason_tr: str) -> None:
        """İşçiyi öldürür, o anki işi kapatır, yeni işçi başlatır (kuyruktaki işler korunur)."""
        with self._lock:
            w = self.worker
            if w is None or self._stopping:
                return
            jid = w.current
            w.current = None
            self.worker = None                       # eski okuyucu iş parçacığı kendiliğinden çıkar
            self.restarts += 1
            self.restart_log.append({"time": time.time(), "reason_tr": reason_tr, "pid": w.pid, "job": jid})
            if jid is not None:
                job = self.jobs.get(jid)
                if job is not None and not job.final:
                    msg = (f"{reason_tr} Çözücü süreci yeniden başlatıldı; iş tamamlanamadı. Lütfen yeniden deneyin.")
                    state = "cancelled" if (job.cancel_requested and not job.watchdog_tripped) else "failed"
                    self._finish_locked(job, state, error={"message_tr": msg, "restart": True})
        log.warning("çözücü işçisi yeniden başlatılıyor: %s", reason_tr)
        w.kill()
        with self._lock:
            if not self._stopping:
                self._spawn_locked()


def _safe_bool(fn, arg) -> bool:
    try:
        return bool(fn(arg))
    except Exception:
        return False
