/* Run with node tests/ui.test.cjs. No packages, browser, native bridge or network required. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createDOM, TestEvent } = require('./dom-adapter.cjs');
const base = path.resolve(__dirname, '..');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async predicate => {
  for (let count = 0; count < 100; count += 1) { if (predicate()) return; await wait(5); }
  throw new Error('Timed out waiting for content lifecycle action');
};

function environment(url = 'http://127.0.0.1/tests/ui-demo-frame.html') {
  const { document, window } = createDOM();
  const intervals = new Map();
  const timeouts = new Set();
  const context = vm.createContext({
    ...window, document,
    console, URL, URLSearchParams, Intl, Date, Map, Set, WeakMap, Promise,
    location: new URL(url),
    setInterval(callback, delay) { const id = setInterval(callback, delay); intervals.set(id, delay); return id; },
    clearInterval(id) { intervals.delete(id); clearInterval(id); },
    setTimeout(callback, delay) {
      const id = setTimeout(() => { timeouts.delete(id); callback(); }, delay);
      timeouts.add(id);
      return id;
    },
    clearTimeout(id) { timeouts.delete(id); clearTimeout(id); },
    queueMicrotask
  });
  context.window = context;
  context.globalThis = context;
  const load = file => vm.runInContext(fs.readFileSync(path.join(base, file), 'utf8'), context, { filename: file });
  for (const file of ['tests/chrome-fixture.js', 'shared.js', 'icons.js', 'ui.js']) load(file);
  return {
    context, document, intervals, load,
    close() {
      for (const id of intervals.keys()) clearInterval(id);
      for (const id of timeouts) clearTimeout(id);
      intervals.clear(); timeouts.clear();
    }
  };
}

async function run() {
  let passed = 0;
  let failed = 0;
  const env = environment();
  try {
    env.load('tests/ui-dom-tests.js');
    const result = await env.context.VeyloDOMTests.run(env.context, line => console.log(line));
    passed += result.passed; failed += result.failed;
  } finally { env.close(); }

  async function test(name, callback) {
    try { await callback(); passed += 1; console.log(`PASS ${name}`); }
    catch (error) { failed += 1; console.log(`FAIL ${name} — ${error.stack || error.message}`); }
  }

  await test('popup ?url 优先且不读active tab、不自动下载', async () => {
    const url = 'https://x.com/veylo_demo/status/1000000000000000001';
    const local = environment(`http://localhost/popup.html?url=${encodeURIComponent(url)}`);
    try {
      const fixture = local.context.VeyloFixture.create({ jobs: [] });
      let tabQueries = 0;
      fixture.chrome.tabs.query = () => { tabQueries += 1; };
      local.context.chrome = fixture.chrome;
      const root = local.document.createElement('main'); root.id = 'veylo-root'; local.document.body.appendChild(root);
      local.load('popup.js');
      await wait(10);
      assert.equal(root.querySelector('#vy-url').value, url);
      assert.equal(tabQueries, 0);
      assert.equal(fixture.count('sg-deliver'), 0);
      local.context.dispatchEvent(new TestEvent('pagehide', { persisted: false }));
      assert.equal(local.intervals.size, 0);
    } finally { local.close(); }
  });

  await test('content 真ShadowRoot/重复执行/收起展开/SPA/销毁', async () => {
    const local = environment('https://www.bilibili.com/video/BV1xx411c7mD?p=2');
    try {
      const fixture = local.context.VeyloFixture.create({ jobs: [] });
      local.context.chrome = fixture.chrome;
      local.load('content.js');
      const root = local.document.getElementById('sg-root');
      assert.ok(root?.shadowRoot, 'content did not create a shadow root');
      assert.equal(root.children.length, 0, 'UI leaked into the light DOM');
      assert.equal(root.style.getPropertyPriority('all'), 'important');
      assert.equal(root.shadowRoot.querySelectorAll('link').length, 2);
      root.shadowRoot.querySelectorAll('link').forEach(link => link.dispatchEvent(new TestEvent('load')));
      await wait(0);
      const fab = root.shadowRoot.querySelector('#sg-fab');
      const panel = root.shadowRoot.querySelector('#sg-panel');
      fab.click();
      await until(() => !!root.shadowRoot.querySelector('#vy-copy') && !root.shadowRoot.querySelector('#vy-send').disabled);
      assert.equal(panel.hidden, false);
      assert.equal(root.shadowRoot.querySelectorAll('.vy-app').length, 1);
      assert.equal(local.intervals.size, 2, 'expected one navigation timer and one history timer');
      root.shadowRoot.querySelector('#vy-copy').click();
      await until(() => fixture.store.copied.length > 0);
      assert.equal(fixture.store.copied, local.context.SG.cleanUrl(local.context.location.href));
      root.shadowRoot.querySelector('#vy-close').click();
      assert.equal(panel.hidden, true);
      assert.equal(local.intervals.size, 1, 'closed panel leaked history polling');
      fab.click();
      assert.equal(root.shadowRoot.querySelectorAll('.vy-app').length, 1);
      assert.equal(local.intervals.size, 2);
      local.load('content.js');
      assert.equal(local.document.querySelectorAll('#sg-root').length, 1, 'repeat content execution duplicated root');
      local.context.location = new URL(local.context.VeyloFixture.URLS.x);
      local.context.document.title = 'SPA context title';
      local.context.dispatchEvent(new TestEvent('popstate'));
      assert.equal(root.shadowRoot.querySelector('#vy-url').value, local.context.VeyloFixture.URLS.x);
      assert.equal(root.shadowRoot.querySelector('#vy-part-row').hidden, true);
      root.shadowRoot.querySelector('#vy-url').dispatchEvent(new TestEvent('keydown', { key: 'Escape', bubbles: true }));
      assert.equal(panel.hidden, true);
      fixture.emit({ type: 'sg-open-panel', url: local.context.VeyloFixture.URLS.youtube, title: 'Context-menu title' });
      assert.equal(panel.hidden, false);
      assert.equal(root.shadowRoot.querySelector('#vy-url').value, local.context.VeyloFixture.URLS.youtube);
      fixture.emit({ type: 'sg-toggle-panel' });
      assert.equal(panel.hidden, true);
      local.context.dispatchEvent(new TestEvent('pagehide', { persisted: false }));
      assert.equal(local.intervals.size, 0);
      assert.equal(local.document.getElementById('sg-root'), null);
    } finally { local.close(); }
  });

  for (const media of ['audio-mp3', 'audio-original']) {
    await test(`preview frame ${media} 查询与 update 保留独立码率，无原生成功伪造`, async () => {
      const local = environment(`http://localhost/tests/ui-demo-frame.html?platform=youtube&media=${media}&audioQuality=256&more=1`);
      try {
        const root = local.document.createElement('div'); root.id = 'frame-root'; local.document.body.appendChild(root);
        const mount = local.context.SGUi.mount;
        // Only layout measurement is stubbed; settings, events and the Chrome fixture remain real.
        local.context.SGUi = Object.freeze({ ...local.context.SGUi, mount(container, options) {
          const ui = mount(container, options);
          ui.element.getBoundingClientRect = () => ({ width: 408, height: 600 });
          return ui;
        } });
        local.context.getComputedStyle = () => ({ fontFamily: 'sans-serif', backgroundColor: 'rgb(0, 0, 0)' });
        local.context.parent = { postMessage() {} };
        local.context.innerWidth = 408; local.context.innerHeight = 600;
        local.load('tests/ui-demo-frame.js');
        const preview = local.context.VeyloDemoFrame;
        await preview.ui.ready;
        const select = preview.ui.element.querySelector('#vy-audio-quality');
        assert.equal(select.value, '256');
        assert.equal(preview.ui.element.querySelector('#vy-media').value, media);
        assert.equal(preview.ui.element.querySelector('#vy-audio-quality-row').hidden, media === 'audio-original');
        assert.equal(preview.ui.element.querySelector('#vy-more').open, true);
        if (media === 'audio-original') {
          assert.equal(preview.ui.element.querySelector('#vy-audio-original-note').hidden, false);
          await preview.update({ media: 'audio-m4a' });
          assert.equal(preview.ui.element.querySelector('#vy-media').value, 'audio-m4a');
          assert.equal(select.value, '256');
          await preview.update({ media: 'audio-original' });
          assert.equal(preview.ui.element.querySelector('#vy-media').value, 'audio-original');
          assert.equal(preview.fixture.store.settings.audioQuality, '256');
        }
        await preview.update({ audioQuality: '192' });
        assert.equal(select.value, '192');
        assert.equal(preview.fixture.last('sg-settings-set').settings.audioQuality, '192');
        await preview.update({ audioQuality: 'lossless' });
        assert.equal(select.value, 'auto');
        assert.equal(preview.fixture.count('sg-deliver'), 0);
        preview.ui.destroy();
      } finally { local.close(); }
    });
  }

  await test('生产UI不加载远端素材且样式不以body/button/*全局污染宿主', async () => {
    const ui = fs.readFileSync(path.join(base, 'ui.js'), 'utf8');
    const css = fs.readFileSync(path.join(base, 'ui.css'), 'utf8');
    const content = fs.readFileSync(path.join(base, 'content.js'), 'utf8');
    assert.equal(/\bfetch\s*\(|XMLHttpRequest|chrome\.cookies/.test(ui + content), false);
    assert.equal(/@import|https?:\/\//.test(css), false);
    assert.equal(/^\s*(?:body|button|\*)\s*[{,]/m.test(css), false);
    assert.ok(content.includes("attachShadow({ mode: 'open' })"));
    assert.ok(css.includes('prefers-reduced-motion'));
    assert.ok(css.includes(':focus-visible'));
  });

  console.log(`\n${passed} passed / ${failed} failed`);
  console.log('The test-only DOM adapter verifies interaction/state/contracts, not CSS layout. Real rendering preview: tests/ui-demo.html.');
  if (failed) process.exitCode = 1;
}
void run().catch(error => { console.error(error); process.exitCode = 1; });
