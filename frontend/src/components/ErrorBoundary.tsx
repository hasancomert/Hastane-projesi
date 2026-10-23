/**
 * Hata sınırı: bir bölümün (harita, panel, grafik) hatası tüm sayfayı beyaz ekrana düşürmesin (şartname 5).
 * Bölüm yerine kısa bir Türkçe ileti ve "yeniden dene" düğmesi gösterilir.
 */
import { Alert, Button, Stack, Text } from '@mantine/core';
import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  /** bölümün adı (iletide görünür): "Harita", "Parametre paneli" … */
  area: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.warn(`${this.props.area} bölümünde hata:`, error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Alert color="red" variant="light" title={`${this.props.area} gösterilemedi`} m="sm">
        <Stack gap="xs">
          <Text size="sm">Bu bölümde beklenmeyen bir hata oluştu; sayfanın geri kalanı çalışmaya devam ediyor.</Text>
          <Text size="xs" c="dimmed" ff="monospace">
            {this.state.error.message}
          </Text>
          <Button size="xs" variant="light" onClick={() => this.setState({ error: null })} w="fit-content">
            Yeniden dene
          </Button>
        </Stack>
      </Alert>
    );
  }
}
