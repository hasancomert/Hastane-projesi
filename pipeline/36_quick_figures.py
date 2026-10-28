"""Sunum için hızlı statik grafikler (varsayılan parametreler, 3 km; önceden hesaplanmış JSON'lardan).

Çıktı: docs/screenshots/faz6_pareto_3km.png, faz6_tornado_3km.png, faz6_maliyet_3km.png
Kaynak: precomputed/pareto_3km.json, precomputed/tornado_3km.json, precomputed/default_3km.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pipeline import common

OUT = common.ROOT / "docs" / "screenshots"
PRE = common.ROOT / "precomputed"
CLASS_COLORS = {"IV-C": "#2a78d6", "V-B": "#1baf7a", "V-C": "#eb6834", "V-D": "#9a3c8e"}
plt.rcParams.update({"font.size": 13, "axes.spines.top": False, "axes.spines.right": False})


def tr(x: float, nd: int = 1) -> str:
    return common.fmt_tr(x, nd)


def pareto() -> None:
    d = json.loads((PRE / "pareto_3km.json").read_text(encoding="utf-8"))
    pts = [p for p in d["points"] if p.get("ok")]
    pts.sort(key=lambda p: p["factor"])
    x = [p["pop_weighted_mean_km"] for p in pts]
    y = [p["opening_tl"] / 1e9 for p in pts]
    fig, ax = plt.subplots(figsize=(13, 5.2), dpi=120)
    ax.plot(x, y, "-o", color="#2a78d6", lw=2)
    show = {0.25, 0.5, 1.0, 2.0, 3.0, 4.0}
    for p, xi, yi in zip(pts, x, y):
        if not any(abs(p["factor"] - f) < 1e-9 for f in show):
            continue
        ax.annotate(f"λ×{tr(p['factor'], 2)}\n{p['n_hospitals']} hst.", (xi, yi), textcoords="offset points",
                    xytext=(6, 6), fontsize=10, color="#444")
    cur = [p for p in pts if abs(p["factor"] - 1.0) < 1e-9]
    if cur:
        c = cur[0]
        ax.plot([c["pop_weighted_mean_km"]], [c["opening_tl"] / 1e9], "o", ms=16, mfc="none", mec="#d62728", mew=2.5,
                label=f"mevcut çözüm (λ×1): {c['n_hospitals']} hastane")
        ax.legend(loc="upper right", frameon=False)
    ax.set_xlabel("nüfus ağırlıklı ortalama yol mesafesi (km)")
    ax.set_ylabel("açılış maliyeti (milyar TL)")
    ax.set_ylim(min(y) - 1.5, max(y) + 3.5)
    ax.set_xlim(min(x) - 0.05, max(x) + 0.15)
    ax.set_title("Pareto: açılış maliyeti ↔ ortalama mesafe (erişim ağırlığı λ taranarak), 3 km, varsayılan parametreler",
                 loc="left", fontsize=13)
    fig.text(0.01, 0.01, "Kaynak: precomputed/pareto_3km.json (pipeline/35_explanations.py). Her nokta, erişim maliyeti "
             "λ katsayısıyla ölçeklenerek çözülmüş en iyi çözümdür; sola-yukarı = daha çok hastane, kısa mesafe.",
             fontsize=9, color="#555")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(OUT / "faz6_pareto_3km.png")
    plt.close(fig)


def tornado() -> None:
    d = json.loads((PRE / "tornado_3km.json").read_text(encoding="utf-8"))
    rows = [r for r in d["rows"] if r.get("low", {}).get("ok") and r.get("high", {}).get("ok")]
    rows.sort(key=lambda r: r.get("range_pct", 0.0))
    fig, ax = plt.subplots(figsize=(13, 0.48 * len(rows) + 1.6), dpi=120)
    for i, r in enumerate(rows):
        lo, hi = r["low"]["delta_pct"] * 100, r["high"]["delta_pct"] * 100
        ax.barh(i, lo, color="#2a78d6", height=0.6)
        ax.barh(i, hi, color="#eb6834", height=0.6)
        nlo, nhi = r["low"].get("delta_n", 0), r["high"].get("delta_n", 0)
        ax.text(min(lo, hi) - 0.3, i, f"{nlo:+d}/{nhi:+d} hst.", va="center", ha="right", fontsize=9, color="#555")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r["label_tr"][:58] for r in rows], fontsize=10)
    ax.axvline(0, color="#333", lw=1)
    ax.set_xlabel("toplam maliyet değişimi (%) — mavi: parametre −%20, turuncu: +%20")
    ax.set_title("Tornado: önemli parametreler ±%20 → toplam maliyet (yanında hastane sayısı değişimi), 3 km",
                 loc="left", fontsize=13)
    lo_x = min(r["low"]["delta_pct"] * 100 for r in rows) - 4
    hi_x = max(r["high"]["delta_pct"] * 100 for r in rows) + 2
    ax.set_xlim(min(lo_x, -2), max(hi_x, 2))
    fig.text(0.01, 0.005, "Kaynak: precomputed/tornado_3km.json. Her çubuk, yalnız o parametre değiştirilip model "
             "yeniden çözülerek elde edildi (gap ≤ %1).", fontsize=9, color="#555")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(OUT / "faz6_tornado_3km.png")
    plt.close(fig)


def maliyet() -> None:
    d = json.loads((PRE / "default_3km.json").read_text(encoding="utf-8"))
    c = d.get("costs_tl") or d.get("costs")
    parts = [("İnşaat (yapı)", c["yapi"]), ("KDV", c["kdv"]), ("Altyapı ve çevre", c["altyapi"]),
             ("Tıbbi donanım", c["donanim"]), ("Sabit açılış", c["sabit"]), ("Arsa", c.get("arsa", 0.0)),
             ("İşletme", c.get("isletme", 0.0)), ("Erişim (25 yıl)", c["erisim"])]
    parts = [(k, v) for k, v in parts if v > 0]
    tot = sum(v for _, v in parts)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), dpi=120, gridspec_kw={"width_ratios": [3, 2]})
    ax = axes[0]
    cols = ["#2a78d6", "#eb6834", "#1baf7a", "#e0a800", "#d64f9b", "#888", "#999", "#127a3a"]
    ax.barh(range(len(parts)), [v / 1e9 for _, v in parts], color=cols[:len(parts)])
    ax.set_yticks(range(len(parts)))
    ax.set_yticklabels([k for k, _ in parts])
    ax.invert_yaxis()
    for i, (_, v) in enumerate(parts):
        ax.text(v / 1e9 + 2, i, f"{tr(v / 1e9, 2)} milyar TL (%{tr(100 * v / tot, 1)})", va="center", fontsize=11)
    ax.set_xlabel("milyar TL (2026 fiyatları, bugünkü değer)")
    ax.set_xlim(0, max(v for _, v in parts) / 1e9 * 1.55)
    ax.set_title(f"Maliyet kırılımı — toplam {tr(tot / 1e9, 1)} milyar TL", loc="left", fontsize=13)
    ax2 = axes[1]
    hs = d.get("hospitals", [])
    by = {}
    for h in hs:
        k = h.get("class") or h.get("cls") or h.get("class_code")
        by.setdefault(k, [0, 0.0])
        by[k][0] += 1
        by[k][1] += float(h.get("beds", 0))
    ks = [k for k in ("IV-C", "V-B", "V-C", "V-D") if k in by]
    ax2.bar(range(len(ks)), [by[k][0] for k in ks], color=[CLASS_COLORS[k] for k in ks])
    for i, k in enumerate(ks):
        ax2.text(i, by[k][0] + 1, f"{by[k][0]} hastane\n{tr(by[k][1], 0)} yatak", ha="center", fontsize=11)
    ax2.set_xticks(range(len(ks)))
    names = {"IV-C": "küçük\n(50–199)", "V-B": "orta\n(200–399)", "V-C": "büyük\n(400–999)", "V-D": "şehir\n(≥1.000)"}
    ax2.set_xticklabels([names[k] for k in ks])
    ax2.set_ylim(0, max(by[k][0] for k in ks) * 1.35)
    ax2.set_title("Sınıflara göre hastaneler", loc="left", fontsize=13)
    fig.text(0.01, 0.01, "Kaynak: precomputed/default_3km.json (varsayılan parametreler, 3 km).", fontsize=9, color="#555")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(OUT / "faz6_maliyet_3km.png")
    plt.close(fig)


if __name__ == "__main__":
    pareto()
    tornado()
    maliyet()
    print("yazıldı:", [p.name for p in sorted(OUT.glob("faz6_*_3km.png"))])
