/**
 * Sağ tık menüsü (şartname 6.5 "kilitle / yasakla"): imlecin altındaki hücre için kart aç, hastaneyi zorunlu kıl,
 * yasakla, kilidi kaldır, tüm kilitleri temizle. Kilit değişince depo problemi değişir → otomatik yeniden çözüm.
 * Çevrimdışıyken kilit öğeleri kapalı ve nedeni yazılı.
 */
import { Menu, Text } from '@mantine/core';

import { selectCanSolve, useApp } from '../store/appStore.ts';
import { hospitalTitle } from './tooltipContent.ts';
import { useMapUi } from './mapUiStore.ts';
import { useShownSolution } from './useShownSolution.ts';

export function MapContextMenu() {
  const menu = useMapUi((s) => s.menu);
  const setMenu = useMapUi((s) => s.setMenu);
  const locks = useApp((s) => s.problem.locks);
  const canLock = useApp(selectCanSolve);
  const { solution: result } = useShownSolution();
  if (!menu) return null;
  const st = useApp.getState();
  const lock = locks.open.includes(menu.cellId) ? 'open' : locks.closed.includes(menu.cellId) ? 'closed' : null;
  const h = menu.hospital !== null ? result?.hospitals[menu.hospital] : undefined;
  const nLocks = locks.open.length + locks.closed.length;
  const close = () => setMenu(null);
  const lockTo = (l: 'open' | 'closed' | null) => {
    st.setLock(menu.cellId, l);
    close();
  };
  return (
    <Menu opened onChange={(o) => !o && close()} position="bottom-start" offset={2} shadow="md" withinPortal={false} closeOnEscape>
      <Menu.Target>
        <div style={{ position: 'absolute', left: menu.x, top: menu.y, width: 1, height: 1 }} aria-hidden />
      </Menu.Target>
      <Menu.Dropdown data-export="skip" data-testid="map-context-menu" style={{ zIndex: 7 }}>
        <Menu.Label>
          {h && menu.hospital !== null ? hospitalTitle(h, menu.hospital) : `Hücre ${menu.cellId}`}
        </Menu.Label>
        {h && menu.hospital !== null && (
          <Menu.Item onClick={() => (st.select({ kind: 'hospital', index: menu.hospital!, cellId: h.cell_id }), close())}>Hastane kartını aç</Menu.Item>
        )}
        <Menu.Item onClick={() => (st.select({ kind: 'cell', gridIndex: menu.gridIndex, cellId: menu.cellId }), close())}>Hücre kartını aç</Menu.Item>
        <Menu.Divider />
        {!canLock && (
          <Text size="xs" c="dimmed" px="sm" py={4} maw="16rem">
            Çevrimdışı mod: kilitler canlı çözüm gerektirir.
          </Text>
        )}
        {lock !== 'open' && (
          <Menu.Item color="green" disabled={!canLock} onClick={() => lockTo('open')} leftSection={<b>+</b>} data-testid="menu-lock-open">
            Burada hastane zorunlu olsun
          </Menu.Item>
        )}
        {lock !== 'closed' && (
          <Menu.Item color="red" disabled={!canLock} onClick={() => lockTo('closed')} leftSection={<b>×</b>} data-testid="menu-lock-closed">
            {h ? 'Bu hastaneyi kapat (hücre yasak)' : 'Burada hastane yasak olsun'}
          </Menu.Item>
        )}
        {lock && (
          <Menu.Item disabled={!canLock} onClick={() => lockTo(null)} data-testid="menu-lock-clear">
            {lock === 'open' ? 'Zorunluluğu kaldır' : 'Yasağı kaldır'}
          </Menu.Item>
        )}
        {nLocks > 0 && (
          <Menu.Item disabled={!canLock} onClick={() => (st.clearLocks(), close())}>
            Tüm kilitleri temizle ({nLocks})
          </Menu.Item>
        )}
      </Menu.Dropdown>
    </Menu>
  );
}
