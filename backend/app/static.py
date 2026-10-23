"""Dosya sunumu: güvenli yol çözümü (dizin dışına çıkış yok), açık MIME eşlemesi, ETag/304, HTTP Range (PMTiles için
206), gzip (GeoJSON/JSON, bellekte bir kez sıkıştırılır), önbellek başlıkları ve tek sayfa uygulaması (SPA) geri dönüşü.

MIME türleri mimetypes modülünden alınmaz: Windows'ta kayıt defteri .js için 'text/plain' gibi yanlış türler
döndürebiliyor; tarayıcı modül betiklerini o zaman reddeder.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import threading
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, MalformedRangeHeader, Response

MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json", ".geojson": "application/geo+json",
    ".map": "application/json", ".pmtiles": "application/vnd.pmtiles", ".pbf": "application/x-protobuf",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".webp": "image/webp", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".wasm": "application/wasm", ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8",
    ".md": "text/markdown; charset=utf-8", ".webmanifest": "application/manifest+json",
}
COMPRESSIBLE = {".html", ".js", ".mjs", ".css", ".json", ".geojson", ".map", ".svg", ".txt", ".csv", ".md"}
CACHE_REVALIDATE = "no-cache"                               # her kullanımda ETag ile doğrula (veri değişebilir)
CACHE_STATIC = "public, max-age=86400"                      # altlık harita, yazı tipleri, simgeler
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"     # Vite'ın özetli adlı derleme dosyaları (assets/)
GZIP_MIN_BYTES = 1024


class RangeFileResponse(FileResponse):
    """Sözdizimi geçersiz Range başlığı (ör. 'bytes=5-1') yok sayılır ve tüm dosya 200 ile döner (RFC 9110 §14.2;
    Starlette 400 veriyordu — bulgu 13). Karşılanamayan aralık yine 416."""

    @classmethod
    def _parse_range_header(cls, http_range: str, file_size: int) -> list[tuple[int, int]]:
        try:
            return super()._parse_range_header(http_range, file_size)
        except MalformedRangeHeader:
            return []


def mime_for(path: Path) -> str:
    return MIME.get(path.suffix.lower(), "application/octet-stream")


def safe_join(base: Path, rel: str) -> Path | None:
    """base altındaki dosya; dizin dışına çıkan, gizli bileşenli, mutlak ya da var olmayan yol → None."""
    if not rel or "\x00" in rel:
        return None
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("..", ".") or p.startswith(".") for p in parts if p) or rel.startswith(("/", "\\")):
        return None
    if ":" in rel:                                       # Windows sürücü harfi / NTFS akışı (C:..., dosya::$DATA)
        return None
    try:
        root = base.resolve()
        target = (root / Path(*[p for p in parts if p])).resolve()
    except (OSError, ValueError, TypeError):
        return None
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target


def _etag(st: os.stat_result) -> str:
    # Starlette FileResponse ile aynı biçim (If-Range karşılaştırması tutarlı olsun)
    return '"' + hashlib.md5(f"{st.st_mtime}-{st.st_size}".encode(), usedforsecurity=False).hexdigest() + '"'


def _not_modified(request: Request, etag: str) -> bool:
    inm = request.headers.get("if-none-match")
    if not inm:
        return False
    tags = [t.strip() for t in inm.split(",")]
    return "*" in tags or etag in tags or f"W/{etag}" in tags


_GZ: dict[str, tuple[float, int, bytes]] = {}
_GZ_LOCK = threading.Lock()


def _gz_bytes(path: Path, st: os.stat_result) -> bytes:
    k = str(path)
    with _GZ_LOCK:
        hit = _GZ.get(k)
        if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
    data = gzip.compress(path.read_bytes(), compresslevel=6)
    with _GZ_LOCK:
        _GZ[k] = (st.st_mtime, st.st_size, data)
    return data


def file_response(request: Request, path: Path, *, cache_control: str = CACHE_REVALIDATE,
                  media_type: str | None = None) -> Response:
    """ETag + 304; sıkıştırılabilir dosyada (Range yokken) gzip; aksi halde Starlette FileResponse (Range → 206)."""
    st = path.stat()
    etag = _etag(st)
    mt = media_type or mime_for(path)
    base_headers = {"cache-control": cache_control, "etag": etag, "vary": "Accept-Encoding"}
    if _not_modified(request, etag) and "range" not in request.headers:
        return Response(status_code=304, headers=base_headers)
    accepts_gz = "gzip" in request.headers.get("accept-encoding", "").lower()
    if (path.suffix.lower() in COMPRESSIBLE and st.st_size >= GZIP_MIN_BYTES and accepts_gz
            and "range" not in request.headers):
        body = _gz_bytes(path, st)
        return Response(content=b"" if request.method == "HEAD" else body, media_type=mt,
                        headers={**base_headers, "content-encoding": "gzip", "content-length": str(len(body))})
    return RangeFileResponse(path, media_type=mt, stat_result=st, headers={"cache-control": cache_control})


PLACEHOLDER_HTML = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ankara hastane yerleşimi — sunucu</title>
<style>body{font-family:system-ui,sans-serif;max-width:46rem;margin:3rem auto;padding:0 1rem;line-height:1.5;
color:#1d2433;background:#fff}code{background:#f1f3f6;padding:.1rem .3rem;border-radius:.2rem}</style></head>
<body><h1>Ankara için optimum hastane yerleşimi</h1>
<p>Sunucu çalışıyor. Arayüz (frontend) henüz derlenmedi: <code>backend/static/index.html</code> yok.</p>
<p>API: <a href="/api/health">/api/health</a> · <a href="/api/meta">/api/meta</a> ·
<a href="/openapi.json">/openapi.json</a> (makine okunur şema). Türkçe açıklama ve örnekler <code>docs/API.md</code>
dosyasında. (<code>/docs</code> etkileşimli sayfası dosyalarını internetten yüklediği için çevrimdışı açılmaz.)</p>
</body></html>"""


def spa_response(request: Request, static_dir: Path, rel: str) -> Response:
    """Derlenmiş arayüz: dosya varsa o; uzantısız yol → index.html (SPA); index.html yoksa bilgi sayfası."""
    target = safe_join(static_dir, rel) if rel else None
    if target is not None:
        cc = CACHE_IMMUTABLE if rel.startswith("assets/") else CACHE_REVALIDATE
        return file_response(request, target, cache_control=cc)
    last = rel.rsplit("/", 1)[-1]
    if rel and "." in last:                               # eksik varlık dosyası: 404 (index.html değil)
        return Response("Bulunamadı", status_code=404, media_type="text/plain; charset=utf-8")
    index = safe_join(static_dir, "index.html")
    if index is not None:
        return file_response(request, index, cache_control=CACHE_REVALIDATE)
    return HTMLResponse(PLACEHOLDER_HTML, headers={"cache-control": "no-cache"})
