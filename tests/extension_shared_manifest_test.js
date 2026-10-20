'use strict';

// Offline only: these tests load local extension sources, never browser profiles or media.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const extensionDir = path.resolve(__dirname, '..', 'extension');
const read = name => fs.readFileSync(path.join(extensionDir, name), 'utf8');
const context = vm.createContext({ URL, URLSearchParams });
vm.runInContext(read('shared.js'), context, { filename: 'shared.js' });
const SG = context.SG;
const plain = value => value == null ? value : JSON.parse(JSON.stringify(value));
let cases = 0;
function check(name, body) { body(); cases++; console.log(`PASS ${name}`); }
const id = 'abcdefghijk';

check('all supported YouTube hosts and paths canonicalize without tracking', () => {
  const hosts = ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtube-nocookie.com', 'www.youtube-nocookie.com'];
  for (const host of hosts) {
    for (const pathname of [`/watch?v=${id}`, `/shorts/${id}`, `/live/${id}/`, `/embed/${id}`, `/v/${id}`]) {
      assert.deepEqual(plain(SG.classify(`https://${host}${pathname}${pathname.includes('?') ? '&' : '?'}utm_source=mock#fragment`)), {
        platform: 'youtube', kind: 'video', url: `https://www.youtube.com/watch?v=${id}`
      });
    }
  }
  for (const host of ['youtu.be', 'www.youtu.be']) {
    assert.equal(SG.cleanUrl(` https://${host}/${id}/?t=30#fragment `), `https://www.youtube.com/watch?v=${id}`);
  }
  assert.equal(SG.cleanUrl(`http://youtu.be/${id}`), `https://www.youtube.com/watch?v=${id}`);
});

check('valid playlist/index survive while invalid optional fields are dropped', () => {
  assert.equal(SG.cleanUrl(`https://www.youtube.com/watch?v=${id}&list=PL_mock-123&index=2&t=30`), `https://www.youtube.com/watch?v=${id}&list=PL_mock-123&index=2`);
  for (const suffix of ['&index=2', '&list=bad%20list&index=2', '&list=PL_mock&index=-1', '&list=PL_mock&index=1234567']) {
    const expected = suffix.includes('PL_mock') ? `https://www.youtube.com/watch?v=${id}&list=PL_mock` : `https://www.youtube.com/watch?v=${id}`;
    assert.equal(SG.cleanUrl(`https://youtube.com/watch?v=${id}${suffix}`), expected);
  }
  assert.deepEqual(plain(SG.classify('https://youtube.com/playlist?list=PL_mock-123&tracking=1')), {
    platform: 'youtube', kind: 'playlist', url: 'https://www.youtube.com/playlist?list=PL_mock-123'
  });
});

check('channel forms canonicalize', () => {
  for (const pathname of ['/@creator', '/@creator/videos', '/channel/mock/streams', '/c/mock/shorts', '/user/mock/']) {
    assert.deepEqual(plain(SG.classify(`https://m.youtube.com${pathname}?tracking=1`)), {
      platform: 'youtube', kind: 'channel', url: `https://www.youtube.com${pathname}`
    });
  }
});

check('all supported X hosts normalize only single posts', () => {
  for (const host of ['x.com', 'www.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com']) {
    for (const tail of ['', '/', '/video/1', '/photo/2/']) {
      assert.deepEqual(plain(SG.classify(`https://${host}/mock_user/status/123456${tail}?s=20#fragment`)), {
        platform: 'x', kind: 'video', url: 'https://x.com/mock_user/status/123456'
      });
    }
    assert.equal(SG.cleanUrl(`https://${host}/i/web/status/123456/`), 'https://x.com/i/web/status/123456');
  }
  assert.equal(SG.cleanUrl('http://twitter.com/mock/status/123'), 'https://x.com/mock/status/123');
});

check('malformed, untrusted and unsupported URLs are rejected', () => {
  for (const raw of [null, {}, 1, '', 'not a URL', 'x'.repeat(4097),
    'javascript:alert(1)', `ftp://youtube.com/watch?v=${id}`,
    `https://youtube.com.evil.example/watch?v=${id}`, `https://evil-youtu.be/${id}`,
    `https://user:mock@youtube.com/watch?v=${id}`, `https://youtube.com:8443/watch?v=${id}`,
    'https://youtube.com/watch?v=short', `https://youtube.com/watch?v=${id}extra`,
    `https://youtube.com/watch?v=${id}%0A`, 'https://youtube.com/playlist?list=bad%20list',
    `https://youtube.com/playlist?list=${'x'.repeat(151)}`, 'https://youtube.com/feed/subscriptions',
    'https://x.com/home', 'https://x.com/mock', 'https://x.com/search?q=video',
    'https://x.com/mock/status/invalid', `https://x.com/mock/status/${'1'.repeat(26)}`,
    'https://x.com/mock/status/123/more', 'https://x.com.evil.example/mock/status/123'
  ]) {
    assert.equal(SG.classify(raw), null);
    assert.equal(SG.cleanUrl(raw), '');
  }
});

