"""Faz 4 — API yüzeyi (çözücü işçisi BAŞLATILMADAN): sağlık, üst veri, kaynaklar, senaryolar, veri dosyaları, ETag/gzip,
PMTiles Range (206), MIME türleri, dizin dışına çıkma denemeleri, SPA geri dönüşü, CORS ve doğrulama hataları (422).

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase4_api.py
"""
from __future__ import annotations

import gzip
import math

import orjson
import pytest
from fastapi.testclient import TestClient

from backend.app.config import BASEMAP_DIR, Settings
from backend.app.main import create_app
from backend.model.params import Params, registry

from .test_phase4_common import BASE_URL, n_precomputed

PMTILES_SIZE = (BASEMAP_DIR / "ankara.pmtiles").stat().st_size


@pytest.fixture(scope="module")
def static_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    static = root / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>arayuz</title><div id=app></div>", encoding="utf-8")
    (static / "assets" / "app-1a2b3c.js").write_text("console.log('merhaba');" * 100, encoding="utf-8")
    (root / "gizli.txt").write_text("GIZLI-ICERIK", encoding="utf-8")     # static dizininin DIŞINDA
    return static


@pytest.fixture(scope="module")
def client(tmp_path_factory, static_dir):
    s = Settings(cache_dir=tmp_path_factory.mktemp("cache"), static_dir=static_dir, start_worker=False,
                 max_queued=50, max_queued_per_client=50)
    with TestClient(create_app(s), base_url=BASE_URL) as c:
        yield c


def _cancel_all(client, resp):
    if resp.status_code in (200, 202):
        client.post(f"/api/jobs/{resp.json()['job_id']}/cancel")


# ---------------------------------------------------------------------- sağlık ve üst veri
def test_health(client):
    h = client.get("/api/health").json()
    assert h["ok"] is True and h["status_tr"] == "çalışıyor"
    assert set(h["job_types"]) >= {"solve", "evaluate", "bounds"}
    assert h["seeded"] == {"solve": n_precomputed()} and h["cache"]["entries"] == n_precomputed()
    assert h["solver_ready"] is False                                  # bu dosyada işçi yok


def test_meta_parameters_and_groups(client):
    m = client.get("/api/meta").json()
    reg = registry()
    assert set(m["parameters"]) == set(reg) and len(reg) == 69
    for k, p in m["parameters"].items():
        e = reg[k]
        assert p["label"] == e["label"] and p["value"] == e["value"] and p["unit"] == e["unit"]
        assert p["status"] == e["status"] and p["status_tr"] in ("kaynaklı", "hesap", "varsayım", "model seçimi")
        assert p["range"] == e.get("range") and p["range_note"] == e.get("range_note")
        if e["status"] in ("kaynakli", "hesap"):
            assert p["sources"] and all(s["title"] and s["url"] for s in p["sources"]), k
        if e["status"] in ("varsayim", "model_secimi"):
            assert p["rationale"], k
    assert [g["id"] for g in m["groups"]] == list("ABCDEFGHI")          # şartname 4.4 grupları
    listed = [k for g in m["groups"] for k in g["keys"]]
    assert sorted(listed) == sorted(reg) and len(listed) == len(set(listed))
    assert "population.scenario_year" in m["groups"][0]["keys"]        # nüfus senaryosu A grubunda
    assert m["defaults"]["grid_km"] == 3 and m["defaults"]["mode"] == "toplam"
    assert [g["km"] for g in m["grids"]] == [2, 3, 5]
    assert m["parameters"]["demand.beds_per_1000"]["allowed"] == {
        "kind": "number", "min": 0, "max": None, "min_inclusive": False, "max_inclusive": True}


