"""Faz 3 — saklanan çıktıların denetimi (yeniden çözmeden): varsayılan çözümler, p taraması (kabul ölçütü), sınır kartları,
kıyaslama dosyası.

Kabul ölçütü (şartname 10): "Varsayılan parametrelerde p taramasının en iyisi = serbest p'li çözüm" — saklanan
precomputed/p_sweep_{3,5}km.json üzerinde (doğrulama B3 sonrası güçlendirilmiş biçim):
  * dip (n* ± 3) simetrik başlangıçlarla sıkı yeniden çözüldü; taramanın en iyi ÜST sınırı tam p = n*'da,
  * hiçbir p'nin çözümü serbest çözümün alt sınırının altında değil (alt sınır geçerli),
  * düzlük dürüstçe raporlanır: alt sınırı serbest çözümün değerini aşmayan p'ler "fark gap içinde" işaretli; alt
    sınırı toleransın ötesinde altta kalanlar "çözülmemiş" listesinde (n*'ın tek en iyi p olduğu kanıtlanamaz).

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase3_outputs.py
"""
from __future__ import annotations

import json

import pytest

from pipeline import common

PRE = common.ROOT / "precomputed"
SPEC_TARGET_S = {3: 15.0, 2: 60.0}


def _ranges(ps) -> str:
    """[64, 65, 67] → '64–65, 67' (kabul özetindeki yazımla aynı)."""
    out, ps, i = [], sorted(ps), 0
    while i < len(ps):
        j = i
        while j + 1 < len(ps) and ps[j + 1] == ps[j] + 1:
            j += 1
        out.append(f"{ps[i]}–{ps[j]}" if j > i else f"{ps[i]}")
        i = j + 1
    return ", ".join(out)


def _load(path):
    if not path.exists():
        pytest.fail(f"{path.relative_to(common.ROOT)} yok — pipeline/32_precompute.py çalıştırılmalı")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("km", [5, 3, 2])
def test_default_solutions_complete_and_checked(km):
    r = _load(PRE / f"default_{km}km.json")
    assert r["ok"] and r["grid_km"] == km and r["mode"] == "toplam"
    assert r["overrides"] == {"grid.cell_km": km}                    # varsayılan parametreler (yalnız ızgara seçimi)
    assert r["check"]["ok"], r["check"]["violations"]
    b, c, k = r["bounds"], r["costs"], r["kpis"]
    assert b["lb_tl"] <= b["ub_tl"]
    assert r["status"] in ("optimal", "sure_doldu", "gap_ulasilamadi")
    assert (r["status"] == "optimal") == (b["gap"] <= r["target_gap"] + 1e-9)
    assert b["gap"] <= 0.01
    assert c["toplam"] == pytest.approx(c["acilis"] + c["erisim"]) == pytest.approx(b["objective_tl"])
    assert sum(k["count_by_class"].values()) == k["n_hospitals"] == len(r["hospitals"])
    assert k["total_beds"] >= k["total_demand_beds"] * (1 - 1e-9)
    assert k["count_by_class"]["IV-C"] > k["n_hospitals"] / 2        # yatak başı maliyet sınıfla artıyor
    assert len(r["duals"]["marginal_cost_tl"]) == r["instance"]["I"]
    assert "Alt sınır L" in r["certificate_tr"]
    # başlangıç adayları (önceki dosya, doğrulamanın .npz çözümleri): sonuç hiçbirinden kötü olamaz (doğrulama A5/A6)
    pre = r["precomputed"]
    for st in pre.get("start_candidates", []):
        assert b["ub_tl"] <= st["objective_tl"] * (1 + 1e-12)
    assert pre["start_source"] is None or any(st["chosen"] for st in pre["start_candidates"])


# U eğrisinin dibi (n* ± 3; 3 km'de ayrıca p = 83) sıkı yeniden çözüldü (doğrulama B3/B4); en kötü gap sınırı:
# 5 km tam MILP hedef gap'e (%0,02) ulaşır; 3 km'de alt sınır tarafı (Lagrange / serbest alt sınır) %0,02'ye inemez
REFINED_MAX_GAP = {5: 2e-4 + 1e-9, 3: 2e-3}


