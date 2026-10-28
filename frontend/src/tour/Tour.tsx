/**
 * Sunum turu (şartname 6.5): hocanın 9 maddesini sırayla anlatır; her adım haritayı / sekmeyi ilgili yere götürür
 * (info/jumps.ts `jumpTo`). Ağır canlı çözüm başlatmaz; gösterilen sonucun sayılarını kullanır.
 * Klavye (ve sunum kumandası): → / PageDown ileri, ← / PageUp geri, Esc kapat.
 */
import { ActionIcon, Button, CloseButton, Group, Paper, Portal, Progress, Text, Tooltip } from '@mantine/core';
import { useEffect } from 'react';
import { create } from 'zustand';

import type { SolveResultOk } from '../api/types.ts';
import { fmtInt, fmtNumber } from '../format/number.ts';
import { jumpTo, type JumpId } from '../info/jumps.ts';
import { selectOkResult, useApp } from '../store/appStore.ts';

interface TourStep {
  title: string;
  jump: JumpId;
  text: (r: SolveResultOk | null) => string;
}

const pct = (x: number | undefined | null, digits = 1) => (x == null ? '—' : `%${fmtNumber(x * 100, digits)}`);
const milyar = (tl: number | undefined | null) => (tl == null ? '—' : `${fmtNumber(tl / 1e9, 1)} milyar TL`);

