/**
 * Bilgi sekmeleri (şartname 6.1, 7.11–7.14): Hocanın Listesi ✓ · Veri · Model · Sınırlılıklar · Kaynakça.
 *
 * Her sekmenin içeriği tembel yüklenir (KaTeX yalnız Model sekmesiyle; ilk açılış hafif kalır). Sekmelerin içindeki
 * her bölümün bir çapası (`id`) vardır; sayfanın herhangi bir yerinden `jumpTo()` (info/jumps.ts) ya da
 * `hastane:jump` olayı, parametre bilgi kutusundaki "Kaynakçada" düğmesi (`hastane:kaynak` olayı) bir çapaya
 * kaydırma ister — sekme ve verisi hazır olunca burada bulunur, kaydırılır ve kısa bir halkayla vurgulanır.
 *
 * Kaynak dosyalar: HocaTab (9 madde), VeriTab (MeasuresSection · PopulationSection · ParamTable), ModelTab (KaTeX),
 * LimitationsTab, BibliographyTab; ortak: jumps.ts (atlamalar), infoStore.ts (depo + veri), facts.ts (canlı sayılar).
 */
import { Badge, Group, Tabs, Text } from '@mantine/core';
import { lazy, Suspense, useEffect, type ComponentType, type LazyExoticComponent } from 'react';

import { ErrorBoundary } from '../components/ErrorBoundary.tsx';
import { useApp, type InfoTabId } from '../store/appStore.ts';
import { useInfo } from './infoStore.ts';
import { flash, HOCA_ITEMS, installJumpListener, settleScroll, waitForElement } from './jumps.ts';
import './info.css';

export interface InfoTabDef {
  id: InfoTabId;
  label: string;
  description: string;
  load: () => Promise<{ default: ComponentType }>;
}

export const INFO_TABS: InfoTabDef[] = [
  { id: 'hoca', label: 'Hocanın Listesi', description: 'Hocanın 9 maddesi: her biri ✓ ve sayfadaki ilgili yere bağlantıyla.', load: () => import('./HocaTab.tsx') },
  { id: 'veri', label: 'Veri', description: 'Ankara ölçüleri, nüfus 2026 ve kalibrasyon; her parametre: değeri, birimi, durumu, kaynağı ya da gerekçesi.', load: () => import('./VeriTab.tsx') },
  { id: 'model', label: 'Model', description: 'Formülasyon (KaTeX), her kısıtın sade Türkçe açıklaması, alt sınırın kaynağı.', load: () => import('./ModelTab.tsx') },
  { id: 'sinirliliklar', label: 'Sınırlılıklar', description: 'Modelin bilerek basitleştirdiği yerler ve sonuca etkileri.', load: () => import('./LimitationsTab.tsx') },
  { id: 'kaynakca', label: 'Kaynakça', description: 'Veri kaynakları (erişim tarihleriyle) ve klasik literatür (DOI’leriyle).', load: () => import('./BibliographyTab.tsx') },
];

const lazyCache = new Map<InfoTabId, LazyExoticComponent<ComponentType>>();
function lazyTab(def: InfoTabDef) {
  let c = lazyCache.get(def.id);
  if (!c) {
    c = lazy(def.load);
    lazyCache.set(def.id, c);
  }
  return c;
}

/** Bekleyen çapa isteğini karşıla: öğe görünür olunca kaydır + vurgula. */
function useAnchorRequests() {
  const pending = useInfo((s) => s.pendingAnchor);
  useEffect(() => {
    if (!pending) return;
    let cancelled = false;
    const find = () =>
      pending === 'info-tabs-top' ? document.querySelector<HTMLElement>('[data-testid="info-tabs"]') : document.getElementById(pending);
    void waitForElement(find, 8000).then((el) => {
      if (cancelled) return;
      if (useInfo.getState().pendingAnchor === pending) useInfo.getState().setPendingAnchor(null);
      const target = el ?? document.querySelector<HTMLElement>('[data-testid="info-tabs"]');
      if (!target) return;
      if (el && pending !== 'info-tabs-top') flash(el);
      void settleScroll(target, 'start');
    });
    return () => {
      cancelled = true;
    };
  }, [pending]);
}

export function InfoTabs() {
  const tab = useApp((s) => s.ui.infoTab);
  const setUi = useApp((s) => s.setUi);

  useEffect(() => {
    installJumpListener();
    // parametre bilgi kutusundaki "Kaynakçada" (panels/ParamInfo.tsx) → kaynağın satırına kaydır
    const onSource = (e: Event) => {
      const id = (e as CustomEvent<{ id?: string }>).detail?.id;
      if (id) useInfo.getState().setPendingAnchor(`kaynak-${id}`);
    };
    window.addEventListener('hastane:kaynak', onSource);
    return () => window.removeEventListener('hastane:kaynak', onSource);
  }, []);
  useAnchorRequests();

  return (
    <div className="info-root" id="bilgi">
      <Tabs value={tab} onChange={(v) => v && setUi({ infoTab: v as InfoTabId })} keepMounted={false} data-testid="info-tabs">
        <Tabs.List>
          {INFO_TABS.map((t) => (
            <Tabs.Tab key={t.id} value={t.id} data-testid={`info-tab-${t.id}`}>
              {t.id === 'hoca' ? (
                <Group gap={6} wrap="nowrap">
                  <span>{t.label}</span>
                  <Badge size="sm" color="teal" variant="light" tt="none" aria-label={`${HOCA_ITEMS.length} maddenin hepsi karşılandı`}>
                    ✓ {HOCA_ITEMS.length}/{HOCA_ITEMS.length}
                  </Badge>
                </Group>
              ) : (
                t.label
              )}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        {INFO_TABS.map((t) => {
          const C = lazyTab(t);
          return (
            <Tabs.Panel key={t.id} value={t.id} pt="md" data-testid={`info-panel-${t.id}`}>
              <ErrorBoundary area={t.label}>
                <Suspense
                  fallback={
                    <Text c="dimmed" size="sm">
                      {t.label} yükleniyor…
                    </Text>
                  }
                >
                  <C />
                </Suspense>
              </ErrorBoundary>
            </Tabs.Panel>
          );
        })}
      </Tabs>
    </div>
  );
}

export default InfoTabs;
