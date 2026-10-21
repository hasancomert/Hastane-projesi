"""Faz 1 — ilçe kontrol tablosu: nüfus ızgarası kaynakları / TÜİK 2025 (kalibrasyon öncesi).

Girdiler
  data/reference/tuik_adnks_ankara.csv     TÜİK ADNKS 2025 ilçe nüfusları (resmî yazım, ilce_key)
  data/processed/ilce_nufus_kaynaklar.csv  ızgara toplamları (GHS-POP, WorldPop, Kontur; piksel merkezi kuralı)
  data/reference/parameters.json           population.province_2025 (yoksa common sabiti)

Çıktılar
  data/processed/ilce_kontrol_tablosu.csv  25 ilçe + il toplamı; oranlar ve 0,7–1,3 dışı bayrakları
  data/processed/ilce_kontrol.png          oran çubuk grafiği

Çalıştırma (repo kökünden):  .venv/bin/python pipeline/16_district_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

TUIK_CSV = common.REFERENCE / "tuik_adnks_ankara.csv"
GRID_CSV = common.PROCESSED / "ilce_nufus_kaynaklar.csv"
PARAMS_JSON = common.REFERENCE / "parameters.json"
OUT_CSV = common.PROCESSED / "ilce_kontrol_tablosu.csv"
OUT_PNG = common.PROCESSED / "ilce_kontrol.png"

LOW, HIGH = 0.7, 1.3   # kabul bandı (görev tanımı)

# kaynak sütunu -> (kısa ad, grafik etiketi, renk). Renkler: kategorik 1–3 (CVD denetiminden geçti).
SOURCES = {
    "ghs_e2025_100m": ("ghs_e2025", "GHS-POP R2023A E2025 (100 m)", "#2a78d6"),
    "worldpop_2025_100m": ("worldpop_2025", "WorldPop R2025A 2025 (100 m)", "#eb6834"),
    "kontur_20231101_h3r8": ("kontur", "Kontur 2023-11-01 (H3 r8)", "#1baf7a"),
}
INK, INK2, SURFACE, BAND = "#0b0b0b", "#52514e", "#fcfcfb", "#e9e8e4"


def province_total() -> int:
    """İl nüfusu: kayıttaki population.province_2025."""
    if PARAMS_JSON.exists():
        return int(json.loads(PARAMS_JSON.read_text(encoding="utf-8"))["population.province_2025"]["value"])
    return common.TUIK_PROVINCE_POP_2025


def build_table() -> pd.DataFrame:
    tuik = pd.read_csv(TUIK_CSV, dtype={"ilce_key": str})
    grid = pd.read_csv(GRID_CSV, dtype={"ilce_key": str})
    grid = grid[grid["satir_turu"] == "ilce"]

    # 1:1 anahtar eşleşmesi (25 ↔ 25)
    for name, df in (("TÜİK", tuik), ("ızgara", grid)):
        if len(df) != 25 or df["ilce_key"].nunique() != 25:
            raise SystemExit(f"{name} tablosunda 25 benzersiz ilçe bekleniyordu: {len(df)} satır")
    kt, kg = set(tuik["ilce_key"]), set(grid["ilce_key"])
    if kt != kg:
        raise SystemExit(f"ilce_key eşleşmiyor: yalnız TÜİK {sorted(kt - kg)}, yalnız ızgara {sorted(kg - kt)}")
    bad = [r.ilce for r in tuik.itertuples() if common.norm_name(r.ilce) != r.ilce_key]
    if bad:
        raise SystemExit(f"ilce_key norm_name(ilce) ile uyuşmuyor: {bad}")

    total = province_total()
    if int(tuik["nufus_2025"].sum()) != total:
        raise SystemExit(f"TÜİK ilçe toplamı {tuik['nufus_2025'].sum()} ≠ il nüfusu {total}")

    cols = list(SOURCES)
    df = tuik[["ilce", "ilce_key", "nufus_2025", "nufus_2023"]].merge(
        grid[["ilce_key", "area_km2", *cols]], on="ilce_key", how="inner", validate="one_to_one")
    df = df.rename(columns={"nufus_2025": "tuik_2025", "nufus_2023": "tuik_2023"})
    tot = {"ilce": "Ankara (25 ilçe toplamı)", "ilce_key": "toplam",
           **{c: df[c].sum() for c in ["tuik_2025", "tuik_2023", "area_km2", *cols]}}
    df = pd.concat([df, pd.DataFrame([tot])], ignore_index=True)

    flags = []
    for c, (short, _, _) in SOURCES.items():
        df[f"oran_{short}"] = (df[c] / df["tuik_2025"]).round(4)
        df[f"disi_{short}"] = ~df[f"oran_{short}"].between(LOW, HIGH)
        flags.append(short)
    # Kontur'un tarihi 2023 sonu: bilgi amaçlı TÜİK 2023 oranı (bayraklanmaz)
    df["oran_kontur_tuik2023"] = (df["kontur_20231101_h3r8"] / df["tuik_2023"]).round(4)
    df["aralik_disi"] = df.apply(lambda r: ";".join(s for s in flags if r[f"disi_{s}"]), axis=1)
    df = df.drop(columns=[f"disi_{s}" for s in flags])
    order = (["ilce", "ilce_key", "tuik_2025", "area_km2", *cols]
             + [f"oran_{s}" for s in flags] + ["aralik_disi", "tuik_2023", "oran_kontur_tuik2023"])
    df = df[order]
    df["area_km2"] = df["area_km2"].round(3)
    for c in cols:
        df[c] = df[c].round(1)
    return df


def plot(df: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    d = df[df["ilce_key"] != "toplam"].sort_values("tuik_2025", ascending=True)
    t = df[df["ilce_key"] == "toplam"].iloc[0]
    rows = [t] + [r for _, r in d.iterrows()]   # toplam en altta, en kalabalık ilçe en üstte
    labels = [f"{r['ilce']}  ({common.fmt_tr(r['tuik_2025'])})" for r in rows]

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    fig, ax = plt.subplots(figsize=(9.5, 12.5), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.axvspan(LOW, HIGH, color=BAND, zorder=0, lw=0)
    ax.axvline(1.0, color=INK2, lw=1, zorder=1)

    n = len(SOURCES)
    h = 0.8 / n
    xmax = max(float(df[[f"oran_{s[0]}" for s in SOURCES.values()]].max().max()) * 1.12, 1.6)
    for k, (c, (short, label, color)) in enumerate(SOURCES.items()):
        ys = [i + (n - 1 - k - (n - 1) / 2) * h for i in range(len(rows))]
        vals = [r[f"oran_{short}"] for r in rows]
        ax.barh(ys, vals, height=h, color=color, edgecolor=SURFACE, linewidth=1.2, label=label, zorder=2)
        for y, v in zip(ys, vals):
            if not (LOW <= v <= HIGH):   # yalnız bant dışı değerler etiketlenir
                ax.text(v + xmax * 0.008, y, common.fmt_tr(v, 2), va="center", ha="left", fontsize=7, color=INK)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(labels, color=INK)
    ax.get_yticklabels()[0].set_fontweight("bold")
    ax.axhline(0.5, color=INK2, lw=0.6, ls=(0, (2, 2)))
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(0, xmax)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: common.fmt_tr(x, 1)))
    ax.grid(axis="x", color="#d9d8d3", lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(INK2)
    ax.tick_params(axis="both", colors=INK2, length=0)
    ax.set_xlabel("Kaynak toplamı / TÜİK 2025 ilçe nüfusu", color=INK)
    fig.suptitle("İlçe nüfusu kontrolü: ızgara kaynakları / TÜİK ADNKS 2025 (kalibrasyon öncesi)",
                 x=0.02, ha="left", fontsize=12, color=INK, y=0.995)
    ax.set_title(f"Gri bant {common.fmt_tr(LOW, 1)}–{common.fmt_tr(HIGH, 1)} kabul aralığı; bant dışı değerler "
                 "etiketli. Parantez içi TÜİK 2025 nüfusu.", loc="left", fontsize=9, color=INK2, pad=28)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=8.5,
              handlelength=1.2, columnspacing=1.4, labelcolor=INK)
    fig.text(0.02, 0.004, "Kaynak: TÜİK ADNKS 2025; GHS-POP R2023A (JRC), WorldPop R2025A, Kontur Population; "
             "ilçe toplamları piksel/hücre merkezi kuralıyla (data/processed/ilce_nufus_kaynaklar.csv).",
             fontsize=7, color=INK2)
    fig.tight_layout(rect=(0, 0.012, 1, 0.985))
    fig.savefig(OUT_PNG, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    df = build_table()
    df.to_csv(OUT_CSV, index=False, encoding="utf-8")
    plot(df)

    shorts = [s[0] for s in SOURCES.values()]
    print(f"{'İlçe':26s} {'TÜİK 2025':>11s} " + " ".join(f"{s:>13s}" for s in shorts) + "  bant dışı")
    for _, r in df.iterrows():
        print(f"{r['ilce']:26s} {common.fmt_tr(r['tuik_2025']):>11s} "
              + " ".join(f"{common.fmt_tr(r[f'oran_{s}'], 3):>13s}" for s in shorts) + f"  {r['aralik_disi']}")
    body = df[df["ilce_key"] != "toplam"]
    for s in shorts:
        out = body[~body[f"oran_{s}"].between(LOW, HIGH)]
        print(f"{s}: {len(out)}/25 ilçe {common.fmt_tr(LOW, 1)}–{common.fmt_tr(HIGH, 1)} dışında")
    print(f"Yazıldı: {OUT_CSV.relative_to(common.ROOT)}, {OUT_PNG.relative_to(common.ROOT)}")


if __name__ == "__main__":
    main()