@pytest.mark.parametrize("km", [3, 5])
def test_p_sweep_best_equals_free_optimum(km):
    sw = _load(PRE / f"p_sweep_{km}km.json")
    free = sw["free"]
    d = _load(PRE / f"default_{km}km.json")
    assert free["ub_tl"] == pytest.approx(d["bounds"]["ub_tl"]) and free["n_hospitals"] == d["kpis"]["n_hospitals"]
    assert free["lb_tl"] == pytest.approx(d["bounds"]["lb_tl"])
    n_star = free["n_hospitals"]
    pts = [p for p in sw["points"] if p["ok"]]
    assert len(pts) >= 20
    by_p = {p["p"]: p for p in pts}
    ps = sorted(by_p)
    assert min(ps) < n_star < max(ps) and n_star in ps
    for p in pts:
        assert p["check_ok"] and len(p["hospitals"]) == p["p"]
        assert p["lb_tl"] <= p["ub_tl"] * (1 + 1e-12)
        assert p["total_tl"] == pytest.approx(p["opening_tl"] + p["access_tl"])
        assert p["ub_tl"] >= free["lb_tl"] * (1 - 1e-12)                 # alt sınır geçerli
        # serbest problem her sabit p'nin gevşetmesi: serbest alt sınır her noktada da alt sınırdır
        assert p["lb_tl"] >= max(free["lb_tl"], p["lb_own_tl"] or 0.0) * (1 - 1e-12)
        assert p["gap"] == pytest.approx((p["ub_tl"] - p["lb_tl"]) / p["ub_tl"])
    # dip: n* ± 3 (3 km'de ayrıca 83) yeniden ve SİMETRİK başlangıçlarla çözüldü: serbest çözüm yalnız p = n*'a değil,
    # tek tek kapatma/açma ile her p'ye başlangıç adayı olarak verildi (doğrulama B3: eski test p = n*'ı serbest çözümle
    # tohumlayıp yalnız onu kıyaslıyordu)
    bottom = set(range(n_star - 3, n_star + 4)) | ({83} if km == 3 else set())
    assert bottom <= set(sw["acceptance"]["refined_p"])
    for q in sorted(bottom):
        r = by_p[q]
        assert r["refined"] and r["gap"] <= REFINED_MAX_GAP[km]
        srcs = {st["source"] for st in r["refine"]["starts"] if st["ok"]}
        assert ("serbest" in srcs) if q == n_star else (srcs & {"serbest_komsu"} or any(x.startswith("komsu_p")
                                                                                          for x in srcs))
    # (1) taramanın en iyi üst sınırı p = n*'da ve oradaki değer serbest çözümle aynı
    best = min(pts, key=lambda p: p["ub_tl"])
    assert best["p"] == n_star
    assert by_p[n_star]["ub_tl"] == pytest.approx(free["ub_tl"], rel=1e-12)
    # (2) düzlük dürüstçe: alt sınırı serbest çözümün değerini aşmayan p'ler "fark gap içinde"; alt sınırı serbest
    # çözümün toleransın ötesinde altında kalanlar "çözülmemiş" (n*'ın tek en iyi olduğu kanıtlanamaz)
    acc = sw["acceptance"]
    tol = acc["tolerance"]
    plateau = [p["p"] for p in pts if p["lb_tl"] <= free["ub_tl"] * (1 + 1e-12)]
    unresolved = [p["p"] for p in pts if p["p"] != n_star and p["lb_tl"] < free["ub_tl"] * (1 - tol)]
    assert plateau == acc["plateau_p"] and n_star in plateau
    assert unresolved == acc["unresolved_p"] and acc["free_n_unique_within_tol"] == (not unresolved)
    for p in pts:
        if p["p"] != n_star:
            assert p["fark_gap_icinde"] == (p["p"] in plateau)
            assert p["fark_tl"] == pytest.approx(p["ub_tl"] - free["ub_tl"])
    assert acc["best_p_is_free_n"] and acc["no_p_beats_free_lb"] and acc["passed"]
    others = [q for q in plateau if q != n_star]
    assert (_ranges(others) if others else "yok") in acc["summary_tr"]
    # U şekli: uç noktalar dipten belirgin pahalı; az hastanede erişim, çok hastanede açılış maliyeti yüksek
    # (az hastanede açılış da yüksek: büyük sınıfların yatak başı maliyeti yüksek — verinin özelliği)
    lo, hi = by_p[ps[0]], by_p[ps[-1]]
    assert lo["ub_tl"] > best["ub_tl"] * 1.005 and hi["ub_tl"] > best["ub_tl"] * 1.005
    assert lo["access_tl"] > best["access_tl"] and hi["opening_tl"] > best["opening_tl"]