const STEPS: TourStep[] = [
  {
    title: 'Problem',
    jump: 'harita',
    text: () =>
      'Ankara’yı kare hücrelere böldük; her hücre hem talep noktası hem aday hastane yeri. Model hangi hücreye, hangi ' +
      'boyutta hastane açılacağına ve her hücrenin hangi hastaneye gideceğine aynı anda karar verir. Amaç: açılış maliyeti + ' +
      '25 yıllık erişim maliyetinin toplamını en küçüklemek. Mevcut hastaneler yok sayıldı — sıfırdan planlama (madde 6).',
  },
  {
    title: 'Madde 1 · Ankara nüfusu 2026 (ve madde 7: yoğunluk)',
    jump: 'nufus_harita',
    text: (r) =>
      `TÜİK ADNKS 31.12.2025 (= 2026 başı): ${r ? fmtInt(r.kpis.population) : '5.910.320'} kişi. WorldPop 100 m nüfus ` +
      'dağılımı her ilçede TÜİK toplamına ölçeklendi; il ve 25 ilçe TÜİK ile birebir. Harita: hücre nüfus yoğunluğu ' +
      '(log ölçek) — yoğunluk, erişim maliyetinin ağırlığıdır.',
  },
  {
    title: 'Madde 2 ve 4 · Ankara haritası ve toplam mesafe',
    jump: 'olculer_harita',
    text: () =>
      'İl sınırı poligonundan hesap: alan 25.647,9 km², çevre 1.590,3 km; doğu–batı 261,1 km, kuzey–güney 232,2 km. ' +
      'En uzak iki sınır noktası 293,8 km (Nallıhan – Şereflikoçhisar); Kızılay’dan en uzak sınır noktası 173,4 km.',
  },
  {
    title: 'Madde 4 · Kaynaklardaki yüzölçümleri',
    jump: 'olculer',
    text: () =>
      'Kaynaklar farklı yüzölçümleri veriyor (24.521 – 26.897 km²): göller dahil/hariç ve sınır çözünürlüğü farkı. ' +
      'Biz poligondan kendimiz hesapladık; tablo, her kaynağın bizden farkını gösteriyor.',
  },
  {
    title: 'Madde 5 · Izgaraya kaç hastane sığar?',
    jump: 'aday',
    text: (r) =>
      `Her hücrede en fazla 1 hastane. İl içinde, büyük kısmı su olmayan ve yerleşim olan/yerleşime yakın hücreler aday: ` +
      `|J| = ${r?.instance?.J != null ? fmtInt(r.instance.J) : '—'} — ızgaraya sığabilecek en fazla hastane. ` +
      'Sonuç panelindeki "Kaç hastane? En az / en fazla" kartı alt ve üst sınırları gösterir.',
  },
  {
    title: 'Madde 3a · Hastane alanı (inşaat / arsa)',
    jump: 'arsa',
    text: () =>
      '300 yataklı genel hastanenin arsası 300 × 130 m² = 39.000 m² — 3 km’lik hücrenin yalnız %0,43’ü. ' +
      'Arazi bağlayıcı değil; bağlayıcı olan talep ve maliyet.',
  },
  {
    title: 'Madde 9 · Hastanelerin açılış maliyeti',
    jump: 'model_maliyet',
    text: () =>
      'İnşaat: 2026 Yapı Yaklaşık Birim Maliyetleri Tebliği (sınıfa göre TL/m²) × yatak başı alan (2026 Yatırım Programı ' +
      'projeleri) + çekirdek alan; üstüne KDV, altyapı, tıbbi donanım ve hastane başı sabit maliyet. Erişim maliyeti: kişi ' +
      'başı hastane başvurusu × gidiş-dönüş × (yakıt + zaman), 25 yıl %5 iskontoyla.',
  },
  {
    title: 'Madde 8 · Kaç hastane, nerede, hangi boyutta?',
    jump: 'kac_nerede',
    text: (r) =>
      r
        ? `Optimum: ${fmtInt(r.kpis.n_hospitals)} hastane (${fmtInt(r.kpis.count_by_class['IV-C'] ?? 0)} küçük, ` +
          `${fmtInt(r.kpis.count_by_class['V-B'] ?? 0)} orta, ${fmtInt(r.kpis.count_by_class['V-C'] ?? 0)} büyük, ` +
          `${fmtInt(r.kpis.count_by_class['V-D'] ?? 0)} şehir hastanesi); toplam ${milyar(r.costs.toplam)}. ` +
          `10 km içinde hastanesi olan nüfus: ${pct(r.kpis.share_pop_within_km['10'])}; nüfus ağırlıklı ortalama yol ` +
          `${fmtNumber(r.kpis.pop_weighted_mean_km, 1)} km.`
        : 'Sonuç yükleniyor…',
  },
  {
    title: 'Madde 3b · Hizmet bölgeleri',
    jump: 'hizmet',
    text: () =>
      'Her hücre, hizmet aldığı hastanenin renginde. Bir hastaneye tıklayınca kartı açılır: hizmet ettiği nüfus, ' +
      'mesafeler, yatak sayısı ve maliyet kırılımı.',
  },
  {
    title: 'Neden çok sayıda küçük hastane?',
    jump: 'model_neden',
    text: () =>
      'Tebliğde m² fiyatı hastane sınıfıyla artıyor; bu yüzden yatak başı maliyet büyük hastanede daha yüksek, hastane başı ' +
      'sabit kısım ise görece küçük. Varsayılan çözümde hastaneler 199 ve 399 yatakta (sınıf eşiklerinin hemen altında) ' +
      'yığılıyor. Uzmanlaşmanın (üçüncü basamak) faydası modellenmedi; bu yüzden şehir hastanesi seçilmiyor.',
  },
  {
    title: 'Neden optimum? · Optimallik sertifikası',
    jump: 'sertifika',
    text: (r) =>
      r && r.bounds.lb_tl != null && r.bounds.ub_tl != null
        ? `Alt sınır ${milyar(r.bounds.lb_tl)} ≤ optimum ≤ bulunan çözüm ${milyar(r.bounds.ub_tl)}; fark ` +
          `${pct(r.bounds.gap, 2)}. Yani bulunan çözüm, gerçek optimumdan en fazla ${pct(r.bounds.gap, 2)} uzakta.`
        : 'Alt sınır ≤ optimum ≤ bulunan çözüm; aradaki fark optimallik garantisidir.',
  },
  {
    title: 'Alt sınır nereden geliyor?',
    jump: 'model_alt_sinir',
    text: () =>
      'Atama kısıtları Lagrange çarpanlarıyla gevşetilir; problem her aday hücre için küçük bir sırt çantası problemine ' +
      'ayrışır ve saniyeler içinde geçerli bir alt sınır verir (alt gradyan / hacim algoritması). HiGHS, seçilmiş aday ' +
      'alt kümesinde dal-sınırla çözümü iyileştirir.',
  },
  {
    title: 'U eğrisi · Neden bu sayı?',
    jump: 'u_egrisi',
    text: () =>
      'Hastane sayısı p sabitken en iyi çözüm: az hastanede erişim, çok hastanede açılış maliyeti artar. Taramanın en iyisi ' +
      'serbest optimumla aynı. Dip çok düz: yakın p değerleri arasındaki farklar çözücü farkının içinde.',
  },
  {
    title: 'Duyarlılık · Tornado',
    jump: 'tornado',
    text: () =>
      'Her önemli parametre ±%20 değiştirilip model yeniden çözüldü. En etkili olanlar: yatak oranı, yapı birim maliyetleri ' +
      've yatak başı alan. Azami erişim mesafesi R kısalınca hastane sayısı artar.',
  },
  {
    title: 'Kapanış · Kaynaklar ve varsayımlar',
    jump: 'kaynakca',
    text: () =>
      'Her parametrenin değeri, birimi ve kaynağı ya da "varsayım" etiketi Veri sekmesinde; veri kaynakları ve klasik ' +
      'literatür (Hakimi 1964, ReVelle & Swain 1970, Toregas vd. 1971, Church & ReVelle 1974) Kaynakça’da. Teşekkürler!',
  },
];