check('job messages distinguish progress, completion, failure and cancellation', () => {
  assert.equal(SG.jobText(null), '');
  assert.equal(SG.jobText({ state: 'queued', downloadDir: 'D:\\MockDownloads' }), '等待下载 · 保存到：D:\\MockDownloads');
  assert.equal(SG.jobText({ state: 'downloading', percent: 49.6, downloadDir: 'D:\\MockDownloads' }), '正在下载 50% · 保存到：D:\\MockDownloads');
  assert.equal(SG.jobText({ state: 'downloading', percent: null }), '正在下载 · 保存到：');
  assert.equal(SG.jobText({ state: 'completed', downloadDir: 'D:\\MockDownloads' }), '下载完成 ✓ 保存到：D:\\MockDownloads');
  assert.equal(SG.jobText({ state: 'failed', error: 'mock failure' }), '下载失败：mock failure');
  const canceled = { state: 'canceled', percent: 49.6, downloadDir: 'D:\\MockDownloads', error: '已取消下载。' };
  assert.equal(SG.jobText(canceled), '已取消下载');
  assert.deepEqual(plain(SG.jobView(canceled)), { text: '已取消下载', tone: 'idle', percent: null });
});

const manifest = JSON.parse(read('manifest.json'));
check('MV3 permissions and minimum version cover used APIs without broad permissions', () => {
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.version, '2.4.2');
  assert.ok(Number(manifest.minimum_chrome_version) >= 116);
  assert.deepEqual([...manifest.permissions].sort(), ['storage', 'activeTab', 'clipboardWrite', 'offscreen', 'nativeMessaging', 'contextMenus', 'alarms'].sort());
  assert.deepEqual(manifest.optional_permissions, ['notifications']);
  assert.equal(manifest.background.service_worker, 'background.js');
  assert.equal(manifest.action.default_popup, 'popup.html');
});

check('manifest resources and HTML/script/style dependencies exist locally', () => {
  const resources = new Set([manifest.background.service_worker, manifest.action.default_popup,
    ...Object.values(manifest.action.default_icon), ...Object.values(manifest.icons),
    ...manifest.content_scripts.flatMap(script => [...script.js, ...(script.css || [])]),
    ...manifest.web_accessible_resources.flatMap(entry => entry.resources), 'offscreen.html']);
  for (const html of ['popup.html', 'offscreen.html']) {
    for (const match of read(html).matchAll(/\b(?:src|href)=["']([^"']+)["']/g)) resources.add(match[1]);
    assert.doesNotMatch(read(html), /<script\b(?![^>]*\bsrc=)[^>]*>\s*\S/i, 'MV3 HTML must not contain inline scripts');
  }
  for (const script of ['background.js']) {
    for (const match of read(script).matchAll(/importScripts\(["']([^"']+)["']\)/g)) resources.add(match[1]);
  }
  for (const stylesheet of [...resources].filter(resource => resource.endsWith('.css'))) {
    for (const match of read(stylesheet).matchAll(/url\(["']?([^)'"\s]+)/g)) resources.add(match[1]);
  }
  for (const resource of resources) {
    assert.ok(!/^[a-z]+:/i.test(resource), 'dependency must be local');
    assert.ok(!resource.split(/[\\/]/).includes('..'), 'dependency must remain inside extension');
    assert.ok(fs.statSync(path.join(extensionDir, resource)).isFile(), `missing resource ${resource}`);
  }
  for (const script of manifest.content_scripts) {
    assert.deepEqual(script.js, ['shared.js', 'icons.js', 'ui.js', 'content.js']);
    assert.equal(script.run_at, 'document_idle');
  }
  assert.deepEqual([...read('popup.html').matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["']/g)].map(match => match[1]),
    ['shared.js', 'icons.js', 'ui.js', 'popup.js']);
  assert.ok(read('popup.html').indexOf('src="shared.js"') < read('popup.html').indexOf('src="popup.js"'));
});

check('privileged UI stays in extension popup with no page injection or exposed resources', () => {
  assert.deepEqual(manifest.content_scripts, []);
  assert.deepEqual(manifest.host_permissions, []);
  assert.deepEqual(manifest.web_accessible_resources, []);
  assert.equal(manifest.externally_connectable, undefined);
  assert.doesNotMatch(read('background.js'), /executeScript|registerContentScripts|onMessageExternal/);
});

console.log(`extension_shared_manifest_test: ${cases} groups passed (URL matrix, job text, MV3 permissions, all local dependencies)`);