def test_meta_scenarios(client):
    sc = {s["id"]: s for s in client.get("/api/meta").json()["scenarios"]}
    assert list(sc) == ["varsayilan", "dusuk_butce", "erisim_oncelikli", "sadece_genel", "sehir_serbest",
                        "nufus_2030"]
    assert [s["title"] for s in sc.values()] == ["Varsayılan", "Düşük bütçe", "Erişim öncelikli",
                                                 "Sadece genel hastane", "Şehir hastanesi serbest", "2030 nüfusu"]
    for s in sc.values():
        assert s["description_tr"] and s["status"] == "model_secimi" and s["warnings"] == []
    reg = registry()
    # değerler kayıttaki alternatiflerden (uydurma sayı yok)
    for sid in ("erisim_oncelikli", "sadece_genel", "sehir_serbest", "nufus_2030"):
        for k, v in sc[sid]["overrides"].items():
            if reg[k]["value"] == v:
                continue
            assert v in [a["value"] for a in reg[k]["alternatives"]], (sid, k, v)
    assert sc["dusuk_butce"]["mode"] == "butce" and sc["dusuk_butce"]["overrides"]["solver.budget_tl"] == 346e9
    # açıklamalardaki türetilmiş sayılar parametre katmanıyla tutarlı
    base, acc = Params.load(), Params.load(sc["erisim_oncelikli"]["overrides"])
    assert round(base.cost_per_km, 2) == 8.86 and round(acc.cost_per_km, 2) == 15.41
    assert round(acc.lam / base.lam, 2) == 1.74
    q = base.province_pop * base.beds_per_person
    assert math.ceil(q / 999) == 19 and math.ceil(q / 4000) == 5


def test_sources(client):
    s = client.get("/api/sources").json()
    assert s["n_sources"] == 148                                       # data/SOURCES.md: "Kaynak sayısı: 148"
    titles = {d["id"]: d["title"] for d in s["domains"]}
    assert titles["nufus"] == "Nüfus — TÜİK ADNKS ve il projeksiyonu"
    rows = [r for d in s["domains"] for r in d["sources"]]
    assert all(r["title"] and r["url"] and r["accessed"] for r in rows)
    beds = [r for r in rows if "demand.beds_per_1000" in r["parameters"]]
    assert any(r["id"] == "saglik_sbsgm_bulten2025" for r in beds)
    lit = {x["id"]: x for x in s["literature"]}
    assert lit["hakimi1964"]["doi"] == "10.1287/opre.12.3.450"