interface TourState {
  open: boolean;
  step: number;
  /** Kartın köşesi; kart bir şeyi örtüyorsa sunumda tek tıkla karşı köşeye alınır. */
  side: 'right' | 'left';
  toggleSide: () => void;
  start: () => void;
  close: () => void;
  go: (i: number) => void;
}

export const useTour = create<TourState>()((set) => ({
  open: false,
  step: 0,
  side: 'right',
  toggleSide: () => set((s) => ({ side: s.side === 'right' ? 'left' : 'right' })),
  start: () => set({ open: true, step: 0 }),
  close: () => set({ open: false }),
  go: (i) => set({ step: Math.max(0, Math.min(STEPS.length - 1, i)) }),
}));

export function startTour(): void {
  useTour.getState().start();
}

export function TourCard() {
  const { open, step, close, go, side, toggleSide } = useTour();
  const result = useApp(selectOkResult) as SolveResultOk | null;
  const s = STEPS[step];

  useEffect(() => {
    if (open) void jumpTo(STEPS[step].jump);
  }, [open, step]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        close();
        return;
      }
      const t = e.target as HTMLElement | null;
      if (t?.closest?.('input, textarea, select, [contenteditable="true"], [role="slider"], [role="tab"], [role="combobox"]')) return;
      if (e.key === 'ArrowRight' || e.key === 'PageDown') {
        e.preventDefault();
        go(useTour.getState().step + 1);
      } else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
        e.preventDefault();
        go(useTour.getState().step - 1);
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [open, go, close]);

  if (!open || !s) return null;
  const last = step === STEPS.length - 1;
  return (
    <Portal>
      <Paper
        data-testid="tour-card"
        shadow="xl"
        radius="md"
        withBorder
        p="md"
        style={{ position: 'fixed', [side]: 16, bottom: 16, width: 'min(480px, calc(100vw - 32px))', zIndex: 1000 }}
      >
        <Group justify="space-between" align="center" mb={4} wrap="nowrap">
          <Text size="sm" c="dimmed" fw={600}>
            Sunum turu · {step + 1} / {STEPS.length}
          </Text>
          <Group gap={4} wrap="nowrap">
            <Tooltip label="Kartı diğer köşeye taşı">
              <ActionIcon variant="subtle" color="gray" aria-label="Kartı diğer köşeye taşı" onClick={toggleSide} data-testid="tour-side">
                ⇄
              </ActionIcon>
            </Tooltip>
            <CloseButton aria-label="Turu kapat" onClick={close} />
          </Group>
        </Group>
        <Progress value={((step + 1) / STEPS.length) * 100} size="sm" mb="sm" />
        <Text fw={700} size="lg" mb={6}>
          {s.title}
        </Text>
        <Text size="md" style={{ lineHeight: 1.45 }}>
          {s.text(result)}
        </Text>
        <Group justify="space-between" mt="md">
          <Button variant="default" onClick={() => go(step - 1)} disabled={step === 0}>
            ← Geri
          </Button>
          <Text size="xs" c="dimmed">
            ← → ile gezin · Esc kapatır
          </Text>
          {last ? (
            <Button onClick={close}>Bitir</Button>
          ) : (
            <Button onClick={() => go(step + 1)}>İleri →</Button>
          )}
        </Group>
      </Paper>
    </Portal>
  );
}

export default TourCard;
