const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { execFile } = require('node:child_process');
const { chromium } = require(process.env.VEYLO_PLAYWRIGHT);

const app = path.join(process.env.LOCALAPPDATA, 'Veylo', 'app-2.4.5');
const extension = path.join(app, 'extension');
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'veylo-clean-chromium-'));
const extensionId = 'ikaahmfclfkamdgikchmjnoalpklkddk';
const tests = [
  { platform: 'youtube', url: 'https://www.youtube.com/watch?v=DTi8wZ1a1TA', quality: '480' },
  // NASA's DART spacecraft footage is hosted directly on X.
  { platform: 'x', url: 'https://x.com/NASA/status/1574539270987173903', quality: '480' },
  // The Daily Dweebs, a one-minute Blender open movie in this anthology.
  { platform: 'bili', url: 'https://www.bilibili.com/video/BV1iW4113786/?p=4', quality: '32' },
];

async function send(page, message) {
  return page.evaluate(message => chrome.runtime.sendMessage(message), message);
}

async function smokeDownload(page, test) {
  const { platform, url, quality } = test;
  const task = await send(page, { type: 'sg-deliver', mode: 'direct', url, quality });
  assert(task?.ok === true, `${platform} could not start from a clean install: ${task?.error}`);
  assert.match(task.jobId, /^[a-f0-9]{32}$/);
  assert.equal(path.resolve(task.downloadDir), path.resolve(process.env.LOCALAPPDATA, 'Veylo', 'Downloads'));
  if (platform === 'bili') assert.equal(task.login, false);
  const deadline = Date.now() + 90000;
  try {
    while (Date.now() < deadline) {
      const status = await send(page, { type: 'sg-job-status', platform, jobId: task.jobId });
      assert(status?.ok === true, `${platform} cannot read the accepted task: ${status?.error}`);
      const job = status.job;
      if (job.state === 'completed') {
        assert(job.files.length > 0 && job.files.every(file => fs.statSync(file).size > 0), 'No valid media was saved');
        return { platform, accepted: true, liveDownload: 'completed', files: job.files.length };
      }
      if (job.state === 'failed') return { platform, accepted: true, liveDownload: 'failed', message: job.error };
      assert.notEqual(job.state, 'canceled');
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    return { platform, accepted: true, liveDownload: 'timed out' };
  } finally {
    await send(page, { type: 'sg-job-cancel', platform, jobId: task.jobId }).catch(() => {});
  }
}

async function serviceAccess(test) {
  const url = test.platform === 'bili' ? 'https://api.bilibili.com/x/web-interface/view?bvid=BV1iW4113786' : test.url;
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(8000) });
    const result = { httpStatus: response.status };
    if (test.platform === 'bili' && response.ok) {
      const value = await response.json();
      if (Number.isSafeInteger(value?.code)) result.platformCode = value.code;
    } else {
      await response.body?.cancel();
    }
    return result;
  } catch (error) {
    const code = error?.cause?.code ?? error.name;
    return { networkError: typeof code === 'string' && /^[\w-]{1,60}$/.test(code) ? code : 'request_failed' };
  }
}

function diagnoseYouTube(url) {
  const bin = path.join(process.env.LOCALAPPDATA, 'Veylo', 'vendor', 'FluentYTDL-3.7.6', 'bin');
  const deno = [path.join(bin, 'js', 'deno.exe'), path.join(bin, 'deno', 'deno.exe')].find(file => fs.existsSync(file));
  if (!deno) return Promise.resolve('runtime-missing');
  const engine = path.join(bin, 'yt-dlp', 'yt-dlp.exe');
  return new Promise(resolve => {
    execFile(engine, ['--ignore-config', '--no-cache-dir', '--no-playlist', '--simulate', '--proxy', '',
      '--socket-timeout', '9', '--extractor-retries', '0', '--js-runtimes', `deno:${deno}`, '--', url],
    { timeout: 25000, windowsHide: true }, (error, _stdout, stderr) => {
      if (!error) return resolve('metadata-accessible');
      const statuses = [[/sign in|confirm you.re not a bot|login required/i, 'sign-in-or-bot-check'],
        [/HTTP Error 429/i, 'http-429'], [/HTTP Error 403/i, 'http-403'],
        [/HTTP Error 5\d\d/i, 'http-5xx'], [/no video formats|requested format is not available/i, 'formats-unavailable']];
      return resolve(statuses.find(([pattern]) => pattern.test(stderr))?.[1] ?? (error.killed ? 'timeout' : 'extraction-failed'));
    });
  });
}

async function main() {
  const context = await chromium.launchPersistentContext(profile, {
    channel: 'chromium', headless: true, viewport: { width: 550, height: 760 },
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  try {
    const page = await context.newPage();
    await page.goto(`chrome-extension://${extensionId}/popup.html`);
    await page.locator('#veylo-root button').first().waitFor({ state: 'visible' });
    const bridges = {};
    for (const [host, version] of [['youtube', '1.7.3'], ['bili', '1.5.3']]) {
      const result = await send(page, { type: 'sg-bridge-ping', host });
      assert.equal(result?.engineAvailable, true, `${host}: ${result?.error || 'bridge engine missing'}`);
      assert.equal(result.version, version);
      bridges[host] = version;
    }
    await page.screenshot({ path: 'dist/clean-browser-popup.png' });
    console.log('PASS actual Chromium extension and both registered native bridges');
    const live = await Promise.all(tests.map(test => smokeDownload(page, test)));
    await Promise.all(live.map(async (result, index) => {
      if (result.liveDownload === 'completed') return;
      result.serviceAccess = await serviceAccess(tests[index]);
      if (result.platform === 'youtube') result.serviceAccess.engineProbe = await diagnoseYouTube(tests[index].url);
    }));
    const reportPath = 'dist/clean-install-report.json';
    const report = { ...JSON.parse(fs.readFileSync(reportPath)), actualChromiumNativeMessaging: bridges, liveGuestTasks: live };
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    for (const result of live) console.log(JSON.stringify(result));
  } finally {
    await context.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
