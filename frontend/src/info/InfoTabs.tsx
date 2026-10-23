/**
 * Bilgi sekmeleri (şartname 6.1, 7.11–7.14): Hocanın Listesi ✓ · Veri · Model · Sınırlılıklar · Kaynakça.
 *
 * İSKELET — Faz 6 doldurur. KaTeX (Model sekmesi) YALNIZ tembel yüklenen bileşenden içe aktarılmalı
 * (`import 'katex/dist/katex.min.css'` dahil; yazı tipleri derlemeye gömülür, internet gerekmez).
 * Veri: Hocanın Listesi → 9 madde, ✓ + sayfadaki ilgili yere bağlantı; Veri → /api/meta parametre tablosu
 * (değer, birim, durum, kaynak) + /api/measures; Kaynakça → /api/sources (domains + literature).
 */
import { Paper, Tabs, Text } from '@mantine/core';

import { useApp, type InfoTabId } from '../store/appStore.ts';

export const INFO_TABS: { id: InfoTabId; label: string; description: string }[] = [
  { id: 'hoca', label: 'Hocanın Listesi', description: 'Hocanın 9 maddesi: her biri ✓ ve sayfadaki ilgili yere bağlantıyla.' },
  { id: 'veri', label: 'Veri', description: 'Her parametre: değeri, birimi, kaynağı ya da “varsayım” etiketi; Ankara ölçüleri ve nüfus kalibrasyonu.' },
  { id: 'model', label: 'Model', description: 'Formülasyon (KaTeX) ve her kısıtın sade Türkçe açıklaması.' },
  { id: 'sinirliliklar', label: 'Sınırlılıklar', description: 'Kuş uçuşu × dolambaç mesafesi, basamak ayrımı yok, komşu illerle hasta akışı yok, sabit maliyet varsayımı …' },
  { id: 'kaynakca', label: 'Kaynakça', description: 'Veri kaynakları (erişim tarihleriyle) ve klasik literatür (DOI’leriyle).' },
];

export function InfoTabs() {
  const tab = useApp((s) => s.ui.infoTab);
  const setUi = useApp((s) => s.setUi);
  return (
    <Tabs value={tab} onChange={(v) => v && setUi({ infoTab: v as InfoTabId })} keepMounted={false} data-testid="info-tabs">
      <Tabs.List>
        {INFO_TABS.map((t) => (
          <Tabs.Tab key={t.id} value={t.id}>
            {t.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
      {INFO_TABS.map((t) => (
        <Tabs.Panel key={t.id} value={t.id} pt="sm">
          <Paper withBorder p="md" radius="md">
            <Text fw={600}>{t.label}</Text>
            <Text size="sm" c="dimmed">
              {t.description}
            </Text>
            <Text size="xs" c="dimmed" mt="xs">
              Bu bölüm hazırlanıyor.
            </Text>
          </Paper>
        </Tabs.Panel>
      ))}
    </Tabs>
  );
}

export default InfoTabs;
