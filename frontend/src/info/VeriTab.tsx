/**
 * "Veri" sekmesi (şartname 7.12 ve hocanın 1., 3a., 4. maddeleri): üç alt bölüm —
 *   Ankara ölçüleri (/api/measures: "toplam mesafe" kartı, alan kaynakları karşılaştırması, hastane arsası × hücre),
 *   Nüfus 2026 (TÜİK toplamı, kalibrasyon özeti, ilçe tablosu),
 *   Parametreler (69 parametre A–I: değer + birim, durum rozeti, kaynaklar → Kaynakça, gerekçe; aranabilir).
 * Alt bölüm seçimi infoStore'da (atlamalar seçebilsin).
 */
import { SegmentedControl, Stack } from '@mantine/core';

import { useInfo, type VeriSection } from './infoStore.ts';
import { MeasuresSection } from './MeasuresSection.tsx';
import { ParamTable } from './ParamTable.tsx';
import { PopulationSection } from './PopulationSection.tsx';

const SECTIONS: { value: VeriSection; label: string }[] = [
  { value: 'olculer', label: 'Ankara ölçüleri' },
  { value: 'nufus', label: 'Nüfus 2026' },
  { value: 'parametreler', label: 'Parametreler' },
];

export default function VeriTab() {
  const section = useInfo((s) => s.veriSection);
  const setSection = useInfo((s) => s.setVeriSection);
  return (
    <Stack gap="md" data-testid="veri-tab">
      <SegmentedControl
        value={section}
        onChange={(v) => setSection(v as VeriSection)}
        data={SECTIONS}
        style={{ alignSelf: 'flex-start' }}
        aria-label="Veri bölümü"
        data-testid="veri-sections"
      />
      {section === 'olculer' && <MeasuresSection />}
      {section === 'nufus' && <PopulationSection />}
      {section === 'parametreler' && <ParamTable />}
    </Stack>
  );
}
