"""FastAPI uygulaması (şartname 5, Faz 4): uç noktalar, hata biçimleri, SSE, statik dosyalar, yaşam döngüsü.

Sunucu yalnız 127.0.0.1'e bağlanır (backend/run.py). Çözümler sunucu sürecinde değil, tek bir uzun ömürlü işçi
sürecinde çalışır (jobs.py, worker.py). Uç noktaların Türkçe açıklaması ve örnekleri: docs/API.md.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import orjson
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse, Response

from . import meta as meta_mod
from . import tasks
from .cache import ResultCache
from .config import (
    API_VERSION,
    BASEMAP_DIR,
    CORS_ORIGINS,
    GRID_DIR,
    GRID_SIZES,
    PROCESSED_DIR,
    WEB_DIR,
    Settings,
    engine_version,
)
from .jobs import JobManager, NotAvailableError, QueueFullError
from .schemas import BoundsIn, EvaluateIn, SolveIn, translate_errors
from .task_explain import ExplainIn
from .task_pareto import ParetoIn
from .task_sensitivity import SensitivityIn
from .task_sweep import SweepIn
from .static import CACHE_REVALIDATE, CACHE_STATIC, file_response, safe_join, spa_response

log = logging.getLogger("hastane.api")


class ORJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS)


def _err(status: int, message_tr: str, headers: dict | None = None, **extra) -> ORJSONResponse:
    return ORJSONResponse({"message_tr": message_tr, **extra}, status_code=status, headers=headers)


def _json_bytes(obj: dict) -> bytes:
    return orjson.dumps(obj)


async def _send_json(send, status: int, obj: dict, extra_headers: list | None = None) -> None:
    body = _json_bytes(obj)
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers + (extra_headers or [])})
    await send({"type": "http.response.body", "body": body})


class Guard:
    """En dıştaki kullanıcı ara katmanı (Faz 4 incelemesi bulgu 7–9):
      * Host başlığı beyaz listede değilse 400 (DNS yeniden bağlama; sunucu yalnız 127.0.0.1'e bağlı olsa da);
      * istek gövdesi max_body_bytes'ı aşarsa 413 (Content-Length'e ya da okunan bayt sayısına göre);
      * yakalanmamış hata → Türkçe 500 JSON yanıtı; hata ServerErrorMiddleware'e ulaşmadığı için bağlantı açık kalır
        (eskiden 500'den sonra uvicorn bağlantıyı kapatıyordu, sonraki keep-alive isteği sıfırlanıyordu)."""

    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.hosts = {h.lower() for h in settings.allowed_hosts}
        self.max_body = int(settings.max_body_bytes)

    def _host_ok(self, scope) -> bool:
        if "*" in self.hosts:
            return True
        host = ""
        for k, v in scope.get("headers") or ():
            if k == b"host":
                host = v.decode("latin-1").strip().lower()
                break
        if host.startswith("["):                       # [::1]:8000
            host = host[1:].split("]", 1)[0]
        else:
            host = host.split(":", 1)[0]
        return host in self.hosts

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        if not self._host_ok(scope):
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await _send_json(send, 400, {"message_tr": "Geçersiz Host başlığı: sunucu yalnız 127.0.0.1 ya da "
                                                              "localhost adıyla kullanılabilir."})
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for k, v in scope.get("headers") or ():
            if k == b"content-length":
                try:
                    too_big = int(v) > self.max_body
                except ValueError:
                    too_big = False
                if too_big:
                    return await _send_json(send, 413, self._too_big_body())
                break
        state = {"started": False, "read": 0}

        async def recv():
            msg = await receive()
            if msg.get("type") == "http.request":
                state["read"] += len(msg.get("body") or b"")
                if state["read"] > self.max_body:
                    raise StarletteHTTPException(413, self._too_big_body()["message_tr"])
            return msg

        async def snd(msg):
            if msg.get("type") == "http.response.start":
                state["started"] = True
            await send(msg)

        try:
            await self.app(scope, recv, snd)
        except Exception as ex:  # noqa: BLE001
            if state["started"]:
                raise
            log.exception("beklenmeyen hata")
            await _send_json(send, 500, {"message_tr": f"Sunucu hatası: {type(ex).__name__}."})

    def _too_big_body(self) -> dict:
        return {"message_tr": f"İstek gövdesi çok büyük (en çok {_fmt_mb(self.max_body / (1024 * 1024))} MB)."}


def _fmt_mb(x: float) -> str:
    return f"{x:g}".replace(".", ",")


HTTP_TR = {
    400: "İstek gövdesi okunamadı (geçersiz ya da çok derin iç içe JSON).",
    405: "Bu yöntem bu yolda desteklenmiyor.",
    413: "İstek gövdesi çok büyük.",
    415: "Desteklenmeyen içerik türü.",
}


class ApiGzip:
    """Yalnız /api JSON yanıtlarını gzip'ler (SSE, 206 ve zaten sıkıştırılmış dosyalar Starlette'çe atlanır).
    Altlık harita ve arayüz dosyaları kendi gzip'ini yapar (static.py); 47 MB'lık PMTiles sıkıştırılmaz."""

    def __init__(self, app) -> None:
        self.app = app
        self.gz = GZipMiddleware(app, minimum_size=1024)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/"):
            return await self.gz(scope, receive, send)
        return await self.app(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks.load_all()
        cache = ResultCache(settings.cache_dir, max_bytes=settings.cache_max_bytes,
                            max_entries=settings.cache_max_entries, engine=engine_version())
        manager = JobManager(settings, cache)
        manager.start()                     # işçi önce başlar: ısınması önbellek tohumlamasıyla aynı anda sürer
        seeded = {}
        if settings.seed_precomputed:       # boş önbellekte ilk açılışta ≈0,35 sn (18 sonuç gzip'lenir), sonra ≈0,05 sn
            for name, jt in tasks.REGISTRY.items():
                if jt.seed is not None:
                    try:
                        seeded[name] = jt.seed(cache)
                    except Exception as ex:
                        log.warning("%s önbelleğe tohumlanamadı: %s", name, ex)
        app.state.manager = manager
        app.state.cache = cache
        app.state.seeded = seeded
        app.state.started = time.time()
        try:
            yield
        finally:
            await asyncio.get_running_loop().run_in_executor(None, manager.stop)

    app = FastAPI(title="Ankara hastane yerleşimi API", version=API_VERSION, lifespan=lifespan,
                  default_response_class=ORJSONResponse,
                  description="Türkçe açıklama ve örnekler: docs/API.md")
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=list(CORS_ORIGINS), allow_methods=["GET", "POST", "OPTIONS"],
                       allow_headers=["*"], allow_credentials=False, expose_headers=["ETag", "Content-Range"])

    # ------------------------------------------------------------------ hata biçimleri
    @app.exception_handler(tasks.ApiValidationError)
    async def _api_validation(_: Request, ex: tasks.ApiValidationError):
        return _err(422, ex.message_tr, errors=ex.errors)

    @app.exception_handler(RequestValidationError)
    async def _req_validation(_: Request, ex: RequestValidationError):
        errs = translate_errors(list(ex.errors()))
        return _err(422, "İstek doğrulanamadı.", errors=errs)

    @app.exception_handler(QueueFullError)
    async def _queue_full(_: Request, ex: QueueFullError):
        return ORJSONResponse({"message_tr": ex.message_tr}, status_code=429, headers={"Retry-After": "5"})

    @app.exception_handler(NotAvailableError)
    async def _not_available(_: Request, ex: NotAvailableError):
        return _err(501, ex.message_tr)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, ex: StarletteHTTPException):
        detail = ex.detail if isinstance(ex.detail, str) else None
        # Türkçe olmayan (çerçevenin İngilizce) iletileri: ASCII dışı harf içermeyen ayrıntı Türkçe karşılığıyla
        english = detail is not None and detail.isascii()
        if ex.status_code == 404:
            msg = detail if detail and detail != "Not Found" else "Bulunamadı."
        elif ex.status_code in HTTP_TR and (detail is None or english):
            msg = HTTP_TR[ex.status_code]
        else:
            msg = detail or "İstek işlenemedi."
        return _err(ex.status_code, msg, headers=getattr(ex, "headers", None))

    def mgr(request: Request) -> JobManager:
        return request.app.state.manager

    # ------------------------------------------------------------------ durum ve üst veri
    @app.get("/api/health")
    def health(request: Request):
        m = mgr(request)
        h = m.health()
        w = h["worker"] or {}
        return {"ok": True, "status_tr": "çalışıyor", "api_version": API_VERSION, "engine_version": engine_version(),
                "time": time.time(), "uptime_s": round(time.time() - request.app.state.started, 2),
                "solver_ready": bool(w.get("ready") and w.get("alive")), "seeded": request.app.state.seeded, **h}

    @app.get("/api/meta")
    def get_meta():
        return meta_mod.build_meta(tasks.REGISTRY)

    @app.get("/api/sources")
    def get_sources():
        return meta_mod.build_sources()

    @app.get("/api/literature")
    def get_literature():
        """Kaynakça (data/reference/literatur.yaml; künyeler Crossref vb. ile doğrulanmış) JSON olarak."""
        return meta_mod.build_literature()

    # ------------------------------------------------------------------ veri dosyaları
    @app.get("/api/grid/{km}")
    def grid_cells(km: int, request: Request):
        if km not in GRID_SIZES:
            raise HTTPException(404, f"Izgara yok: {km} km (izinli: {', '.join(map(str, GRID_SIZES))}).")
        return file_response(request, GRID_DIR / f"grid_{km}km_cells.geojson", cache_control=CACHE_REVALIDATE)

    boundary_files = {"il": WEB_DIR / "ankara_il.geojson", "ilceler": WEB_DIR / "ankara_ilceler.geojson"}
    boundary_cache: dict = {}

    @app.get("/api/boundaries")
    def boundaries(request: Request):
        """İl ve ilçe sınırları tek yanıtta: {"il": FeatureCollection, "ilceler": FeatureCollection}."""
        sts = [p.stat() for p in boundary_files.values()]
        sig = tuple((s.st_mtime_ns, s.st_size) for s in sts)
        if boundary_cache.get("sig") != sig:
            body = b'{"il":' + boundary_files["il"].read_bytes().strip() + b',"ilceler":' + \
                boundary_files["ilceler"].read_bytes().strip() + b"}"
            import gzip as _gz
            import hashlib as _h
            boundary_cache.update(sig=sig, body=body, gz=_gz.compress(body, 6),
                                  etag='"' + _h.md5(repr(sig).encode(), usedforsecurity=False).hexdigest() + '"')
        etag = boundary_cache["etag"]
        headers = {"etag": etag, "cache-control": CACHE_REVALIDATE, "vary": "Accept-Encoding"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        if "gzip" in request.headers.get("accept-encoding", ""):
            return Response(boundary_cache["gz"], media_type="application/json",
                            headers={**headers, "content-encoding": "gzip"})
        return Response(boundary_cache["body"], media_type="application/json", headers=headers)

    @app.get("/api/boundaries/{name}")
    def boundary_file(name: str, request: Request):
        p = boundary_files.get(name)
        if p is None:
            raise HTTPException(404, "Sınır dosyası yok (izinli: il, ilceler).")
        return file_response(request, p, media_type="application/geo+json")

    @app.get("/api/measures")
    def measures(request: Request):
        return file_response(request, PROCESSED_DIR / "ankara_olculer.json")

    bounds_cache: dict = {}

    @app.get("/api/bounds/{km}")
    def bounds_cards(km: int):
        """Varsayılan parametrelerle önceden hesaplanmış (uzun süreli, çoğu kanıtlı) sınır kartları."""
        p = PROCESSED_DIR / "sinir_kartlari.json"
        st = p.stat()
        if bounds_cache.get("sig") != (st.st_mtime_ns, st.st_size):
            bounds_cache.update(sig=(st.st_mtime_ns, st.st_size), data=orjson.loads(p.read_bytes()))
        d = bounds_cache["data"]
        g = (d.get("grids") or {}).get(str(km))
        if g is None:
            raise HTTPException(404, f"{km} km için sınır kartı yok.")
        return {"grid_km": km, "generated_by": d.get("generated_by"), "generated_at": d.get("generated_at"),
                "precomputed": True, **g}

    @app.get("/api/precomputed/{name}")
    def precomputed(name: str, request: Request):
        p = meta_mod.precomputed_path(name.removesuffix(".json"))
        if p is None:
            raise HTTPException(404, "Bu ad önceden çözülmüş dosyalar listesinde yok (izinli: "
                                     + ", ".join(meta_mod.PRECOMPUTED_NAMES) + ", scenario_<id>_<km>km, "
                                     "explain_[<senaryo>_]<km>km, pareto_[<senaryo>_]<km>km, "
                                     "tornado_[<senaryo>_]<km>km).")
        return file_response(request, p)

    # ------------------------------------------------------------------ işler
    def submit(request: Request, type_name: str, body) -> ORJSONResponse:
        job, replaced = mgr(request).submit(type_name, body, client_id=body.client_id, replace=body.replace,
                                            use_cache=body.use_cache)
        snap = mgr(request).snapshot(job.id, include_result=job.from_cache) or {}
        snap["replaced"] = replaced
        return ORJSONResponse(snap, status_code=200 if job.from_cache else 202)

    @app.post("/api/solve", status_code=202)
    def post_solve(request: Request, body: SolveIn):
        return submit(request, "solve", body)

    @app.post("/api/evaluate", status_code=202)
    def post_evaluate(request: Request, body: EvaluateIn):
        return submit(request, "evaluate", body)

    @app.post("/api/bounds", status_code=202)
    def post_bounds(request: Request, body: BoundsIn):
        return submit(request, "bounds", body)

    @app.post("/api/explain", status_code=202)
    def post_explain(request: Request, body: ExplainIn):
        """Şartname 5 POST /api/explain (hastane kapatma etkisi, N − 1 / N + 1, boyut, gölge fiyat, model nedenleri)."""
        return submit(request, "explain", body)

    @app.post("/api/sweep", status_code=202)
    def post_sweep(request: Request, body: SweepIn):
        """Şartname 5 POST /api/sweep: p taraması (U eğrisi), arka planda, ilerlemeli."""
        return submit(request, "sweep", body)

    @app.post("/api/pareto", status_code=202)
    def post_pareto(request: Request, body: ParetoIn):
        """Şartname 5 POST /api/pareto: λ taraması (açılış maliyeti – ortalama mesafe)."""
        return submit(request, "pareto", body)

    @app.post("/api/sensitivity", status_code=202)
    def post_sensitivity(request: Request, body: SensitivityIn):
        """Şartname 5 POST /api/sensitivity: tornado (±%20)."""
        return submit(request, "sensitivity", body)

    @app.post("/api/jobs/{type_name}", status_code=202)
    def post_job(type_name: str, request: Request, payload: dict = Body(default_factory=dict)):
        """Genel iş gönderimi: kayıtlı her iş türü (Faz 6'da eklenecekler dahil) bu uçtan da çalışır."""
        try:
            jt = tasks.get(type_name)
        except KeyError:
            raise HTTPException(404, f"Bilinmeyen iş türü: '{type_name}' (izinli: "
                                     f"{', '.join(sorted(tasks.REGISTRY))}).") from None
        try:
            body = jt.request_model.model_validate(payload)
        except ValidationError as ex:
            errs = translate_errors([{**e, "loc": ("body", *e.get("loc", ()))} for e in ex.errors()])
            return _err(422, "İstek doğrulanamadı.", errors=errs)
        return submit(request, type_name, body)

    @app.get("/api/jobs")
    def list_jobs(request: Request, client_id: str | None = None, limit: int = 50):
        return {"jobs": mgr(request).list_jobs(client_id, max(1, min(limit, 500)))}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request, result: bool = True):
        snap = mgr(request).snapshot(job_id, include_result=result)
        if snap is None:
            raise HTTPException(404, "İş bulunamadı (kimlik yanlış ya da iş bellekten silinmiş).")
        return snap

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, request: Request):
        m = mgr(request)
        job = m.cancel(job_id)
        if job is None:
            raise HTTPException(404, "İş bulunamadı.")
        snap = m.snapshot(job_id, include_result=False)
        snap["cancel_effective"] = job.state in ("cancelled",) or job.cancel_requested
        return snap

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request):
        m = mgr(request)
        last = request.headers.get("last-event-id", "")
        after = int(last) if last.isdigit() else 0
        sub = m.subscribe(job_id, asyncio.get_running_loop(), after)
        if sub is None:
            raise HTTPException(404, "İş bulunamadı.")
        q, backlog, final = sub
        if final and not backlog:
            # iş bitmiş ve istemci son olayı zaten almış (Last-Event-ID = son olay): 204 → EventSource yeniden
            # bağlanmayı bırakır (boş 200 akışı sonsuz yeniden bağlanmaya yol açıyordu; bulgu 11)
            m.unsubscribe(job_id, q)
            return Response(status_code=204, headers={"Cache-Control": "no-cache"})
        hb = settings.sse_heartbeat_s

        def ev(item: dict) -> ServerSentEvent:
            return ServerSentEvent(data=orjson.dumps(item["data"], option=orjson.OPT_SERIALIZE_NUMPY).decode(),
                                   event=item["event"], id=str(item["id"]))

        async def gen():
            try:
                for item in backlog:
                    yield ev(item)
                    if item["event"] in ("result", "error"):
                        return
                if final:
                    return
                t0 = time.monotonic()
                while True:
                    try:
                        item = await asyncio.wait_for(q.get(), timeout=hb)
                    except asyncio.TimeoutError:
                        snap = m.snapshot(job_id, include_result=False)
                        if snap is None:
                            return
                        data = {"state": snap["state"], "position": snap["position"],
                                "elapsed_s": round(time.monotonic() - t0, 2), "run_s": snap["run_s"],
                                "progress": snap["progress"], "cancel_requested": snap["cancel_requested"]}
                        yield ServerSentEvent(data=orjson.dumps(data).decode(), event="heartbeat")
                        continue
                    yield ev(item)
                    if item["event"] in ("result", "error"):
                        return
            finally:
                m.unsubscribe(job_id, q)

        return EventSourceResponse(gen(), ping=15, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ altlık harita ve arayüz
    @app.api_route("/basemap/{path:path}", methods=["GET", "HEAD"])
    def basemap(path: str, request: Request):
        target = safe_join(BASEMAP_DIR, path)
        if target is None:
            return Response("Bulunamadı", status_code=404, media_type="text/plain; charset=utf-8")
        return file_response(request, target, cache_control=CACHE_STATIC)

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def frontend(path: str, request: Request):
        if path == "api" or path.startswith("api/"):
            return _err(404, "Bilinmeyen API yolu.")
        return spa_response(request, request.app.state.settings.static_dir, path)

    app.add_middleware(ApiGzip)
    app.add_middleware(Guard, settings=settings)           # en dış kullanıcı katmanı (Host, gövde sınırı, 500)
    return app
