// Legacy panel fixture only; not registered or exposed by the public manifest.
(() => {
  'use strict';
  if (globalThis.__veyloContentLoaded || typeof SGUi === 'undefined') return;
  globalThis.__veyloContentLoaded = true;
  const root = document.createElement('div');
  root.id = 'sg-root';
  root.hidden = true;
  // Inline important protects the host element itself. Page selectors cannot enter the shadow tree.
  const hostStyles = {
    all: 'initial', display: 'block', position: 'fixed', top: 'auto', left: 'auto',
    right: 'max(12px, env(safe-area-inset-right))', bottom: 'max(20px, env(safe-area-inset-bottom))',
    'z-index': '2147483647', width: 'min(356px, calc(100vw - 24px))', height: 'auto',
    margin: '0', padding: '0', border: '0', background: 'transparent',
    'pointer-events': 'none', visibility: 'visible', opacity: '1', transform: 'none',
    filter: 'none', 'text-align': 'left', direction: 'ltr', isolation: 'isolate', 'color-scheme': 'light dark'
  };
  for (const [name, value] of Object.entries(hostStyles)) root.style.setProperty(name, value, 'important');
  const shadow = root.attachShadow({ mode: 'open' });
  const stylesheets = ['ui.css', 'content.css'].map(file => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = chrome.runtime.getURL(file);
    return link;
  });
  let reveal;
  const styled = Promise.all(stylesheets.map(link => new Promise(resolve => {
    link.addEventListener('load', resolve, { once: true });
    link.addEventListener('error', resolve, { once: true });
  })));
  shadow.append(...stylesheets);
  const shell = document.createElement('div');
  shell.id = 'sg-shell';
  const panel = document.createElement('div');
  panel.id = 'sg-panel';
  panel.hidden = true;
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Veylo 视频助手');
  panel.setAttribute('aria-modal', 'false');
  panel.style.setProperty('display', 'none', 'important');
  const mountPoint = document.createElement('div');
  mountPoint.id = 'veylo-root';
  panel.appendChild(mountPoint);
  const fab = document.createElement('button');
  fab.id = 'sg-fab';
  fab.type = 'button';
  fab.title = 'Veylo — 好内容，随心留存';
  fab.setAttribute('aria-label', '打开 Veylo 视频下载面板');
  fab.setAttribute('aria-expanded', 'false');
  fab.setAttribute('aria-controls', 'sg-panel');
  fab.innerHTML = SGUi.brandSvg;
  shell.append(panel, fab);
  shadow.appendChild(shell);
  document.documentElement.appendChild(root);

  let ui = null;
  let opened = false;
  let lastUrl = '';
  let lastTitle = '';
  let destroyed = false;
  let stylesReady = false;
  let returnFocus = null;

  function setHostVisible(visible) {
    root.hidden = !visible;
    root.style.setProperty('display', visible ? 'block' : 'none', 'important');
  }

  function context() {
    return { url: SG.cleanUrl(location.href), title: document.title || '' };
  }

  function ensureUI() {
    if (!ui) ui = SGUi.mount(mountPoint, { ...context(), variant: 'floating', active: true, onClose: () => setOpen(false) });
    return ui;
  }

  function setOpen(value, source = null) {
    if (destroyed) return;
    opened = value === true;
    if (opened) {
      returnFocus = document.activeElement !== root ? document.activeElement : returnFocus;
      const controller = ensureUI();
      controller.setContext(source || context());
      controller.setActive(true);
      panel.hidden = false;
      panel.style.setProperty('display', 'block', 'important');
      fab.setAttribute('aria-expanded', 'true');
      fab.setAttribute('aria-label', '收起 Veylo 视频下载面板');
      setHostVisible(stylesReady);
      globalThis.queueMicrotask(() => shadow.querySelector('#vy-url')?.focus({ preventScroll: true }));
    } else {
      ui?.setActive(false);
      panel.hidden = true;
      panel.style.setProperty('display', 'none', 'important');
      fab.setAttribute('aria-expanded', 'false');
      fab.setAttribute('aria-label', '打开 Veylo 视频下载面板');
      setHostVisible(stylesReady && !!SG.classify(location.href));
      if (!root.hidden) fab.focus({ preventScroll: true });
      else if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    }
  }

  function navigate() {
    if (destroyed) return;
    if (!root.isConnected && document.documentElement) document.documentElement.appendChild(root);
    const changed = lastUrl !== location.href;
    const titleChanged = lastTitle !== document.title;
    if (!changed && !titleChanged) return;
    lastUrl = location.href;
    lastTitle = document.title;
    if (changed && opened) ui?.setContext(context());
    else if (titleChanged && opened) ui?.setContext(context(), { ifUnedited: true });
    setHostVisible(stylesReady && (opened || !!SG.classify(location.href)));
  }

  function handleMessage(message, _sender, sendResponse) {
    if (!['sg-toggle-panel', 'sg-open-panel'].includes(message?.type)) return;
    if (message.type === 'sg-toggle-panel') setOpen(!opened, message.url ? { url: String(message.url).slice(0, 4096), title: message.title || '' } : null);
    else setOpen(true, message.url ? { url: String(message.url).slice(0, 4096), title: message.title || '' } : null);
    sendResponse?.({ ok: true, open: opened });
  }

  function onEscape(event) {
    if (event.key !== 'Escape' || !opened) return;
    event.preventDefault();
    event.stopPropagation();
    setOpen(false);
  }

  function onPageHide(event) {
    ui?.setActive(false);
    if (!event.persisted) destroy();
  }

  function onPageShow(event) {
    if (event.persisted) { navigate(); ui?.setActive(opened); }
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    globalThis.clearInterval(navigationTimer);
    globalThis.clearTimeout(styleTimeout);
    ui?.destroy();
    window.removeEventListener('yt-navigate-finish', navigate);
    window.removeEventListener('popstate', navigate);
    window.removeEventListener('hashchange', navigate);
    window.removeEventListener('pagehide', onPageHide);
    window.removeEventListener('pageshow', onPageShow);
    chrome.runtime.onMessage?.removeListener(handleMessage);
    root.remove();
    globalThis.__veyloContentLoaded = false;
  }

  fab.addEventListener('click', () => setOpen(!opened));
  shadow.addEventListener('keydown', onEscape);
  window.addEventListener('yt-navigate-finish', navigate);
  window.addEventListener('popstate', navigate);
  window.addEventListener('hashchange', navigate);
  window.addEventListener('pagehide', onPageHide);
  window.addEventListener('pageshow', onPageShow);
  chrome.runtime.onMessage?.addListener(handleMessage);
  const navigationTimer = globalThis.setInterval(navigate, 1000);
  // Never flash an unstyled panel. The timeout also lets users recover after an extension update.
  const styleTimeout = globalThis.setTimeout// Legacy panel fixture only; not registered or exposed by the public manifest.
(() => { stylesReady = true; navigate(); setHostVisible(opened || !!SG.classify(location.href)); }, 1500);
  reveal = () => {
    if (destroyed) return;
    stylesReady = true;
    globalThis.clearTimeout(styleTimeout);
    setHostVisible(opened || !!SG.classify(location.href));
  };
  void styled.then(reveal);
  navigate();
})();