def test_bound_cards_all_grids():
    bc = _load(common.PROCESSED / "sinir_kartlari.json")["grids"]
    exp_J = {"5": 454, "3": 634, "2": 891}
    assert set(bc) == set(exp_J)
    for km, card in bc.items():
        assert card["max_hospitals_grid"] == exp_J[km]
        assert card["capacity"]["with_city"] == 5 and card["capacity"]["without_city"] == 19
        la, lk, pm = card["coverage_lscp"], card["coverage_lscp_k_list"], card["p_min"]
        # kartın anlamı "her talep hücresine R içinde bir hastane": R içindeki TÜM adaylarla (k = 0), kanıtlı
        # (doğrulama #10 / C3); model listeleriyle (k = 50) olan ikinci satır ayrı ve etiketli
        assert la["k_nearest"] == 0 and lk["k_nearest"] == 50 and la["arcs"] > lk["arcs"]
        assert la["proven"] and la["value"] == la["lower_bound"]
        assert lk["proven"] and lk["value"] == lk["lower_bound"]
        assert la["value"] <= lk["value"]
        assert "yol mesafesi" in card["labels_tr"]["coverage_lscp"]
        assert "model listeleriyle (k = 50)" in card["labels_tr"]["coverage_lscp_k_list"]
        assert "k = 50" in card["labels_tr"]["p_min"]
        # p_min modelde (k listeleri, tüm kısıtlar) tanımlı: alt sınırı k listeli LSCP'den ve kapasiteden küçük olamaz
        assert pm["k_nearest"] == 50
        assert pm["value"] is not None and pm["lower_bound"] <= pm["value"]
        assert pm["lower_bound"] >= max(lk["lower_bound"], card["capacity"]["with_city"])
        assert pm["value"] >= lk["value"]
        d = PRE / f"default_{km}km.json"
        if d.exists():
            assert pm["value"] <= json.loads(d.read_text(encoding="utf-8"))["kpis"]["n_hospitals"] <= exp_J[km]
    # bağımsız doğrulamanın kanıtlı değerleri (Faz 3 doğrulaması C2/C3, kendi set-cover çözümüyle)
    assert {km: c["coverage_lscp"]["value"] for km, c in bc.items()} == {"5": 25, "3": 23, "2": 24}
    assert {km: c["coverage_lscp_k_list"]["value"] for km, c in bc.items()} == {"5": 26, "3": 27, "2": 34}


def test_benchmark_file_complete():
    """Kıyaslama dosyası eksiksiz ve tutarlı (hedeflere uyum raporda yorumlanır; burada yalnız veri denetlenir)."""
    b = _load(common.PROCESSED / "solver_benchmark.json")
    for km in ("5", "3", "2"):
        g = b["grids"][km]
        agg = g["aggregate"]
        assert agg["cold"]["all_checks_ok"] and ("warm" not in agg or agg["warm"]["all_checks_ok"])
        assert agg["cold"]["runs"] == len(g["runs"]) >= 3
        # sert süre sınırı (doğrulama #16) ve başarı oranı alanları
        assert agg["cold"]["within_hard_limit"] == agg["cold"]["runs"]
        assert agg["cold"]["success_rate"] == agg["cold"]["reached_1pct_within_target"] / agg["cold"]["runs"]
        for run in g["runs"]:
            c = run["cold"]
            assert c["lb_tl"] <= c["ub_tl"] and c["lb_source"] == "lagrange"
            assert (c["status"] == "optimal") == (c["gap"] <= 0.01 + 1e-9)
            t1 = c["time_to_gap_s"]["0.01"]
            assert t1 is None or t1 <= c["times"]["total_s"] + 1e-6
            assert c["times"]["total_s"] <= agg["time_limit_s"] + 0.5
        assert agg["target_s"] == SPEC_TARGET_S.get(int(km))
    assert "trace" in b["grids"]["3"]["runs"][0]["cold"]
    assert set(b["previous"]["grids"]) == {"5", "3", "2"}           # önceki kıyaslamanın özetleri korunur
