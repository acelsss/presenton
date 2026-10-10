'use client';

import { useEffect } from 'react';
import { initMixpanel, MixpanelEvent, trackEvent } from '@/utils/mixpanel';
import { usePathname } from 'next/navigation';
import { hostEditorConfig } from '@/utils/host-editor';

export function MixpanelInitializer({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  // Initialize once
  useEffect(() => {
    if (!hostEditorConfig()) initMixpanel();
  }, []);

  useEffect(() => {
    if (!hostEditorConfig()) trackEvent(MixpanelEvent.PageView, { url: pathname });
  }, [pathname]);

  return <>{children}</>;
}

export default MixpanelInitializer;


