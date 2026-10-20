(() => {
  'use strict';
  const params = new URLSearchParams(location.search);
  const variant = params.get('variant') === 'content' ? 'content' : 'popup';
  const platform = params.get('platform') || 'bili';
  const theme = params.get('theme') || 'light';
  const fixture = VeyloFixture.create({
    platform: platform in VeyloFixture.TITLES ? platform : 'bili',
    // 'current' previews the 2.3.0 hosts; the default keeps the legacy (no features) host state.
    hosts: params.get('hosts') === 'current' ? 'current' : 'legacy',
    settings: {
      theme, dir: params.get('directory') === 'long' ? VeyloFixture.LONG_PATH : 'default',
      recentDirs: [VeyloFixture.LONG_PATH, 'D:\\Veylo Demo (synthetic)\\Downloads'],
      media: ['video', 'audio-original', 'audio-mp3', 'audio-m4a'].includes(params.get('media')) ? params.get('media') : 'video',
      audioQuality: ['auto', '320', '256', '192', '128'].includes(params.get('audioQuality')) ? params.get('audioQuality') : 'auto',
      subtitles: ['off', 'manual', 'auto'].includes(params.get('subtitles')) ? params.get('subtitles') : 'off',
      subLangs: ['zh.*,en', 'zh.*', 'en'].includes(params.get('subLangs')) ? params.get('subLangs') : 'zh.*,en',
      sponsorblock: ['off', 'sponsor', 'all'].includes(params.get('sponsorblock')) ? params.get('sponsorblock') : 'off',
      danmaku: ['off', 'ass', 'xml'].includes(params.get('danmaku')) ? params.get('danmaku') : 'off',
      biliSubtitles: ['off', 'srt'].includes(params.get('biliSubtitles')) ? params.get('biliSubtitles') : 'off',
      liveMinutes: ['60', '120', '240', '480', '720'].includes(params.get('liveMinutes')) ? params.get('liveMinutes') : '240'
    },
    permissionDecision: params.get('permission') !== 'deny',
    engineAvailable: { x: params.get('engine') !== 'missing', bili: true }
  });
  fixture.resetJobs(params.get('jobs') || 'mixed');
  globalThis.chrome = fixture.chrome;
  document.body.dataset.variant = variant;
  document.body.dataset.production = String(params.get('production') === '1');
  const url = VeyloFixture.URLS[platform] || 'https://example.invalid/not-supported';
  let ui;
  let shadow = null;
  let opened = true;
  let panel = null;
  let fab = null;
  const initial = { variant: variant === 'content' ? 'floating' : 'popup', url, title: VeyloFixture.TITLES[platform] || '', theme };
  if (variant === 'content') {
    const host = document.createElement('div');
    host.id = 'sg-root';
    // Match the production inline host reset; all other styling comes from the real shadow CSS.
    for (const [name, value] of Object.entries({ all: 'initial', display: 'block', position: 'fixed', right: '12px', bottom: '20px', width: 'min(356px, calc(100vw - 24px))', 'z-index': '2147483647', 'pointer-events': 'none', transform: 'none', visibility: 'visible' })) host.style.setProperty(name, value, 'important');
    shadow = host.attachShadow({ mode: 'open' });
    for (const file of ['ui.css', 'content.css']) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = `../${file}`;
      shadow.appendChild(link);
    }
    const shell = document.createElement('div');
    shell.id = 'sg-shell';
    panel = document.createElement('div');
    panel.id = 'sg-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Veylo synthetic in-page panel');
    const mount = document.createElement('div');
    panel.appendChild(mount);
    fab = document.createElement('button');
    fab.id = 'sg-fab';
    fab.type = 'button';
    fab.innerHTML = SGUi.brandSvg;
    fab.setAttribute('aria-label', '展开或收起合成 Veylo 面板');
    fab.setAttribute('aria-expanded', 'true');
    const toggle = value => {
      opened = value;
      panel.hidden = !opened;
      fab.setAttribute('aria-expanded', String(opened));
      ui.setActive(opened);
    };
    shell.append(panel, fab);
    shadow.appendChild(shell);
    document.body.appendChild(host);
    ui = SGUi.mount(mount, { ...initial, onClose: () => toggle(false) });
    fab.addEventListener('click', () => toggle(!opened));
    shadow.addEventListener('keydown', event => { if (event.key === 'Escape' && opened) { event.preventDefault(); toggle(false); fab.focus(); } });
  } else ui = SGUi.mount(document.getElementById('frame-root'), initial);
  if (params.get('tab') === 'history') ui.showTab('history');
  if (params.get('more') === '1') ui.element.querySelector('#vy-more').open = true;

  async function update(config = {}) {
    if (config.platform) ui.setContext({ url: VeyloFixture.URLS[config.platform] || 'https://example.invalid/not-supported', title: VeyloFixture.TITLES[config.platform] || '' });
    if (config.theme) ui.setTheme(config.theme);
    if (config.jobs) { fixture.resetJobs(config.jobs); await ui.refreshHistory(false); }
    if (config.permission) fixture.store.permissionDecision = config.permission !== 'deny';
    if (config.engine) fixture.store.engineAvailable.x = config.engine !== 'missing';
    if (config.hosts) {
      // Capability gating is cached per mount, so switching host generation reloads this preview frame.
      const next = new URLSearchParams({ ...Object.fromEntries(params), hosts: config.hosts });
      if (next.get('hosts') !== (params.get('hosts') || 'legacy')) { location.search = next.toString(); return measure(); }
    }
    if (config.directory) {
      fixture.store.settings.dir = config.directory === 'long' ? VeyloFixture.LONG_PATH : 'default';
      fixture.store.settings.recentDirs = [VeyloFixture.LONG_PATH];
      ui.setActive(false);
      ui.setActive(true);
    }
    await ui.ready;
    if (config.media !== undefined) {
      const select = ui.element.querySelector('#vy-media');
      select.value = ['video', 'audio-original', 'audio-mp3', 'audio-m4a'].includes(config.media) ? config.media : 'video';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (config.audioQuality !== undefined) {
      const select = ui.element.querySelector('#vy-audio-quality');
      select.value = ['auto', '320', '256', '192', '128'].includes(config.audioQuality) ? config.audioQuality : 'auto';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
    return measure();
  }

  function measure() {
    const app = ui.element;
    const rectangle = app.getBoundingClientRect();
    const workspace = app.querySelector('#vy-workspace');
    const send = app.querySelector('#vy-send');
    return {
      variant, width: Math.round(rectangle.width), height: Math.round(rectangle.height),
      documentScroll: document.documentElement.scrollWidth > innerWidth || document.documentElement.scrollHeight > innerHeight,
      workspaceScroll: workspace.scrollHeight > workspace.clientHeight,
      buttonFont: getComputedStyle(send).fontFamily,
      buttonBackground: getComputedStyle(send).backgroundColor,
      shadow: !!shadow
    };
  }

  globalThis.VeyloDemoFrame = { fixture, ui, shadow, update, measure };
  void ui.ready.then(() => parent.postMessage({ type: 'veylo-demo-ready', variant, metrics: measure() }, location.origin));
  window.addEventListener('pagehide', () => ui.destroy());
})();