# ---------------------------------------------------------------------- veri dosyaları
def test_grid_geojson_etag_gzip(client):
    r = client.get("/api/grid/5")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/geo+json")
    assert r.headers["content-encoding"] == "gzip" and r.headers["etag"]
    fc = r.json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 1178
    r2 = client.get("/api/grid/5", headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304 and r2.content == b""
    assert client.get("/api/grid/4").status_code == 404
    assert client.get("/api/grid/3", headers={"Accept-Encoding": "identity"}).headers.get("content-encoding") is None


def test_boundaries_measures_bounds_precomputed(client):
    b = client.get("/api/boundaries").json()
    assert b["il"]["type"] == "FeatureCollection" and len(b["ilceler"]["features"]) == 25
    il = client.get("/api/boundaries/il")
    assert il.headers["content-type"].startswith("application/geo+json") and il.json()["type"] == "FeatureCollection"
    assert client.get("/api/boundaries/mahalle").status_code == 404
    m = client.get("/api/measures").json()
    assert "card" in m and "area" in m
    bc = client.get("/api/bounds/5").json()
    assert bc["grid_km"] == 5 and bc["coverage_lscp"]["value"] == 25 and bc["p_min"]["value"] == 27
    assert client.get("/api/bounds/4").status_code == 404
    p = client.get("/api/precomputed/default_5km")
    assert p.status_code == 200 and p.json()["kpis"]["n_hospitals"] == 69
    assert client.get("/api/precomputed/p_sweep_3km.json").status_code == 200
    for bad in ("default_4km", "..%2F..%2Fdata%2FSOURCES", "%2e%2e%2fparameters", "solver_benchmark"):
        r = client.get(f"/api/precomputed/{bad}")
        assert r.status_code == 404 and "message_tr" in r.json(), bad


# ---------------------------------------------------------------------- altlık harita: Range ve MIME
def test_pmtiles_range_requests(client):
    r = client.get("/basemap/ankara.pmtiles", headers={"Range": "bytes=0-126"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-126/{PMTILES_SIZE}"
    assert len(r.content) == 127 and r.content[:7] == b"PMTiles"
    assert r.headers["content-type"] == "application/vnd.pmtiles" and r.headers["accept-ranges"] == "bytes"
    assert "content-encoding" not in r.headers and r.headers["cache-control"].startswith("public")
    r = client.get("/basemap/ankara.pmtiles", headers={"Range": "bytes=-100"})
    assert r.status_code == 206 and len(r.content) == 100
    r = client.get("/basemap/ankara.pmtiles", headers={"Range": f"bytes={PMTILES_SIZE + 10}-"})
    assert r.status_code == 416
    h = client.head("/basemap/ankara.pmtiles")
    assert h.status_code == 200 and int(h.headers["content-length"]) == PMTILES_SIZE


def test_basemap_fonts_sprites_mime(client):
    f = client.get("/basemap/fonts/Noto%20Sans%20Regular/0-255.pbf")
    assert f.status_code == 200 and f.headers["content-type"] == "application/x-protobuf" and len(f.content) > 0
    j = client.get("/basemap/sprites/v4/light.json")
    assert j.status_code == 200 and j.headers["content-type"].startswith("application/json")
    p = client.get("/basemap/sprites/v4/light.png")
    assert p.status_code == 200 and p.headers["content-type"] == "image/png"


TRAVERSAL = [
    "/basemap/..%2F..%2Frequirements.txt",
    "/basemap/%2e%2e/%2e%2e/requirements.txt",
    "/basemap/fonts/..%2F..%2F..%2Fbackend%2Fapp%2Fmain.py",
    "/basemap/fonts/..%5C..%5C..%5Crequirements.txt",
    "/basemap/%2Fetc%2Fpasswd",
    "/basemap/C:%5CWindows%5Cwin.ini",
    "/basemap/ankara.pmtiles%00.txt",
    "/basemap/fonts/.%2E/.%2E/.%2E/requirements.txt",
    "/basemap/.gitignore",
    "/..%2Fgizli.txt",
    "/%2e%2e/gizli.txt",
    "/assets/..%2F..%2Fgizli.txt",
    "/assets/%2e%2e%2f%2e%2e%2fgizli.txt",
    "/api/precomputed/..%2F..%2Frequirements",
    "/api/boundaries/..%2F..%2Fankara_olculer",
]


@pytest.mark.parametrize("path", TRAVERSAL)
def test_directory_traversal_rejected(client, path):
    r = client.get(path)
    assert r.status_code == 404, (path, r.status_code)
    assert b"GIZLI-ICERIK" not in r.content and b"highspy" not in r.content and b"root:" not in r.content
    assert b"import" not in r.content


# ---------------------------------------------------------------------- arayüz (SPA) ve CORS
def test_spa_fallback_and_assets(client):
    r = client.get("/")
    assert r.status_code == 200 and b"arayuz" in r.content and r.headers["cache-control"] == "no-cache"
    r = client.get("/senaryo/karsilastir")                           # istemci tarafı yol → index.html
    assert r.status_code == 200 and b"arayuz" in r.content
    a = client.get("/assets/app-1a2b3c.js")
    assert a.status_code == 200 and a.headers["content-type"].startswith("text/javascript")
    assert "immutable" in a.headers["cache-control"] and a.headers["content-encoding"] == "gzip"
    assert client.get("/assets/yok-9z9z.js").status_code == 404     # eksik varlık index.html'e düşmez
    r = client.get("/api/olmayan")
    assert r.status_code == 404 and r.json()["message_tr"]


def test_placeholder_without_frontend(tmp_path):
    s = Settings(cache_dir=tmp_path / "c", static_dir=tmp_path / "bos", start_worker=False, seed_precomputed=False)
    with TestClient(create_app(s), base_url=BASE_URL) as c:
        r = c.get("/")
        assert r.status_code == 200 and "Arayüz (frontend) henüz derlenmedi".encode() in r.content


def test_cors_only_vite_dev_server(client):
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
        r = client.options("/api/solve", headers={"Origin": origin, **pre})
        assert r.headers.get("access-control-allow-origin") == origin
    r = client.options("/api/solve", headers={"Origin": "http://kotu.example", **pre})
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert "access-control-allow-origin" not in r.headers


# ---------------------------------------------------------------------- doğrulama (422, Türkçe)
def _errors(r) -> list[dict]:
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["message_tr"]
    return body["errors"]


def test_unknown_parameter_key(client):
    errs = _errors(client.post("/api/solve", json={"overrides": {"demand.beds_per_100": 3}}))
    assert errs[0]["key"] == "demand.beds_per_100" and errs[0]["type"] == "bilinmeyen_parametre"
    assert "demand.beds_per_1000" in errs[0]["suggestions"] and "Bilinmeyen parametre" in errs[0]["message_tr"]


def test_out_of_domain_and_ui_range(client):
    errs = _errors(client.post("/api/solve", json={"overrides": {"demand.beds_per_1000": -1, "access.alpha": 4,
                                                                 "cost.vat_rate": 1.5, "size.min_hospitals": 2.5}}))
    by = {e["key"]: e for e in errs}
    assert by["demand.beds_per_1000"]["type"] == "aralik_disi"
    assert by["demand.beds_per_1000"]["allowed"]["min"] == 0 and by["demand.beds_per_1000"]["allowed"]["min_inclusive"] is False
    assert by["access.alpha"]["type"] == "arayuz_araligi_disi" and by["access.alpha"]["ui_range"] == [1.0, 3.0]
    assert by["cost.vat_rate"]["allowed"]["max"] == 1 and "≤ 1" in by["cost.vat_rate"]["message_tr"]
    assert by["size.min_hospitals"]["type"] == "tur_hatasi"
    # aralık dışı kabul: uyarıyla
    r = client.post("/api/solve", json={"overrides": {"access.alpha": 4}, "allow_outside_range": True,
                                        "grid_km": 5, "use_cache": False})
    assert r.status_code == 202 and any("access.alpha" in w for w in r.json()["warnings"])
    _cancel_all(client, r)
    errs = _errors(client.post("/api/solve", json={"overrides": {"solver.objective_mode": "hepsi",
                                                                 "size.city_enabled": "belki"}}))
    assert {e["type"] for e in errs} == {"secenek_disi", "tur_hatasi"}
    assert "toplam" in next(e for e in errs if e["key"] == "solver.objective_mode")["allowed"]["options"]


def test_inconsistent_and_conflicting(client):
    errs = _errors(client.post("/api/solve", json={"overrides": {"size.general_min_beds": 1200}, "allow_outside_range": True}))
    assert errs[0]["type"] == "tutarsiz_parametre" and "en az yatak" in errs[0]["message_tr"]
    errs = _errors(client.post("/api/solve", json={"grid_km": 5, "overrides": {"grid.cell_km": 3}}))
    assert errs[0]["type"] == "celiski"


def test_bad_lock_cell_ids(client):
    errs = _errors(client.post("/api/solve", json={"grid_km": 5, "locks": {"open": ["5km_112_860", "3km_1_1", "x"],
                                                                         "closed": ["5km_0_0"]}}))
    locs = [tuple(e["loc"]) for e in errs]
    assert ("body", "locks", "open", 1) in locs and ("body", "locks", "open", 2) in locs
    assert ("body", "locks", "closed", 0) in locs and all(e["type"] == "bilinmeyen_hucre" for e in errs)
    errs = _errors(client.post("/api/solve", json={"grid_km": 5, "locks": {"open": ["5km_112_860"],
                                                                         "closed": ["5km_112_860"]}}))
    assert errs[0]["type"] == "kilit_celiskisi"
    # parametre ve kilit hataları tek yanıtta
    errs = _errors(client.post("/api/solve", json={"grid_km": 5, "overrides": {"yok.boyle": 1},
                                                   "locks": {"open": ["yok"]}}))
    assert {e["type"] for e in errs} == {"bilinmeyen_parametre", "bilinmeyen_hucre"}


def test_request_schema_errors_turkish(client):
    errs = _errors(client.post("/api/solve", json={"grid_km": 7, "time_limit": 0, "foo": 1}))
    msgs = {e["field"]: e["message_tr"] for e in errs}
    assert "ya da" in msgs["grid_km"] and msgs["foo"] == "Bilinmeyen alan." and "büyük" in msgs["time_limit"]
    r = client.post("/api/solve", content=b"{bozuk json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["errors"][0]["message_tr"] == "Geçersiz JSON gövdesi."
    r = client.post("/api/jobs/yok", json={})
    assert r.status_code == 404 and "Bilinmeyen iş türü" in r.json()["message_tr"]
    r = client.post("/api/jobs/solve", json={"grid_km": "uc"})
    assert r.status_code == 422 and r.json()["errors"][0]["loc"][:2] == ["body", "grid_km"]
    assert client.get("/api/jobs/yokboyle").status_code == 404


def test_evaluate_validation(client):
    errs = _errors(client.post("/api/evaluate", json={"grid_km": 5, "hospitals": [
        {"cell_id": "5km_112_860"}, {"cell_id": "5km_112_860"}, {"cell_id": "5km_0_0"},
        {"cell_id": "5km_109_861", "class": "IV-C", "beds": 300}, {"cell_id": "5km_110_861", "beds": 5000}]}))
    types = {(tuple(e["loc"]), e["type"]) for e in errs}
    assert (("body", "hospitals", 1, "cell_id"), "tekrar") in types
    assert (("body", "hospitals", 2, "cell_id"), "bilinmeyen_hucre") in types
    assert (("body", "hospitals", 3, "beds"), "aralik_disi") in types
    assert (("body", "hospitals", 4, "beds"), "aralik_disi") in types
    iv = next(e for e in errs if e["loc"] == ["body", "hospitals", 3, "beds"])
    assert iv["allowed"]["min"] == 50 and iv["allowed"]["max"] == 199
    errs = _errors(client.post("/api/evaluate", json={"grid_km": 5, "overrides": {"size.city_enabled": False},
                                                      "hospitals": [{"cell_id": "5km_112_860", "class": "V-D"}]}))
    assert errs[0]["type"] == "sinif_kapali"
    errs = _errors(client.post("/api/evaluate", json={"grid_km": 5, "hospitals": []}))
    assert errs[0]["type"] == "too_short"


def test_queue_limit_429(tmp_path):
    s = Settings(cache_dir=tmp_path / "c", start_worker=False, seed_precomputed=False, max_queued=3,
                 max_queued_per_client=2)
    with TestClient(create_app(s), base_url=BASE_URL) as c:
        body = {"grid_km": 5, "use_cache": False}
        assert c.post("/api/solve", json={**body, "client_id": "a"}).status_code == 202
        assert c.post("/api/solve", json={**body, "client_id": "a"}).status_code == 202
        r = c.post("/api/solve", json={**body, "client_id": "a"})
        assert r.status_code == 429 and "kuyrukta" in r.json()["message_tr"] and r.headers["retry-after"] == "5"
        # 'en son istek kazanır': replace=true eskileri iptal eder, sınıra takılmaz
        r = c.post("/api/solve", json={**body, "client_id": "a", "replace": True})
        assert r.status_code == 202 and len(r.json()["replaced"]) == 2
        assert c.post("/api/solve", json={**body, "client_id": "b"}).status_code == 202
        assert c.post("/api/solve", json={**body, "client_id": "c"}).status_code == 202
        r = c.post("/api/solve", json={**body, "client_id": "d"})
        assert r.status_code == 429 and "Kuyruk dolu" in r.json()["message_tr"]
        jobs = c.get("/api/jobs").json()["jobs"]
        assert sum(j["state"] == "cancelled" for j in jobs) == 2
        assert [j["position"] for j in jobs if j["state"] == "queued"] == [2, 1, 0]


def test_gzip_on_api_json(client):
    r = client.get("/api/meta", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    raw = client.get("/api/meta", headers={"Accept-Encoding": "identity"})
    assert raw.headers.get("content-encoding") is None and orjson.loads(raw.content)["app"]["api_version"] == "1"
    assert len(gzip.compress(raw.content)) < len(raw.content)


def test_result_memory_bound(tmp_path):
    """Bellekte yalnız son max_results_kept sonucun tutulması (eski işlerin özeti kalır)."""
    s = Settings(cache_dir=tmp_path / "c", start_worker=False, max_results_kept=2)
    with TestClient(create_app(s), base_url=BASE_URL) as c:
        ids = [c.post("/api/solve", json={"grid_km": 5}).json()["job_id"] for _ in range(4)]   # önbellekten
        snaps = [c.get(f"/api/jobs/{i}").json() for i in ids]
        assert [x["result"] is not None for x in snaps] == [False, False, True, True]
        assert snaps[0]["result_evicted"] and snaps[0]["state"] == "done"
        with c.stream("GET", f"/api/jobs/{ids[0]}/events") as r:
            last = [ln for ln in r.iter_lines() if ln.startswith("data:")][-1]
        assert '"result_evicted":true' in last


# ---------------------------------------------------------------------- Faz 4 incelemesi düzeltmeleri (bulgu 7–13)
JSON = {"content-type": "application/json"}


def test_overflow_and_huge_values_give_422(client):
    r = client.post("/api/solve", content=b'{"overrides": {"grid.cell_km": 1e999}}', headers=JSON)
    assert r.status_code == 422 and r.json()["errors"][0]["type"] == "secenek_disi"
    r = client.post("/api/solve", content=b'{"overrides": {"grid.k_nearest": 100000000000000000000000000000}}',
                    headers=JSON)
    assert r.status_code == 422 and r.json()["errors"][0]["value"] == "100000000000000000000000000000"
    r = client.post("/api/solve", content=b'{"overrides": {"cost.fixed_per_hospital": 1e999}}', headers=JSON)
    assert r.status_code == 422 and "sonlu" in r.json()["errors"][0]["message_tr"]
    r = client.post("/api/solve", content=b'{"time_limit": 100000000000000000000000000000}', headers=JSON)
    assert r.status_code == 422 and isinstance(r.json()["errors"][0]["input"], str)


def test_deeply_nested_body_turkish_400(client):
    r = client.post("/api/solve", content=b"[" * 100000 + b"]" * 100000, headers=JSON)
    assert r.status_code == 400 and r.json()["message_tr"].startswith("İstek gövdesi okunamadı")


def test_body_size_limit_413(client):
    r = client.post("/api/solve", content=b'{"a": "' + b"x" * (3 * 1024 * 1024) + b'"}', headers=JSON)
    assert r.status_code == 413 and "çok büyük" in r.json()["message_tr"]

    def chunks():                                   # Content-Length olmadan (parça parça) gelen büyük gövde
        for _ in range(40):
            yield b" " * (100 * 1024)
    r = client.post("/api/solve", content=chunks(), headers=JSON)
    assert r.status_code == 413
    assert client.get("/api/health").status_code == 200


def test_host_header_whitelist(client):
    r = client.get("/api/health", headers={"host": "kotu.example:8000"})
    assert r.status_code == 400 and "Host" in r.json()["message_tr"]
    for host in ("127.0.0.1:8000", "localhost:8000", "localhost"):
        assert client.get("/api/health", headers={"host": host}).status_code == 200, host


def test_unhandled_error_is_turkish_json(client, monkeypatch):
    from backend.app import meta as meta_mod

    def boom(*a, **k):
        raise RuntimeError("deneme")
    monkeypatch.setattr(meta_mod, "build_meta", boom)
    r = client.get("/api/meta")
    assert r.status_code == 500 and r.json() == {"message_tr": "Sunucu hatası: RuntimeError."}
    assert client.get("/api/health").status_code == 200


def test_replace_requires_client_id(client):
    r = client.post("/api/solve", json={"grid_km": 5, "replace": True})
    assert r.status_code == 422 and r.json()["errors"][0]["loc"] == ["body", "client_id"]


def test_invalid_range_is_ignored(client):
    r = client.get("/basemap/ankara.pmtiles", headers={"Range": "bytes=5-1"})
    assert r.status_code == 200 and int(r.headers["content-length"]) == PMTILES_SIZE
    assert client.get("/basemap/ankara.pmtiles", headers={"Range": "bytes=-0"}).status_code == 416


def test_explain_stub_501(client):
    m = client.get("/api/meta").json()
    assert m["job_types"]["explain"]["available"] is False and m["job_types"]["solve"]["available"] is True
    body = {"grid_km": 5, "hospitals": [{"cell_id": "5km_112_860"}], "what": ["hastane_kapat"],
            "close": ["5km_112_860"]}
    r = client.post("/api/explain", json=body)
    assert r.status_code == 501 and "Faz 6" in r.json()["message_tr"]
    assert client.post("/api/jobs/explain", json=body).status_code == 501


def test_sse_after_final_event_returns_204(client):
    jid = client.post("/api/solve", json={"grid_km": 5}).json()["job_id"]       # önbellekten: hemen biter
    with client.stream("GET", f"/api/jobs/{jid}/events") as r:
        ids = [ln.split(":", 1)[1].strip() for ln in r.iter_lines() if ln.startswith("id:")]
    assert r.status_code == 200 and ids
    r = client.get(f"/api/jobs/{jid}/events", headers={"Last-Event-ID": ids[-1]})
    assert r.status_code == 204
    r = client.get(f"/api/jobs/{jid}/events", headers={"Last-Event-ID": ids[-2]})
    assert r.status_code == 200 and b"event: result" in r.content


def test_meta_rings_scenario_grids_and_precomputed_layers(client):
    m = client.get("/api/meta").json()
    assert m["access_rings"]["R_km"] == 30 and 20 < m["access_rings"]["R_crowfly_km"] < 30
    assert m["defaults"]["recommended_time_limit_s"] == {"2": 60.0, "3": 15.0, "5": 15.0}
    for sc in m["scenarios"]:
        assert set(sc["grids"]) == {"2", "3", "5"}
        assert set(sc["precomputed_grids"]) <= {2, 3, 5} and set(sc["live_grids"]) <= {2, 3, 5}
    d = next(sc for sc in m["scenarios"] if sc["id"] == "varsayilan")
    assert d["precomputed_grids"] == [] or set(d["precomputed_grids"]) == {2, 3, 5}
    p = client.get("/api/precomputed/default_3km").json()
    L = p["layers"]
    assert len(L["access"]["nearest_km"]) == 3111 and len(L["demand"]["cell_id"]) == p["instance"]["I"]
    assert "size" in p["hospitals"][0] and "population" in p["assignment"]


def test_non_cooperative_cancel_waits_for_job_deadline(tmp_path):
    """Bulgu 2 (yedek kural): iptali izlemeyen iş türünde işçi iptal anında değil, işin bekçi süresinde yeniden başlar."""
    import time as _t
    from dataclasses import replace

    from backend.app import tasks
    from backend.app.jobs import Job, JobManager
    m = JobManager(Settings(cache_dir=tmp_path / "c", start_worker=False, cancel_grace_s=15.0), None)
    for coop, expect_far in ((True, False), (False, True)):
        jt = replace(tasks.get("solve"), cooperative_cancel=coop)
        job = Job(jt, tasks.Prepared(payload={}, max_runtime_s=300.0), "x")
        job.state, job.deadline = "running", _t.monotonic() + 330.0
        m.jobs[job.id] = job
        with m._lock:
            m._cancel_locked(job, "deneme")
        left = job.cancel_deadline - _t.monotonic()
        assert (left > 300.0) if expect_far else (14.0 < left <= 15.0), (coop, left)
