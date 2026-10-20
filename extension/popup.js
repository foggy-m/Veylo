(() => {
  'use strict';
  const root = document.getElementById('veylo-root');
  if (!root || typeof SGUi === 'undefined') return;
  const params = new URLSearchParams(location.search);
  const hasExplicitUrl = params.has('url');
  const suppliedUrl = hasExplicitUrl ? (params.get('url') || '').slice(0, 4096) : '';
  const ui = SGUi.mount(root, { variant: 'popup', url: suppliedUrl });
  // An explicit context-menu link wins, even if it is invalid; never auto-submit it.
  if (!hasExplicitUrl) {
    try {
      chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
        if (chrome.runtime.lastError) return;
        const tab = tabs?.[0];
        if (tab?.url && SG.classify(tab.url)) ui.setContext({ url: SG.cleanUrl(tab.url), title: tab.title || '' }, { ifUnedited: true });
      });
    } catch (_) { /* A pasted link remains usable if activeTab is unavailable. */ }
  }
  window.addEventListener('pagehide', event => {
    if (event.persisted) ui.setActive(false);
    else ui.destroy();
  });
  window.addEventListener('pageshow', event => { if (event.persisted) ui.setActive(true); });
})();
