/* Reusable deterministic DOM tests. Runs against a browser DOM or the local minimal DOM adapter. */
(function (global) {
  'use strict';
  async function run(w, report = () => {}) {
    const originalChrome = w.chrome;
    const originalSetInterval = w.setInterval;
    const originalClearInterval = w.clearInterval;
    const preview = w.VeyloDemoFrame?.ui;
    preview?.setActive(false);
    const timers = new Map();
    w.setInterval = (callback, delay) => {
      const id = originalSetInterval.call(w, callback, delay);
      timers.set(id, { callback, delay });
      return id;
    };
    w.clearInterval = id => { timers.delete(id); originalClearInterval.call(w, id); };
    const wait = ms => new Promise(resolve => w.setTimeout(resolve, ms));
    const until = async predicate => {
      for (let count = 0; count < 100; count += 1) { if (predicate()) return; await wait(5); }
      throw new Error('Timed out waiting for a synthetic UI action');
    };
    const assert = (condition, message) => { if (!condition) throw new Error(message); };
    let passed = 0;
    let failed = 0;
    let current = null;
    const cleanup = () => {
      current?.ui.destroy();
      current?.root.remove();
      current = null;
    };
    async function setup(config = {}, mount = {}) {
      cleanup();
      const fixture = w.VeyloFixture.create(config);
      w.chrome = fixture.chrome;
      const root = w.document.createElement('div');
      root.style.position = 'absolute';
      root.style.left = '-10000px';
      root.style.width = '408px';
      w.document.body.appendChild(root);
      const ui = w.SGUi.mount(root, { url: w.VeyloFixture.URLS.bili, title: w.VeyloFixture.TITLES.bili, ...mount });
      const $ = name => ui.element.querySelector(`#vy-${name}`);
      current = { fixture, root, ui, $ };
      await ui.ready;
      return current;
    }
    async function test(name, callback) {
      try { await callback(); passed += 1; report(`PASS ${name}`); }
      catch (error) { failed += 1; report(`FAIL ${name} — ${error.message}`); }
      finally { cleanup(); }
    }
    function change(element, value) {
      if (typeof value === 'boolean') element.checked = value;
      else element.value = value;
      element.dispatchEvent(new w.Event('change', { bubbles: true }));
    }
    function input(element, value) {
      element.value = value;
      element.dispatchEvent(new w.Event('input', { bubbles: true }));
    }

    try {
      await test('初始 B 站选项完整；挂载不提交、不申请通知', async () => {
        const { fixture, $, ui } = await setup();
        assert(ui.element.querySelector('h1').textContent === 'Veylo', 'Brand is missing');
        assert(!$('part-row').hidden && !$('quality-row').hidden, 'Bilibili quality/parts missing');
        assert($('quality').options.length === 7, 'Original Bilibili quality choices not preserved');
        assert($('more').open === false, 'Advanced options must be collapsed by default');
        assert(fixture.count('sg-deliver') === 0, 'Mount auto-submitted a task');
        assert(fixture.count('permissions.request') === 0, 'Mount requested notification permission');
      });

      await test('音频音质仅在直接 MP3/M4A 显示；视频与合集不承诺音质', async () => {
        const { fixture, $, ui } = await setup();
        const select = $('audio-quality');
        assert(select && $('audio-quality-row') && $('audio-quality-note'), 'Audio quality control is missing');
        assert($('more').contains(select), 'Audio quality is outside more options');
        assert(ui.element.querySelector('label[for="vy-audio-quality"]').textContent === '音频音质', 'Audio quality label missing');
        assert(select.getAttribute('aria-describedby') === 'vy-audio-quality-note', 'Audio quality helper is not associated');
        assert([...select.options].map(option => `${option.value}:${option.textContent}`).join('|') === 'auto:内核默认|320:320 kbps|256:256 kbps|192:192 kbps|128:128 kbps', 'Audio quality choices incorrect');
        assert(select.value === 'auto' && fixture.store.settings.audioQuality === 'auto', 'Audio quality must default to auto');
        assert($('audio-quality-note').textContent.includes('目标编码码率') && $('audio-quality-note').textContent.includes('不能提升源音质'), 'Target bitrate/source limitation missing');
        for (const platform of ['youtube', 'x', 'bili']) {
          ui.setContext({ url: w.VeyloFixture.URLS[platform] });
          for (const media of ['video', 'audio-mp3', 'audio-m4a']) {
            change($('media'), media);
            assert($('audio-quality-row').hidden === (media === 'video') && $('audio-quality-note').hidden === (media === 'video'), `${platform}/${media} visibility incorrect`);
            assert(select.disabled === (media === 'video'), `${platform}/${media} availability incorrect`);
          }
        }
        for (const kind of ['playlist', 'channel']) {
          ui.setContext({ url: w.VeyloFixture.URLS[kind] });
          assert($('audio-quality-row').hidden && $('audio-quality-note').hidden && select.disabled, `${kind} exposed direct audio quality`);
        }
        ui.setContext({ url: 'https://example.invalid/not-supported' });
        assert($('audio-quality-row').hidden && select.disabled, 'Unsupported URL exposed audio quality');
        assert(fixture.count('sg-deliver') === 0, 'Format changes created a task');
      });

      await test('原音轨是首个推荐音频选项，不转码说明与转换风险清晰且保留旧偏好', async () => {
        const { fixture, $, ui } = await setup({ settings: { audioQuality: '256' } });
        assert([...$('media').options].map(option => `${option.value}:${option.textContent}`).join('|') === 'video:视频 + 音频|audio-original:仅音频 · 原音轨（推荐）|audio-mp3:仅音频 · 转换为 MP3|audio-m4a:仅音频 · 转换为 M4A', 'Original audio recommendation/order missing');
        assert($('media').value === 'video' && fixture.count('sg-settings-set') === 0, 'Adding the recommendation migrated the saved format');
        const invalid = await w.SGUi.request({ type: 'sg-settings-set', settings: { media: 'not-a-format' } });
        assert(invalid.settings.media === 'video', 'Fixture media whitelist is not normalized');
        for (const media of ['audio-mp3', 'audio-m4a', 'video']) {
          change($('media'), media);
          await wait(5);
          assert($('media').value === media && $('audio-quality').value === '256', 'An existing explicit format or bitrate was replaced');
        }
        for (const platform of ['youtube', 'x', 'bili']) {
          ui.setContext({ url: w.VeyloFixture.URLS[platform] });
          change($('media'), 'audio-original');
          assert($('media').value === 'audio-original', 'Original audio preference was rejected');
          assert($('quality-row').hidden && $('subtitles-row').hidden && $('audio-quality-row').hidden && $('audio-quality-note').hidden && $('audio-quality').disabled, 'Original audio exposed conversion/video settings');
          assert($('more-value').textContent.startsWith('原音轨'), 'Original audio summary is mislabeled');
          const note = $('audio-original-note');
          assert(note && !note.hidden && $('media').getAttribute('aria-describedby') === 'vy-audio-original-note', 'Original audio helper is missing or unassociated');
          for (const text of ['最佳可用', '保留源编码', '不重新编码', '不强制码率', '输出格式随源', '不保证源本身无损', '不保证文件大小']) assert(note.textContent.includes(text), `Original audio helper lacks ${text}`);
          assert($('audio-quality').value === '256' && fixture.store.settings.audioQuality === '256', 'Original audio changed the saved conversion bitrate');
        }
        for (const kind of ['playlist', 'channel']) {
          ui.setContext({ url: w.VeyloFixture.URLS[kind] });
          assert($('media-row').hidden && $('media').disabled && $('audio-original-note').hidden, 'Collection exposed an original audio promise');
        }
        ui.setContext({ url: w.VeyloFixture.URLS.youtube });
        change($('media'), 'audio-mp3');
        assert(!$('audio-quality-row').hidden && $('audio-quality').value === '256' && $('audio-original-note').hidden, 'Conversion bitrate was not restored');
        for (const text of ['可能增大文件', '不能提升源音质', '不确定时选原音轨', '不保证保留源编码']) assert($('audio-quality-note').textContent.includes(text), `Conversion helper lacks ${text}`);
        assert(fixture.count('sg-deliver') === 0, 'Changing format probed or delivered a task');
      });

      await test('音质同 tick 立即保存，关闭重挂载保留且不改视频画质', async () => {
        const { fixture, $, ui, root } = await setup({ settings: { media: 'audio-mp3', quality: '720', qualityBili: '64' }, delays: { 'sg-settings-set': 35 } });
        const before = fixture.count('sg-settings-set');
        change($('audio-quality'), '320');
        change($('audio-quality'), '256');
        assert(fixture.count('sg-settings-set') === before + 2, 'Audio quality patches were not dispatched immediately');
        assert(JSON.stringify(fixture.last('sg-settings-set').settings) === '{"audioQuality":"256"}', 'Audio quality patch changed unrelated settings');
        ui.destroy();
        const fresh = w.SGUi.mount(root, { url: w.VeyloFixture.URLS.bili });
        current.ui = fresh;
        await fresh.ready;
        assert(fresh.element.querySelector('#vy-audio-quality').value === '256', 'Remount lost the audio quality preference');
        await wait(40);
        assert(fixture.store.settings.audioQuality === '256' && fixture.store.settings.quality === '720' && fixture.store.settings.qualityBili === '64', 'Audio preference changed video quality or was lost on closure');
      });

      await test('音质仅接受约定字符串；旧设置和非法值回退自动', async () => {
        for (const value of [undefined, 'best', 'lossless', '320k', ' 256', 256, null, '']) {
          const { fixture, $ } = await setup({ settings: { media: 'audio-mp3', audioQuality: value } });
          assert($('audio-quality').value === 'auto', `Invalid or legacy audio quality ${String(value)} did not default to auto`);
          assert(fixture.count('sg-settings-set') === 0, 'Reading an invalid preference rewrote storage');
        }
      });

      await test('音质初始读取与乱序保存响应不回滚最新选择', async () => {
        const pending = setup({
          settings: { media: 'audio-mp3', audioQuality: '128' },
          delays: { 'sg-settings-get': 45, 'sg-settings-set': payload => payload.settings.audioQuality === '320' ? 70 : 5 }
        });
        await wait(0);
        assert(current.$('audio-quality').disabled, 'Audio quality enabled before settings were ready');
        change(current.$('audio-quality'), '320');
        change(current.$('audio-quality'), '256');
        await pending;
        assert(current.$('audio-quality').value === '256', 'Initial settings read rolled back the audio preference');
        await wait(40);
        assert(current.$('audio-quality').value === '256', 'Older save response rolled back the audio preference');
      });

      await test('原音轨立即保存并抵抗迟到读取/保存响应，重挂载保留转换码率', async () => {
        const pending = setup({ settings: { media: 'audio-mp3', audioQuality: '256' }, delays: { 'sg-settings-get': 45, 'sg-settings-set': payload => payload.settings.media === 'audio-m4a' ? 70 : 5 } });
        await wait(0);
        change(current.$('media'), 'audio-m4a');
        change(current.$('media'), 'audio-original');
        assert(current.fixture.count('sg-settings-set') === 2 && JSON.stringify(current.fixture.last('sg-settings-set').settings) === '{"media":"audio-original"}', 'Original audio was not saved immediately or changed bitrate');
        const { fixture, $, ui, root } = await pending;
        await wait(40);
        assert($('media').value === 'audio-original' && $('audio-quality').value === '256', 'A stale settings response reverted original mode or bitrate');
        ui.destroy();
        const fresh = w.SGUi.mount(root, { url: w.VeyloFixture.URLS.bili });
        current.ui = fresh;
        await fresh.ready;
        assert(fresh.element.querySelector('#vy-media').value === 'audio-original' && fixture.store.settings.audioQuality === '256', 'Remount lost original mode or converted preference');
      });

      await test('共享 native select 保留全部近期路径，tooltip 是完整路径', async () => {
        const { $ } = await setup({ settings: { dir: w.VeyloFixture.LONG_PATH, recentDirs: [w.VeyloFixture.LONG_PATH] } });
        assert($('dir').value === w.VeyloFixture.LONG_PATH, 'Long selected directory lost');
        assert($('dir').title === w.VeyloFixture.LONG_PATH, 'Full path tooltip lost');
        assert([...$('dir').options].some(option => option.value === 'default'), 'Default directory missing');
        assert(!$('pick-folder').disabled, 'Native folder button is disabled');
      });

      await test('画质/分 P 改动立即生效；慢响应不会重置选择', async () => {
        const { fixture, $, ui } = await setup({ delays: { 'sg-settings-set': 35 } });
        change($('quality'), '80');
        change($('quality'), '64');
        change($('part'), 'all');
        input($('url'), w.VeyloFixture.URLS.bili);
        assert($('quality').value === '64' && $('part').value === 'all', 'Local change was not immediate');
        $('send').click();
        await until(() => fixture.count('sg-deliver') === 1);
        assert(fixture.last('sg-deliver').quality === '64' && fixture.last('sg-deliver').part === 'all', 'Submit used stale settings');
        await until(() => fixture.store.settings.part === 'all');
        await wait(40);
        ui.showTab('download');
        assert($('quality').value === '64', 'Older response reverted quality');
      });

      await test('初始设置读取与本地选项更改竞态不覆盖用户选择', async () => {
        const pending = setup({ delays: { 'sg-settings-get': 45 } });
        await wait(0);
        change(current.$('quality'), '32');
        await pending;
        assert(current.$('quality').value === '32', 'Initial settings response reverted local choice');
      });

      await test('关闭后立即展开不让未落盘设置倒退', async () => {
        const { $, ui } = await setup({ delays: { 'sg-settings-set': 60, 'sg-settings-get': 5 } });
        change($('quality'), '80');
        change($('quality'), '64');
        ui.setActive(false);
        ui.setActive(true);
        await wait(20);
        assert($('quality').value === '64', 'Reactivation overwrote a pending local setting');
      });

      await test('读取请求发出时的未落盘字段，迟到响应也不能回滚', async () => {
        const { $, ui } = await setup();
        current.fixture.store.delays['sg-settings-get'] = 100;
        current.fixture.store.delays['sg-settings-set'] = 20;
        change($('quality'), '80');
        change($('quality'), '64');
        ui.setActive(false);
        ui.setActive(true);
        await wait(130);
        assert($('quality').value === '64', 'Late get snapshot reverted a now-saved local field');
      });

      await test('同 tick 多次改设置后立即关闭，所有 patch 均已发送并保留', async () => {
        const { fixture, $, ui } = await setup({ delays: { 'sg-settings-set': 35 } });
        const before = fixture.count('sg-settings-set');
        change($('quality'), '80');
        change($('part'), 'all');
        change($('quality'), '64');
        assert(fixture.count('sg-settings-set') === before + 3, 'A patch was still queued in the popup context');
        ui.destroy();
        await wait(45);
        assert(fixture.store.settings.qualityBili === '64' && fixture.store.settings.part === 'all', 'Popup closure lost a setting mutation');
      });

      await test('复制链接在共享界面可用且不创建下载任务', async () => {
        const { fixture, $ } = await setup();
        $('copy').click();
        await until(() => fixture.store.copied.length > 0);
        assert(fixture.store.copied === w.SG.cleanUrl(w.VeyloFixture.URLS.bili), 'Copy did not use canonical URL');
        assert(fixture.count('sg-deliver') === 0, 'Copy created a task');
      });

      for (const kind of ['playlist', 'channel']) {
        await test(`YouTube ${kind} 仅交接，不显示或发送直接下载`, async () => {
          const { fixture, $, ui } = await setup({ settings: { media: 'audio-mp3', audioQuality: '256' } }, { url: w.VeyloFixture.URLS[kind], title: '' });
          assert($('send-text').textContent === '送入 FluentYTDL', 'Collection exposed direct download');
          assert($('download-options').hidden && !$('handoff-note').hidden, 'Collection configuration is misleading');
          assert($('audio-quality-row').hidden && $('audio-quality').disabled, 'Collection exposed an audio quality promise');
          $('send').click();
          await until(() => fixture.count('sg-deliver') === 1);
          const payload = fixture.last('sg-deliver');
          assert(payload.mode === 'handoff' && payload.media === 'video' && payload.audioQuality === 'auto', 'Collection sent direct mode or a bitrate promise');
          assert(fixture.store.settings.audioQuality === '256', 'Handoff overwrote the audio preference');
          ui.showTab('download');
        });
      }

      await test('YouTube 字幕/语言选项与参数正确', async () => {
        const { fixture, $ } = await setup({}, { url: w.VeyloFixture.URLS.youtube, title: w.VeyloFixture.TITLES.youtube });
        assert(!$('subtitles-row').hidden, 'YouTube subtitles are missing');
        change($('subtitles'), 'auto');
        change($('sub-langs'), 'en');
        assert(!$('langs-row').hidden, 'Subtitle languages did not reveal');
        $('send').click();
        await until(() => fixture.count('sg-deliver') === 1);
        assert(fixture.last('sg-deliver').subtitles === 'auto' && fixture.last('sg-deliver').subLangs === 'en', 'Subtitle payload incorrect');
      });

      await test('音频隐藏画质/字幕，提交 quality=best/subtitles=off', async () => {
        const { fixture, $ } = await setup({ settings: { quality: '720', subtitles: 'auto' } }, { url: w.VeyloFixture.URLS.youtube });
        change($('media'), 'audio-mp3');
        assert($('quality-row').hidden && $('subtitles-row').hidden, 'Audio retained visual video options');
        $('send').click();
        await until(() => fixture.count('sg-deliver') === 1);
        const payload = fixture.last('sg-deliver');
        assert(payload.media === 'audio-mp3' && payload.quality === 'best' && payload.subtitles === 'off', 'Audio payload incorrect');
      });

      for (const platform of ['youtube', 'x', 'bili']) {
        await test(`${platform} 原音轨仅投递 auto/best/off，保留目录分P与已存转换码率`, async () => {
          const { fixture, $ } = await setup({ jobs: [], settings: { media: 'audio-original', audioQuality: '256', quality: '720', qualityBili: '64', subtitles: 'auto', part: 'all', dir: w.VeyloFixture.LONG_PATH, metadata: true } }, { url: w.VeyloFixture.URLS[platform] });
          $('send').click();
          const payload = fixture.last('sg-deliver');
          assert(payload?.media === 'audio-original' && payload.audioQuality === 'auto' && payload.quality === 'best' && payload.subtitles === 'off', 'Original audio sent conversion options');
          assert(payload.part === 'all' && payload.dir === w.VeyloFixture.LONG_PATH && payload.metadata === (platform !== 'bili'), 'Original audio changed existing directory/part/metadata behavior');
          await until(() => !$('send').disabled);
          assert(fixture.store.jobs[0].media === 'audio-original' && fixture.store.jobs[0].request.audioQuality === 'auto' && fixture.store.jobs[0].state === 'queued' && !fixture.store.jobs[0].files, 'Fixture lost original request or fabricated native output');
          assert(fixture.store.settings.audioQuality === '256' && $('audio-quality').value === '256', 'Original audio reset the saved bitrate');
          if (platform !== 'x') {
            $('handoff').click();
            const handoff = fixture.last('sg-deliver');
            assert(handoff.mode === 'handoff' && handoff.media === 'video' && handoff.audioQuality === 'auto', 'Software handoff promised original audio');
            await until(() => !$('send').disabled);
          }
          change($('media'), 'audio-m4a');
          assert(!$('audio-quality-row').hidden && $('audio-quality').value === '256', 'Returning to conversion lost 256 kbps');
        });
      }

      for (const platform of ['youtube', 'x', 'bili']) {
        for (const media of ['audio-mp3', 'audio-m4a']) {
          await test(`${platform} ${media} 逐项投递字符串 audioQuality，视频参数互不干扰`, async () => {
            const { fixture, $ } = await setup({ jobs: [], settings: { media, quality: '720', qualityBili: '64', subtitles: 'auto' } }, { url: w.VeyloFixture.URLS[platform] });
            for (const audioQuality of ['auto', '320', '256', '192', '128']) {
              change($('audio-quality'), audioQuality);
              const before = fixture.count('sg-deliver');
              $('send').click();
              const payload = fixture.last('sg-deliver');
              assert(payload?.audioQuality === audioQuality, `${platform}/${media}/${audioQuality} audio quality payload incorrect`);
              assert(payload.mode === 'direct' && payload.media === media && payload.quality === 'best' && payload.subtitles === 'off', 'Audio delivery changed the existing format/quality/subtitle contract');
              await until(() => fixture.count('sg-deliver') === before + 1 && !$('send').disabled);
              assert(fixture.store.settings.audioQuality === audioQuality && fixture.store.settings.quality === '720' && fixture.store.settings.qualityBili === '64', 'Delivery overwrote independent preferences');
            }
          });
        }
      }

      for (const platform of ['youtube', 'x', 'bili']) {
        await test(`${platform} 视频投递归一 auto，不覆盖已保存的音频音质`, async () => {
          const { fixture, $ } = await setup({ jobs: [], settings: { media: 'audio-m4a', audioQuality: '256', quality: '720', qualityBili: '64' } }, { url: w.VeyloFixture.URLS[platform] });
          change($('media'), 'video');
          $('send').click();
          const payload = fixture.last('sg-deliver');
          assert(payload?.media === 'video' && payload.audioQuality === 'auto', 'Video did not normalize the request to auto');
          assert(payload.quality === (platform === 'bili' ? '64' : '720'), 'Video quality was changed by the audio preference');
          await until(() => !$('send').disabled);
          assert(fixture.store.settings.audioQuality === '256' && $('audio-quality').value === '256', 'Video reset the saved audio preference');
          change($('media'), 'audio-m4a');
          assert(!$('audio-quality-row').hidden && $('audio-quality').value === '256', 'Returning to audio lost the preference');
        });
      }

      await test('音质读取请求发出时未落盘的字段，迟到响应与其他设置不回滚', async () => {
        const { fixture, $, ui } = await setup({ settings: { media: 'audio-mp3' } });
        fixture.store.delays['sg-settings-get'] = 100;
        fixture.store.delays['sg-settings-set'] = 20;
        change($('theme'), 'dark');
        change($('audio-quality'), '320');
        change($('audio-quality'), '256');
        ui.setActive(false);
        ui.setActive(true);
        await wait(130);
        assert($('audio-quality').value === '256' && fixture.store.settings.audioQuality === '256', 'Late get or unrelated save rolled back audio quality');
      });

      await test('B 站不伪装字幕或媒体信息支持；X 不显示字幕', async () => {
        const { fixture, $, ui } = await setup({ settings: { metadata: true, subtitles: 'auto' } });
        assert($('metadata-row').hidden && $('subtitles-row').hidden, 'Bilibili unsupported options visible');
        $('send').click();
        await until(() => fixture.count('sg-deliver') === 1);
        assert(fixture.last('sg-deliver').metadata === false && fixture.last('sg-deliver').subtitles === 'off', 'Bilibili unsupported options submitted');
        ui.setContext({ url: w.VeyloFixture.URLS.x });
        assert(!$('metadata-row').hidden && $('subtitles-row').hidden && $('handoff').hidden, 'X platform gating incorrect');
      });

      await test('编辑来源链接不会把原视频标题传给新任务', async () => {
        const { fixture, $ } = await setup({}, { url: w.VeyloFixture.URLS.youtube, title: 'Original context title' });
        input($('url'), w.VeyloFixture.URLS.x);
        $('send').click();
        await until(() => fixture.count('sg-deliver') === 1);
        assert(!Object.prototype.hasOwnProperty.call(fixture.last('sg-deliver'), 'title'), 'Stale context title leaked to a different URL');
      });

      await test('活动页异步返回不覆盖用户已粘贴的链接', async () => {
        const { $, ui } = await setup({}, { url: '' });
        input($('url'), w.VeyloFixture.URLS.x);
        const changed = ui.setContext({ url: w.VeyloFixture.URLS.youtube }, { ifUnedited: true });
        assert(changed === false && $('url').value === w.VeyloFixture.URLS.x, 'Active tab overwrote a typed URL');
      });

      await test('原生文件夹取消恢复原值；成功后重挂载恢复新路径', async () => {
        const first = await setup();
        first.fixture.store.pickCanceled = true;
        first.$('pick-folder').click();
        await until(() => first.$('status-text').textContent.includes('已取消'));
        assert(first.$('dir').value === 'default', 'Cancel changed the directory');
        first.fixture.store.pickCanceled = false;
        first.$('pick-folder').click();
        await until(() => first.$('dir').value === w.VeyloFixture.LONG_PATH);
        first.ui.destroy();
        const second = w.SGUi.mount(first.root, { url: w.VeyloFixture.URLS.bili });
        current.ui = second;
        await second.ready;
        assert(second.element.querySelector('#vy-dir').value === w.VeyloFixture.LONG_PATH, 'Native-picked path was not persisted across popup remount');
      });

      await test('通知只在明确勾选时申请，拒绝则还原且不影响下载', async () => {
        const { fixture, $ } = await setup({ permissionDecision: false });
        assert(fixture.count('permissions.request') === 0, 'Automatic permission prompt');
        change($('notify'), true);
        assert(fixture.count('permissions.request') === 1, 'Permission was not requested synchronously in the gesture');
        await until(() => !$('notify').checked);
        assert(!$('send').disabled, 'Permission denial disabled downloads');
        assert($('status-text').textContent.includes('不影响下载'), 'Permission denial explanation missing');
      });

      await test('content 无 permissions API 时提示从工具栏开启通知', async () => {
        const { fixture, $ } = await setup({}, { variant: 'floating' });
        delete fixture.chrome.permissions;
        change($('notify'), true);
        assert(!$('notify').checked, 'Floating UI retained an unavailable notification setting');
        assert($('status-text').textContent.includes('从工具栏打开 Veylo'), 'Toolbar guidance missing for content scripts');
        assert(!$('send').disabled, 'Missing permissions API disabled downloading');
        await until(() => fixture.count('sg-settings-set') >= 1);
        await wait(5);
      });

      await test('通知允许后持久化；权限等待中关闭开关不会重新开启', async () => {
        const { fixture, $ } = await setup({ permissionDecision: true });
        change($('notify'), true);
        await until(() => fixture.store.settings.notify === true);
        change($('notify'), false);
        await until(() => fixture.store.settings.notify === false);
        change($('notify'), true);
        change($('notify'), false);
        await wait(15);
        assert(!$('notify').checked && fixture.store.settings.notify === false, 'Late permission result re-enabled notifications');
        await until(() => fixture.count('sg-settings-set') >= 3);
        await wait(5);
      });

      await test('桥接连通但内核缺失必须显示异常', async () => {
        const { $ } = await setup({ engineAvailable: { x: false, bili: true } });
        $('bridge-check').click();
        await until(() => $('bridge-check').getAttribute('aria-busy') === 'false');
        assert($('status').dataset.tone === 'err' && $('status-text').textContent.includes('未找到下载内核'), 'Missing engine reported green');
      });

      await test('历史多任务包含标题、平台、路径、进度，重试仅失败可见', async () => {
        const { $, ui } = await setup();
        ui.showTab('history');
        const cards = [...$('jobs').querySelectorAll('.vy-job')];
        assert(cards.length === 4, 'Multiple tasks missing');
        assert(cards[2].querySelector('.vy-job-media').textContent === 'MP3 · 256 kbps' && cards[3].querySelector('.vy-job-media').textContent === 'M4A · 内核默认', 'Demo audio jobs do not preview requested/default quality');
        assert($('jobs').querySelectorAll('button[data-action="retry"]').length === 1, 'Retry visible on a non-failed task');
        assert($('jobs').querySelectorAll('button[data-action="folder"]').length === 4, 'Open-folder actions missing');
        assert($('jobs').querySelector('[role="progressbar"]').getAttribute('aria-valuenow') === '46', 'Progress value incorrect');
        assert($('active-count').textContent === '2', 'Active task count incorrect');
      });

      await test('历史音质来自原请求；旧记录和非法音质显示内核默认，不读取当前偏好', async () => {
        const jobs = [
          { id: 'requested-mp3', media: 'audio-mp3', audioQuality: '320', request: { audioQuality: '256' }, label: 'MP3 · 256 kbps' },
          { id: 'requested-m4a', media: 'audio-m4a', request: { audioQuality: '192' }, label: 'M4A · 192 kbps' },
          { id: 'legacy-mp3', media: 'audio-mp3', audioQuality: '320', label: 'MP3 · 内核默认' },
          { id: 'invalid-m4a', media: 'audio-m4a', request: { audioQuality: 'lossless' }, label: 'M4A · 内核默认' },
          { id: 'numeric-mp3', media: 'audio-mp3', request: { audioQuality: 256 }, label: 'MP3 · 内核默认' },
          { id: 'requested-video', media: 'video', request: { audioQuality: '320' }, label: '视频' }
        ];
        const { $ } = await setup({ settings: { audioQuality: '128' }, jobs: jobs.map(job => ({ ...job, platform: 'x', url: w.VeyloFixture.URLS.x, state: 'queued' })) });
        for (const job of jobs) {
          const media = $('jobs').querySelector(`[data-job="x:${job.id}"]`).querySelector('.vy-job-media');
          assert(media.textContent === job.label, `${job.id} did not display the original requested quality`);
        }
        change($('audio-quality'), '320');
        assert($('jobs').querySelector('[data-job="x:requested-mp3"]').querySelector('.vy-job-media').textContent === 'MP3 · 256 kbps', 'Global preference changed a history label');
      });

      await test('提交中的偏好改动不改变乐观记录与刷新后的原请求音质', async () => {
        const { fixture, $ } = await setup({ jobs: [], settings: { media: 'audio-mp3', audioQuality: '256' }, delays: { 'sg-deliver': 20, 'sg-history-refresh': 60 } });
        $('send').click();
        change($('audio-quality'), '128');
        change($('media'), 'video');
        await until(() => $('jobs').querySelectorAll('.vy-job').length === 1);
        assert($('jobs').querySelector('.vy-job-media').textContent === 'MP3 · 256 kbps', 'Optimistic delivery used the current preference instead of the submitted request');
        await until(() => !$('send').disabled);
        assert($('jobs').querySelector('.vy-job-media').textContent === 'MP3 · 256 kbps', 'History refresh lost the original requested quality');
        const job = fixture.store.jobs[0];
        assert(job.request?.audioQuality === '256' && job.request.media === 'audio-mp3' && job.request.quality === 'best', 'Fixture did not snapshot the original request');
        assert(job.state === 'queued' && !job.files, 'Synthetic delivery fabricated native encoding success');
      });

      await test('原音轨历史与乐观投递/重试按原media显示不转码，不使用当前码率', async () => {
        const original = { ...w.VeyloFixture.makeJobs()[2], media: 'audio-original', request: { media: 'audio-original', audioQuality: 'auto', quality: 'best', subtitles: 'off' } };
        const { fixture, $ } = await setup({ jobs: [original], settings: { media: 'audio-mp3', audioQuality: '256' }, delays: { 'sg-deliver': 20, 'sg-history-refresh': 60 } });
        assert($('jobs').querySelector('.vy-job-media').textContent === '原音轨 · 不转码', 'Original history was mislabeled as a converted format');
        $('jobs').querySelector('button[data-action="retry"]').click();
        await until(() => $('jobs').querySelectorAll('.vy-job').length === 2);
        assert($('jobs').querySelector('.vy-job-media').textContent === '原音轨 · 不转码', 'Optimistic retry used the current conversion preference');
        await until(() => !$('jobs').querySelector('button[data-action="retry"]').disabled);
        assert(fixture.store.jobs[0].media === 'audio-original' && fixture.store.jobs[0].request.audioQuality === 'auto', 'Retry changed the original request');
        change($('media'), 'audio-original');
        $('send').click();
        change($('media'), 'audio-m4a');
        await until(() => $('jobs').querySelectorAll('.vy-job').length === 3);
        assert($('jobs').querySelector('.vy-job-media').textContent === '原音轨 · 不转码', 'Optimistic delivery used the changed global format');
        await until(() => !$('send').disabled);
        assert([...$('jobs').querySelectorAll('.vy-job-media')].every(media => media.textContent === '原音轨 · 不转码'), 'Refresh mislabeled source jobs');
        assert(fixture.store.settings.audioQuality === '256', 'Original delivery/retry changed the saved bitrate');
      });

      await test('历史标题/错误按纯文本渲染，不解释 HTML', async () => {
        const job = w.VeyloFixture.makeJobs()[2];
        job.title = '<img src=x onerror="throw 1"> & <b>not markup</b>';
        job.error = '<svg onload="throw 2">synthetic error';
        const { $ } = await setup({ jobs: [job] });
        assert($('jobs').querySelector('.vy-job-title').textContent === job.title, 'Title not preserved as text');
        assert(!$('jobs').querySelector('img') && !$('jobs').querySelector('b'), 'Dynamic HTML was interpreted');
        assert($('jobs').querySelector('.vy-job-error').textContent === job.error, 'Error interpreted as HTML');
      });

      await test('失败任务重试后刷新记录，打开位置使用 jobId/platform', async () => {
        const { fixture, $ } = await setup({ jobs: w.VeyloFixture.makeJobs().filter(job => job.state === 'failed') });
        $('jobs').querySelector('button[data-action="retry"]').click();
        await until(() => fixture.count('sg-job-retry') === 1 && fixture.count('sg-history-refresh') >= 1);
        await wait(10);
        const folder = $('jobs').querySelector('button[data-action="folder"]');
        folder.click();
        await until(() => fixture.count('sg-job-open-folder') === 1);
        assert(fixture.last('sg-job-open-folder').jobId && fixture.last('sg-job-open-folder').platform, 'Open-folder payload incomplete');
        assert(fixture.store.jobs.some(job => job.state === 'queued'), 'Retry did not create queued job');
      });

      for (const legacy of [false, true]) {
        await test(`失败音频重试保留${legacy ? '旧记录内核默认' : '原请求256 kbps'}，乐观记录与刷新均不使用当前偏好`, async () => {
          const original = w.VeyloFixture.makeJobs()[2];
          if (legacy) delete original.request;
          else original.request = { url: original.url, media: 'audio-mp3', audioQuality: '256', quality: 'best', dir: original.downloadDir, mode: 'direct' };
          const { fixture, $ } = await setup({ jobs: [original], settings: { audioQuality: '128' }, delays: { 'sg-history-refresh': 60 } });
          $('jobs').querySelector('button[data-action="retry"]').click();
          await until(() => $('jobs').querySelectorAll('.vy-job').length === 2);
          const label = legacy ? 'MP3 · 内核默认' : 'MP3 · 256 kbps';
          assert($('jobs').querySelector('.vy-job-media').textContent === label, 'Optimistic retry lost the original request quality');
          await until(() => !$('jobs').querySelector('button[data-action="retry"]').disabled);
          assert($('jobs').querySelector('.vy-job-media').textContent === label, 'Refreshed retry lost the original request quality');
          assert(fixture.store.jobs[0].request?.audioQuality === (legacy ? 'auto' : '256'), 'Fixture retry did not preserve original quality or default legacy to auto');
          assert(fixture.store.settings.audioQuality === '128', 'Retry changed the saved audio preference');
        });
      }

      await test('清历史需内联确认/取消；只清结束记录并保留 active', async () => {
        const { fixture, $ } = await setup();
        $('history-clear').click();
        assert(!$('clear-confirm').hidden && fixture.count('sg-history-clear') === 0, 'Clear skipped confirmation');
        $('clear-cancel').click();
        assert($('clear-confirm').hidden && fixture.count('sg-history-clear') === 0, 'Cancel cleared history');
        $('history-clear').click();
        $('clear-accept').click();
        await until(() => fixture.count('sg-history-clear') === 1 && $('clear-confirm').hidden);
        assert(fixture.store.jobs.length === 2 && fixture.store.jobs.every(job => ['queued', 'downloading'].includes(job.state)), 'Clear removed active jobs or kept ended records');
      });

      await test('清除记录与在途历史刷新不会互相回滚', async () => {
        const { fixture, $, ui } = await setup({ delays: { 'sg-history-refresh': 40 } });
        fixture.store.snapshotHistory = true;
        const inFlight = ui.refreshHistory(true);
        $('history-clear').click();
        $('clear-accept').click();
        await until(() => fixture.count('sg-history-clear') === 1 && $('clear-confirm').hidden);
        await inFlight;
        await wait(10);
        assert($('jobs').querySelectorAll('.vy-job').length === 2, 'An older history snapshot resurrected cleared records');
      });

      await test('空态与失败读取状态清晰，仍可返回添加下载', async () => {
        const { fixture, $, ui } = await setup({ jobs: [] });
        ui.showTab('history');
        assert(!$('history-empty').hidden && $('empty-title').textContent === '还没有任务', 'Empty history state missing');
        fixture.store.errors['sg-history-refresh'] = '合成读取失败';
        await ui.refreshHistory(true);
        assert(!$('history-error').hidden, 'History failure was hidden');
        $('empty-download').click();
        assert(!$('download-panel').hidden, 'Empty state cannot return to download');
      });

      await test('3 秒历史轮询无重叠；暂停/销毁清除 interval', async () => {
        const { fixture, ui } = await setup({ delays: { 'sg-history-refresh': 35 } });
        const timer = [...timers.values()][0];
        assert(timer?.delay === 3000 && timers.size === 1, 'History polling cadence or count incorrect');
        timer.callback(); timer.callback(); timer.callback();
        await wait(5);
        assert(fixture.count('sg-history-refresh') === 1 && fixture.store.maxHistoryInFlight === 1, 'Overlapping history polls');
        await ui.refreshHistory(true);
        ui.setActive(false);
        assert(timers.size === 0, 'Paused component leaked interval');
        ui.setActive(true);
        assert(timers.size === 1, 'Resume duplicated intervals');
        ui.destroy();
        assert(timers.size === 0, 'Destroy leaked interval');
      });

      await test('重复挂载同容器会销毁旧组件，不重复 UI 或轮询', async () => {
        const { root } = await setup();
        const fresh = w.SGUi.mount(root, { url: w.VeyloFixture.URLS.x });
        current.ui = fresh;
        await fresh.ready;
        assert(root.querySelectorAll('.vy-app').length === 1 && timers.size === 1, 'Duplicate mount leaked UI or timer');
      });

      // ---- 2.3.0 contract ----
      const capsReady = (fixture, count = 1) => until(() => fixture.count('sg-bridge-ping') >= count).then(() => wait(5));
      const active = () => ({ id: 'c'.repeat(32), platform: 'youtube', url: w.VeyloFixture.URLS.youtube, title: 'Active synthetic', downloadDir: 'D:\\Veylo Demo (synthetic)\\Films', state: 'downloading', percent: 20, media: 'video', request: { media: 'video', audioQuality: 'auto' }, createdAt: '2026-10-15T08:00:00.000Z' });
      const canceled = () => ({ ...active(), id: 'd'.repeat(32), state: 'canceled', error: '已取消下载。', percent: 40, request: { url: w.VeyloFixture.URLS.youtube, mode: 'direct', media: 'video', audioQuality: 'auto', sponsorblock: 'sponsor', section: '1:30-2:45' } });

      await test('进行中任务可取消：内联确认，继续下载不发送，确认后发送 jobId/platform', async () => {
        const { fixture, $, ui } = await setup({ hosts: 'current', jobs: [active(), w.VeyloFixture.makeJobs()[1]] });
        ui.showTab('history');
        await capsReady(fixture);
        const cancel = () => $('jobs').querySelector('button[data-action="cancel"]');
        assert($('jobs').querySelectorAll('button[data-action="cancel"]').length === 1, 'Cancel must appear only on active jobs');
        assert(cancel().textContent.includes('取消') && !cancel().disabled, 'Cancel button missing or disabled');
        cancel().click();
        const confirm = $('jobs').querySelector('.vy-inline-confirm');
        assert(confirm && confirm.textContent.includes('取消后已下载的部分将被删除，已完成的文件保留。'), 'Inline cancel confirmation missing');
        assert(confirm.getAttribute('role') === 'group' && confirm.getAttribute('aria-label'), 'Confirmation is not an accessible group');
        $('jobs').querySelector('button[data-action="cancel-keep"]').click();
        assert(!$('jobs').querySelector('.vy-inline-confirm') && fixture.count('sg-job-cancel') === 0, 'Keep downloading sent a cancel');
        cancel().click();
        const accept = $('jobs').querySelector('button[data-action="cancel-accept"]');
        assert(accept.textContent.includes('取消下载') && $('jobs').querySelector('button[data-action="cancel-keep"]').textContent.includes('继续下载'), 'Confirm button copy incorrect');
        accept.click();
        await until(() => fixture.count('sg-job-cancel') === 1);
        const payload = fixture.last('sg-job-cancel');
        assert(payload.jobId === 'c'.repeat(32) && payload.platform === 'youtube' && Object.keys(payload).length === 3, 'Cancel payload incorrect');
        await until(() => !$('jobs').querySelector('button[data-action="cancel"]'));
        assert($('jobs').querySelector(`[data-job="youtube:${'c'.repeat(32)}"]`).querySelector('.vy-job-state').textContent === '取消中', 'Cancel request state not shown');
      });

      await test('旧本机组件不支持取消时按钮禁用并提示更新', async () => {
        const { fixture, $, ui } = await setup({ jobs: [active()] });
        ui.showTab('history');
        await capsReady(fixture);
        const cancel = $('jobs').querySelector('button[data-action="cancel"]');
        assert(cancel && cancel.disabled && cancel.title.includes('请更新本机组件后使用'), 'Unsupported cancel was not disabled with guidance');
      });

      await test('已取消卡片：已取消/idle、重新下载用原请求、打开位置、清除已结束', async () => {
        const { fixture, $, ui } = await setup({ hosts: 'current', jobs: [canceled()] });
        ui.showTab('history');
        const card = $('jobs').querySelector('.vy-job');
        assert(card.querySelector('.vy-job-state').textContent === '已取消' && card.querySelector('.vy-job-state').dataset.tone === 'idle', 'Canceled status incorrect');
        assert(!card.querySelector('[role="progressbar"]') && !card.querySelector('button[data-action="cancel"]'), 'Canceled card still looks active');
        const retry = card.querySelector('button[data-action="retry"]');
        assert(retry && retry.textContent.trim() === '重新下载' && card.querySelector('button[data-action="folder"]'), `Canceled actions missing: ${retry && retry.textContent}`);
        assert(card.querySelector('.vy-job-media').textContent === '视频 · 跳过片段 · 片段 1:30–2:45', 'Media tags missing');
        assert(w.SG.jobView({ state: 'canceled' }).text === '已取消下载' && w.SG.jobView({ state: 'canceled' }).tone === 'idle', 'jobView canceled incorrect');
        assert(!$('history-clear').hidden, 'Clear-ended hidden for canceled records');
        retry.click();
        await until(() => fixture.count('sg-job-retry') === 1);
        await until(() => fixture.store.jobs.length === 2);
        assert(fixture.store.jobs[0].request.sponsorblock === 'sponsor' && fixture.store.jobs[0].request.section === '1:30-2:45', 'Re-download did not reuse the original request');
        await wait(30);
        assert($('jobs').querySelectorAll('.vy-job').length === 2 && $('jobs').querySelector('[data-job="youtube:' + 'd'.repeat(32) + '"]').querySelector('.vy-job-state').textContent === '已取消', 'Canceled record disappeared after re-download');
        $('history-clear').click();
        $('clear-accept').click();
        await until(() => fixture.count('sg-history-clear') === 1 && fixture.store.jobs.length === 1);
        assert(!fixture.store.jobs.some(job => job.state === 'canceled'), 'Clear kept canceled records');
        assert(!$('jobs').querySelector('[data-job="youtube:' + 'd'.repeat(32) + '"]'), 'Canceled card survived clearing');
      });

      await test('新选项按平台显示：YouTube 跳过片段/片段，X 片段，B 站弹幕/字幕', async () => {
        const { fixture, $, ui } = await setup({ hosts: 'current' }, { url: w.VeyloFixture.URLS.youtube });
        await capsReady(fixture);
        const visible = names => names.every(name => !$(name).hidden);
        const hiddenAll = names => names.every(name => $(name).hidden);
        assert(visible(['sponsorblock-row', 'section-row']) && hiddenAll(['danmaku-row', 'bili-subtitles-row', 'items-row']), 'YouTube controls incorrect');
        assert([...$('sponsorblock').options].map(o => o.value).join() === 'off,sponsor,all', 'SponsorBlock choices incorrect');
        assert($('section').getAttribute('placeholder') === '如 1:30-2:45，留空为完整视频' && ui.element.querySelector('label[for="vy-section"]').textContent === '片段（可选）', 'Section copy incorrect');
        assert(!$('sponsorblock').disabled && !$('section').disabled && $('update-note').hidden, 'Supported controls disabled');
        ui.setContext({ url: w.VeyloFixture.URLS.x });
        assert(!$('section-row').hidden && hiddenAll(['sponsorblock-row', 'danmaku-row', 'bili-subtitles-row']), 'X controls incorrect');
        ui.setContext({ url: w.VeyloFixture.URLS.bili });
        await capsReady(fixture, 2);
        assert(visible(['danmaku-row', 'bili-subtitles-row']) && hiddenAll(['sponsorblock-row', 'section-row', 'items-row']), 'Bilibili controls incorrect');
        assert([...$('danmaku').options].map(o => o.value).join() === 'off,ass' && [...$('bili-subtitles').options].map(o => o.value).join() === 'off,srt', 'Bilibili extras choices incorrect');
        change($('danmaku'), 'ass');
        change($('bili-subtitles'), 'srt');
        assert(fixture.last('sg-settings-set').settings.biliSubtitles === 'srt', 'Bilibili subtitles not persisted');
        assert($('more-value').textContent.includes('弹幕') && $('more-value').textContent.includes('字幕'), 'More summary lacks extras');
        $('send').click();
        const payload = fixture.last('sg-deliver');
        assert(payload.danmaku === 'ass' && payload.biliSubtitles === 'srt' && payload.sponsorblock === 'off' && payload.section === '' && payload.live === 'off', 'Bilibili payload incorrect');
        await until(() => !$('send').disabled);
      });

      await test('旧本机组件：新选项禁用并提示，投递归一为关闭', async () => {
        const { fixture, $, ui } = await setup({ settings: { sponsorblock: 'all', danmaku: 'xml' } }, { url: w.VeyloFixture.URLS.youtube });
        await capsReady(fixture);
        assert($('sponsorblock').disabled && $('section').disabled, 'Unsupported YouTube controls enabled');
        assert(!$('update-note').hidden && $('update-note').textContent.includes('请更新本机组件后使用'), 'Update guidance missing');
        $('send').click();
        let payload = fixture.last('sg-deliver');
        assert(payload.sponsorblock === 'off' && payload.section === '' && payload.live === 'off', 'Unsupported fields were sent');
        await until(() => !$('send').disabled);
        ui.setContext({ url: w.VeyloFixture.URLS.bili });
        await capsReady(fixture, 2);
        assert($('danmaku').disabled && $('bili-subtitles').disabled, 'Unsupported Bilibili controls enabled');
        $('send').click();
        payload = fixture.last('sg-deliver');
        assert(payload.danmaku === 'off' && payload.biliSubtitles === 'off', 'Unsupported Bilibili fields were sent');
        assert(fixture.store.settings.sponsorblock === 'all' && fixture.store.settings.danmaku === 'xml', 'Gating overwrote saved preferences');
        await until(() => !$('send').disabled);
      });

      await test('跳过片段在原音轨时禁用并说明', async () => {
        const { fixture, $ } = await setup({ hosts: 'current', settings: { sponsorblock: 'sponsor' } }, { url: w.VeyloFixture.URLS.youtube });
        await capsReady(fixture);
        change($('media'), 'audio-original');
        assert($('sponsorblock').disabled && !$('sponsorblock-note').hidden && $('sponsorblock-note').textContent.includes('原音轨不重新编码'), 'Original audio did not explain SponsorBlock');
        change($('media'), 'audio-mp3');
        assert(!$('sponsorblock').disabled && $('sponsorblock-note').hidden, 'SponsorBlock not re-enabled');
        assert($('more-value').textContent.includes('跳过片段'), 'Summary lacks SponsorBlock');
        $('send').click();
        assert(fixture.last('sg-deliver').sponsorblock === 'sponsor' && fixture.last('sg-deliver').live === 'off', 'Ordinary audio lost SponsorBlock');
        await until(() => !$('send').disabled);
      });

      await test('片段输入校验：非法阻止提交，合法规范化发送，与原音轨互斥', async () => {
        const { fixture, $ } = await setup({ hosts: 'current', jobs: [] }, { url: w.VeyloFixture.URLS.youtube });
        await capsReady(fixture);
        for (const bad of ['abc', '2:00-1:00', '1:61-2:00', '5-5', '0-86401', '1-2-3']) {
          input($('section'), bad);
          assert(!$('section-error').hidden && $('section').getAttribute('aria-invalid') === 'true' && $('send').disabled, `${bad} was accepted`);
        }
        input($('section'), ' 1:30 - 2:45 ');
        assert($('section-error').hidden && !$('send').disabled && $('section').getAttribute('aria-invalid') === 'false', 'Valid section rejected');
        $('send').click();
        assert(fixture.last('sg-deliver').section === '1:30-2:45', 'Section not normalized');
        await until(() => !$('send').disabled);
        await until(() => $('jobs').querySelectorAll('.vy-job').length === 1);
        assert($('jobs').querySelector('.vy-job-media').textContent === '视频 · 片段 1:30–2:45', 'Section tag missing');
        for (const ok of ['0-inf', '01:02:03-01:02:04', '10-20']) { input($('section'), ok); assert($('section-error').hidden, `${ok} rejected`); }
        change($('media'), 'audio-original');
        assert($('section').disabled && !$('section-note').hidden, 'Original audio allowed a section');
        $('send').click();
        assert(fixture.last('sg-deliver').section === '', 'Disabled section was sent');
        await until(() => !$('send').disabled);
        change($('media'), 'video');
        input($('url'), w.VeyloFixture.URLS.x);
        assert(!$('section-row').hidden && $('section').value === '', 'Section did not reset for a new link');
      });

      await test('YouTube 播放列表：下载列表与送入 FluentYTDL 两个动作，可选范围校验', async () => {
        const { fixture, $ } = await setup({ hosts: 'current', jobs: [] }, { url: w.VeyloFixture.URLS.playlist, title: '' });
        await capsReady(fixture);
        assert($('send-text').textContent === '下载列表' && !$('handoff').hidden && $('handoff-text').textContent === '送入 FluentYTDL', 'Playlist actions incorrect');
        assert(!$('download-options').hidden && !$('items-row').hidden && $('section-row').hidden && $('handoff-note').hidden, 'Playlist options incorrect');
        input($('items'), '1-10;x');
        assert(!$('items-error').hidden && $('send').disabled, 'Invalid item spec accepted');
        input($('items'), '1-10, 15');
        assert($('items-error').hidden && !$('send').disabled, 'Valid item spec rejected');
        $('send').click();
        const payload = fixture.last('sg-deliver');
        assert(payload.mode === 'direct' && payload.playlist === true && payload.items === '1-10,15' && payload.section === '' && payload.live === 'off', 'Playlist direct payload incorrect');
        await until(() => !$('send').disabled);
        await until(() => $('jobs').querySelectorAll('.vy-job').length === 1);
        assert($('jobs').querySelector('.vy-job-media').textContent === '视频 · 列表 11 项', 'Playlist tag missing');
        $('handoff').click();
        const handoff = fixture.last('sg-deliver');
        assert(handoff.mode === 'handoff' && handoff.playlist === false, 'Handoff sent a playlist direct flag');
        await until(() => !$('send').disabled);
      });

      await test('列表标签：终态以实际 fileCount 为准，进行中最多 200 项且开放范围不猜数量', async () => {
        const cases = [
          { state: 'completed', items: '1-500', fileCount: 200, label: '视频 · 列表 200 项' },
          { state: 'failed', items: '1-500', fileCount: 0, label: '视频 · 列表 0 项' },
          { state: 'canceled', items: '1-500', fileCount: 7, label: '视频 · 列表 7 项' },
          { state: 'completed', items: '1-', fileCount: 200, label: '视频 · 列表 200 项' },
          { state: 'downloading', items: '1-500', fileCount: 3, label: '视频 · 列表 200 项' },
          { state: 'queued', items: '1-', label: '视频 · 列表' },
          { state: 'downloading', items: '1,5-', fileCount: 8, label: '视频 · 列表 8 项' }
        ];
        const jobs = cases.map((entry, index) => ({ ...active(), ...entry, id: `playlist-count-${index}`, url: w.VeyloFixture.URLS.playlist, request: { playlist: true, items: entry.items } }));
        const { $ } = await setup({ hosts: 'current', jobs });
        for (let index = 0; index < cases.length; index += 1) {
          const text = $('jobs').querySelector(`[data-job="youtube:playlist-count-${index}"]`).querySelector('.vy-job-media').textContent;
          assert(text === cases[index].label, `${cases[index].state}/${cases[index].items}: expected ${cases[index].label}, got ${text}`);
        }
      });

      await test('频道即使本机组件支持列表也仅交接', async () => {
        const { fixture, $ } = await setup({ hosts: 'current' }, { url: w.VeyloFixture.URLS.channel, title: '' });
        await capsReady(fixture);
        assert($('send-text').textContent === '送入 FluentYTDL' && $('download-options').hidden && $('items-row').hidden, 'Channel exposed direct download');
      });

      await test('旧本机组件的播放列表保持交接并提示更新', async () => {
        const { fixture, $ } = await setup({}, { url: w.VeyloFixture.URLS.playlist, title: '' });
        await capsReady(fixture);
        assert($('send-text').textContent === '送入 FluentYTDL' && !$('update-note').hidden && $('update-note').textContent.includes('列表下载'), 'Legacy playlist state incorrect');
      });

      await test('B 站合集选项按能力显示；番剧/课程显示当前/全部剧集', async () => {
        const { fixture, $, ui } = await setup({ hosts: 'current' });
        await capsReady(fixture);
        assert([...$('part').options].map(o => `${o.value}:${o.textContent}`).join('|') === 'current:当前分 P|all:全部分 P|collection:整个合集', 'Video collection option missing');
        change($('part'), 'collection');
        $('send').click();
        assert(fixture.last('sg-deliver').part === 'collection', 'Collection part not sent');
        await until(() => !$('send').disabled);
        ui.setContext({ url: w.VeyloFixture.URLS.bangumi });
        assert(!$('part-row').hidden && [...$('part').options].map(o => `${o.value}:${o.textContent}`).join('|') === 'current:当前剧集|collection:全部剧集' && $('part').value === 'collection', 'Bangumi part row incorrect');
        const legacy = await setup({ settings: { part: 'collection' } });
        await capsReady(legacy.fixture);
        assert(![...legacy.$('part').options].some(o => o.value === 'collection') && legacy.$('part').value === 'current', 'Legacy host showed collection');
        legacy.$('send').click();
        assert(legacy.fixture.last('sg-deliver').part === 'current', 'Unsupported collection part was sent');
        await until(() => !legacy.$('send').disabled);
        legacy.ui.setContext({ url: w.VeyloFixture.URLS.bangumi });
        assert(!legacy.$('part-row').hidden && legacy.$('part').disabled, 'Legacy bangumi part select enabled');
      });

      await test('精简后没有直播/XML/登录入口，旧偏好不影响普通下载', async () => {
        const { fixture, $, ui } = await setup({ hosts: 'current', jobs: [], settings: { danmaku: 'xml', liveMinutes: '720' } }, { url: w.VeyloFixture.URLS.youtube });
        await capsReady(fixture);
        assert(!$('live') && !$('live-row') && !$('live-minutes') && !$('login'), 'Retired controls still exist');
        $('send').click();
        await until(() => fixture.count('sg-deliver') === 1 && !$('send').disabled);
        assert(fixture.last('sg-deliver').live === 'off' && fixture.last('sg-deliver').liveMinutes === '240', 'Retired preference affected download');
        ui.setContext({ url: w.VeyloFixture.URLS.bili }); await capsReady(fixture, 2);
        assert($('danmaku').value === 'off' && [...$('danmaku').options].map(o => o.value).join() === 'off,ass', 'XML was not retired safely');
      });

      await test('旧直播/XML失败记录保留位置但不显示误导的重试按钮', async () => {
        const jobs = w.VeyloFixture.makeJobs().filter(job => job.state === 'failed');
        jobs[0].request.live = 'from-start';
        const { $, ui } = await setup({ hosts: 'current', jobs });
        ui.showTab('history'); await until(() => $('jobs').children.length === 1);
        assert(!$('jobs').querySelector('button[data-action="retry"]') && $('jobs').querySelector('button[data-action="folder"]'), 'Retired job actions incorrect');
        assert($('jobs').textContent.includes('此旧任务使用已停用功能'), 'Retired job explanation missing');
        jobs[0].request.live = 'off'; jobs[0].request.danmaku = 'xml';
        current.fixture.store.jobs = jobs; await ui.refreshHistory(false);
        assert(!$('jobs').querySelector('button[data-action="retry"]'), 'Old XML request remained retryable');
      });
      await test('本机返回登录字段也不显示徽标或凭据，并复用宿主能力查询', async () => {
        const { fixture, $, ui } = await setup({ hosts: 'current', jobs: [], pingExtra: { x: { login: { youtube: 'SECRET-COOKIE-VALUE' }, appRoot: 'D:/synthetic-root' } } }, { url: w.VeyloFixture.URLS.youtube });
        await capsReady(fixture);
        ui.setContext({ url: w.VeyloFixture.URLS.x });
        ui.setContext({ url: w.VeyloFixture.URLS.bili }); await capsReady(fixture, 2);
        ui.setContext({ url: w.VeyloFixture.URLS.youtube }); await wait(10);
        assert(fixture.count('sg-bridge-ping') === 2, 'Host queries duplicated');
        assert(!$('login') && !ui.element.textContent.includes('SECRET') && !ui.element.textContent.includes('synthetic-root'), 'Retired login data reached UI');
      });

      await test('浮窗收起再打开仅重新查询当前宿主一次，能力刷新且重复激活不多查', async () => {
        const { fixture, $, ui } = await setup({ hosts: 'current', jobs: [] }, { url: w.VeyloFixture.URLS.youtube, variant: 'floating' });
        assert(!$('login') && !$('section').disabled, 'Initial capabilities missing');
        const before = fixture.count('sg-bridge-ping');
        ui.setActive(false);
        fixture.store.login.x.youtube = 'expired';
        fixture.store.features.x = fixture.store.features.x.filter(feature => feature !== 'sections');
        ui.setActive(true);
        ui.setActive(true);
        ui.setContext({ url: w.VeyloFixture.URLS.x });
        ui.setContext({ url: w.VeyloFixture.URLS.youtube });
        await until(() => $('section').disabled);
        await wait(10);
        assert(fixture.count('sg-bridge-ping') === before + 1 && fixture.last('sg-bridge-ping').host === 'x', 'Reopening duplicated pings or queried an unused host');
        assert($('section').disabled, 'Reopening retained old capabilities');
      });

      await test('旧打开周期的迟到 ping 不覆盖新状态或提前结束新查询', async () => {
        const { fixture, $, ui } = await setup({ hosts: 'current', jobs: [], settings: { dir: w.VeyloFixture.LONG_PATH } }, { url: w.VeyloFixture.URLS.youtube, variant: 'floating' });
        const send = fixture.chrome.runtime.sendMessage.bind(fixture.chrome.runtime);
        const pending = [];
        fixture.chrome.runtime.sendMessage = (payload, callback) => {
          if (payload.type === 'sg-bridge-ping') pending.push(callback);
          else send(payload, callback);
        };
        ui.setActive(false);
        ui.setActive(true);
        ui.setActive(false);
        ui.setActive(true);
        assert(pending.length === 2, 'Each reopening must start exactly one host query');
        pending[0]({ ok: true, features: w.VeyloFixture.FEATURES.x, login: { youtube: 'signed-in', x: 'guest' } });
        await wait(5);
        assert(!$('login') && $('section').disabled, 'A stale ping overwrote the pending current query');
        $('pick-folder').click();
        await wait(5);
        assert(fixture.count('sg-pick-folder') === 0 && pending.length === 2, 'A stale completion lost the current in-flight query');
        pending[1]({ ok: true, features: ['pick-folder-initial'], login: { youtube: 'expired', x: 'guest' } });
        await until(() => fixture.count('sg-pick-folder') === 1);
        assert(!$('login') && $('section').disabled, 'The current ping was not applied');
        assert(fixture.last('sg-pick-folder').initialDir === w.VeyloFixture.LONG_PATH, 'Folder picker did not await current capabilities');
        await until(() => !$('pick-folder').disabled);
      });

      await test('重开后旧连接检查的迟到响应不回滚能力', async () => {
        const { fixture, $, ui } = await setup({ hosts: 'current', jobs: [] }, { url: w.VeyloFixture.URLS.youtube, variant: 'floating' });
        const send = fixture.chrome.runtime.sendMessage.bind(fixture.chrome.runtime);
        const pending = [];
        fixture.chrome.runtime.sendMessage = (payload, callback) => {
          if (payload.type === 'sg-bridge-ping') pending.push({ host: payload.host, callback });
          else send(payload, callback);
        };
        $('bridge-check').click();
        assert(pending.length === 2, 'Connection check must query both hosts');
        fixture.chrome.runtime.sendMessage = send;
        fixture.store.login.x.youtube = 'expired';
        fixture.store.features.x = fixture.store.features.x.filter(feature => feature !== 'sections');
        ui.setActive(false);
        ui.setActive(true);
        await until(() => $('section').disabled);
        $('copy').click();
        await until(() => $('status-text').textContent === '链接已复制');
        for (const { host, callback } of pending) callback({ ok: true, features: w.VeyloFixture.FEATURES[host], login: host === 'x' ? { youtube: 'signed-in', x: 'guest' } : { bilibili: 'signed-in' } });
        await until(() => $('bridge-check').getAttribute('aria-busy') === 'false');
        assert(!$('login') && $('section').disabled, 'A connection check from the previous opening overwrote fresh host state');
        assert($('status-text').textContent === '链接已复制', 'A stale connection check overwrote the current action status');
      });

      await test('检查本机连接只显示连通与内核状态，不声称已登录或过期', async () => {
        const { $, ui } = await setup({ hosts: 'current', login: { x: { youtube: 'signed-in', x: 'expired' } } });
        $('bridge-check').click(); await until(() => $('bridge-check').getAttribute('aria-busy') === 'false');
        const text = $('status-text').textContent;
        assert(text.includes('YouTube / X：已连接') && text.includes('bilibili：已连接'), 'Connection summary missing');
        assert(!$('login') && !/已登录|已过期|访客/.test(ui.element.textContent), 'Retired login claims displayed');
      });

      await test('连接检查丢弃未知登录值与凭据样式字段', async () => {
        const { $, ui } = await setup({ hosts: 'current', jobs: [], login: { bili: { bilibili: 'SYNTHETIC-SECRET' }, x: { youtube: 'signed-in', x: 'SYNTHETIC-SECRET' } } });
        $('bridge-check').click(); await until(() => $('bridge-check').getAttribute('aria-busy') === 'false');
        assert(!$('login') && !ui.element.textContent.includes('SYNTHETIC-SECRET') && !ui.element.textContent.includes('登录状态未知'), 'Login data reached UI');
      });

      await test('选择文件夹在非默认目录且组件支持时附带 initialDir', async () => {
        const first = await setup({ hosts: 'current' });
        first.$('pick-folder').click();
        await until(() => first.fixture.count('sg-pick-folder') === 1);
        assert(!('initialDir' in first.fixture.last('sg-pick-folder')), 'Default dir sent initialDir');
        await until(() => first.$('dir').value === w.VeyloFixture.LONG_PATH && !first.$('pick-folder').disabled);
        first.$('pick-folder').click();
        await until(() => first.fixture.count('sg-pick-folder') === 2);
        assert(first.fixture.last('sg-pick-folder').initialDir === w.VeyloFixture.LONG_PATH && first.fixture.last('sg-pick-folder').host === 'bili', 'initialDir missing');
        const legacy = await setup({ settings: { dir: w.VeyloFixture.LONG_PATH } });
        legacy.$('pick-folder').click();
        await until(() => legacy.fixture.count('sg-pick-folder') === 1);
        assert(!('initialDir' in legacy.fixture.last('sg-pick-folder')), 'Legacy host received initialDir');
        await until(() => !legacy.$('pick-folder').disabled);
      });

      await test('实用选项保存与投递，切换原音轨禁用封面章节，B站不显示', async () => {
        const { fixture, $, ui } = await setup({ hosts: 'current', jobs: [] }, { url: w.VeyloFixture.URLS.youtube });
        await capsReady(fixture);
        for (const name of ['embed-thumbnail', 'split-chapters', 'download-archive']) { $(name).checked = true; $(name).dispatchEvent(new w.Event('change', { bubbles: true })); }
        change($('limit-rate'), '1M'); change($('concurrent-fragments'), '4');
        await until(() => fixture.store.settings.concurrentFragments === '4');
        assert(!$('practical-options').hidden && !$('embed-thumbnail').disabled, 'Supported practical options unavailable');
        $('send').click(); await until(() => fixture.count('sg-deliver') === 1);
        const sent = fixture.last('sg-deliver');
        assert(sent.embedThumbnail && sent.splitChapters && sent.downloadArchive && sent.limitRate === '1M' && sent.concurrentFragments === '4', 'Practical settings not sent');
        ui.showTab('download'); change($('media'), 'audio-original');
        assert($('embed-thumbnail').disabled && $('split-chapters').disabled && !$('download-archive').disabled, 'Original mode incompatibilities wrong');
        ui.setContext({ url: w.VeyloFixture.URLS.bili });
        assert($('practical-options').hidden, 'Bili exposed yt-dlp-only options');
      });
      await test('任务记录搜索与平台状态筛选可组合，打开文件使用记录序号', async () => {
        const { fixture, $, ui } = await setup({ hosts: 'current' });
        ui.showTab('history'); await until(() => $('jobs').children.length === 4);
        await until(() => [...$('jobs').querySelectorAll('button[data-action="file"]')].some(button => !button.disabled));
        const open = $('jobs').querySelector('button[data-action="file"]'); open.click();
        await until(() => fixture.count('sg-job-open-file') === 1);
        assert(fixture.last('sg-job-open-file').fileIndex === 0, 'File index was not passed');
        $('history-search').value = 'sunday'; $('history-search').dispatchEvent(new w.Event('input', { bubbles: true }));
        assert($('jobs').children.length === 1, 'Title search incorrect');
        change($('history-platform'), 'bili'); assert($('jobs').children.length === 0 && $('empty-title').textContent === '没有匹配的任务', 'Combined filter empty state wrong');
        $('history-search').value = ''; $('history-search').dispatchEvent(new w.Event('input', { bubbles: true }));
        change($('history-platform'), 'all'); change($('history-state'), 'active');
        assert($('jobs').children.length === 2, 'Active filter wrong');
        assert(fixture.store.jobs.length === 4, 'Filtering changed saved history');
      });
      await test('键盘标签页方向键与主题切换可用', async () => {
        const { $, ui } = await setup();
        $('tab-download').dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
        assert($('tab-history').getAttribute('aria-selected') === 'true' && !$('history-panel').hidden, 'Arrow-key tab switching failed');
        ui.showTab('download');
        change($('theme'), 'dark');
        assert(ui.element.dataset.theme === 'dark', 'Theme change was not immediate');
        await until(() => current.fixture.store.settings.theme === 'dark');
        await wait(5);
      });
    } finally {
      cleanup();
      for (const id of timers.keys()) originalClearInterval.call(w, id);
      timers.clear();
      w.setInterval = originalSetInterval;
      w.clearInterval = originalClearInterval;
      w.chrome = originalChrome;
      preview?.setActive(true);
    }
    return { passed, failed };
  }
  global.VeyloDOMTests = Object.freeze({ run });
})(globalThis);
