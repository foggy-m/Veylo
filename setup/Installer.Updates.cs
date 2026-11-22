using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace VeyloSetup
{
    internal sealed partial class Installer
    {
        private sealed class Vendor
        {
            public string Directory;
            public Dependency Release;
        }

        private string VendorDirectory { get { return Path.Combine(Root, "vendor"); } }
        private string EngineSelection { get { return Path.Combine(App, "native", "yt-dlp-version.txt"); } }
        private string TrackFile { get { return Path.Combine(App, "check-updates-on-open.txt"); } }

        public bool TrackUpdates
        {
            get
            {
                Files.PlainPath(TrackFile);
                return File.Exists(TrackFile) && File.ReadAllText(TrackFile) == "yes";
            }
            set { Files.WriteAtomic(TrackFile, value ? "yes" : "no"); }
        }

        private string VendorPaths(bool bili)
        {
            return Path.Combine(App, bili ? "bilibili\\native\\bili_paths.json" : "native\\fluent_paths.json");
        }

        private Vendor VendorFor(bool bili, Dependency dependency)
        {
            return new Vendor
            {
                Directory = Files.Inside(VendorDirectory, (bili ? "Bili23-" : "FluentYTDL-") + dependency.Version),
                Release = dependency
            };
        }

        private Vendor InstalledVendor(bool bili, Dependency baseline)
        {
            var fallback = VendorFor(bili, baseline);
            string path = VendorPaths(bili);
            Files.PlainPath(path);
            if (!File.Exists(path)) return fallback;
            try
            {
                var saved = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
                object version, sha;
                if (saved == null || !saved.TryGetValue("version", out version) || !saved.TryGetValue("sha256", out sha) ||
                    !Regex.IsMatch(version as string ?? "", @"\A\d{1,4}\.\d{1,4}\.\d{1,4}\z") ||
                    !Regex.IsMatch(sha as string ?? "", @"\A[a-f0-9]{64}\z") ||
                    !ReleaseCatalog.Newer((string)version, baseline.Version)) return fallback;
                var selected = VendorFor(bili, new Dependency { Version = (string)version, Sha256 = (string)sha });
                if (HasVendor(selected.Directory, selected.Release, bili ? BiliFiles : FluentFiles)) return selected;
            }
            catch (ArgumentException) { }
            catch (InvalidOperationException) { }
            catch (IOException) { }
            return fallback;
        }

        private string FluentProfile
        {
            get { return VendorFor(false, ReadDependencies()["fluent"]).Directory; }
        }

        private void WriteVendorPaths(Vendor vendor, bool bili, string output)
        {
            var values = new Dictionary<string, string>
            {
                { bili ? "bili_root" : "fluent_root", vendor.Directory },
                { "download_dir", output }, { "version", vendor.Release.Version }, { "sha256", vendor.Release.Sha256 }
            };
            if (!bili) values["fluent_data_root"] = FluentProfile;
            Files.WriteAtomic(VendorPaths(bili), json.Serialize(values));
        }

        private void EnsureVendor(Vendor vendor, bool bili, Dependency extractor, Download download,
            Action<string, int> progress, CancellationToken cancel)
        {
            string[] required = bili ? BiliFiles : FluentFiles;
            if (HasVendor(vendor.Directory, vendor.Release, required)) return;
            string cache = Files.MakeDirectory(Path.Combine(Root, "download-cache"));
            Files.MakeDirectory(VendorDirectory);
            string tool = bili ? null : download.Fetch(extractor, cache, cancel);
            string archive = download.Fetch(vendor.Release, cache, cancel);
            progress(bili ? "正在解压哔哩哔哩下载组件…" : "正在解压 YouTube / X 下载组件…", -1);
            UnpackVendor(vendor.Directory, vendor.Release, required, archive, tool, cancel);
        }

        public void OpenVendor(bool bili)
        {
            var vendor = InstalledVendor(bili, ReadDependencies()[bili ? "bili" : "fluent"]);
            string executable = Files.Inside(vendor.Directory, bili ? "Bili23.exe" : "FluentYTDL.exe");
            Files.PlainPath(executable);
            var launch = new ProcessStartInfo(executable) { WorkingDirectory = vendor.Directory, UseShellExecute = false };
            if (!bili) launch.EnvironmentVariables["FLUENTYTDL_DATA_DIR_OVERRIDE"] = FluentProfile;
            Process.Start(launch);
        }

        private void SaveManager()
        {
            string running = Assembly.GetExecutingAssembly().Location;
            string installed = Path.Combine(App, "Veylo-Setup.exe");
            Files.PlainPath(installed);
            if (!string.Equals(running, installed, StringComparison.OrdinalIgnoreCase))
            {
                bool matches = false;
                if (File.Exists(installed))
                {
                    using (var source = File.OpenRead(running))
                    using (var existing = File.OpenRead(installed))
                        matches = Download.Hash(source) == Download.Hash(existing);
                }
                if (!matches) Files.CopyAtomic(running, installed);
            }

            try
            {
                string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs, Environment.SpecialFolderOption.Create);
                if (programs.Length == 0)
                    programs = Files.Inside(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Microsoft/Windows/Start Menu/Programs");
                Shortcut.Save(Path.Combine(Files.MakeDirectory(programs), "Veylo 安装与更新.lnk"), installed, App);
            }
            catch (COMException) { }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        private static ReleaseCatalog.Release ReadLatest(Download download, string repository, CancellationToken cancel)
        {
            try { return ReleaseCatalog.Latest(download, repository, cancel); }
            catch (Exception error)
            {
                if (!IsUpdateError(error) && !(error is OperationCanceledException)) throw;
                return null;
            }
        }

        private static bool IsUpdateError(Exception error)
        {
            return error is IOException || error is HttpRequestException || error is InvalidOperationException ||
                error is ArgumentException || error is System.ComponentModel.Win32Exception || error is UnauthorizedAccessException;
        }

        public string[] UpdateComponents(Action<string, int> progress, CancellationToken cancel)
        {
            using (LockInstall())
            using (var download = new Download(progress))
            {
                ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
                var installed = ReadDependencies();
                if (!File.Exists(Path.Combine(Extension, "manifest.json"))) throw new IOException("请先完成 Veylo 安装。");
                var repositories = new[] { ReleaseCatalog.FluentRepo, ReleaseCatalog.BiliRepo, ReleaseCatalog.EngineRepo, ReleaseCatalog.VeyloRepo };
                progress("正在查询官方版本和 Veylo 兼容清单…", -1);
                var requests = repositories.ToDictionary(repo => repo, repo => Task.Run(() => ReadLatest(download, repo, cancel)));
                Task.WaitAll(requests.Values.ToArray());
                cancel.ThrowIfCancellationRequested();
                var catalog = ReadVerifiedComponents(download, requests[ReleaseCatalog.VeyloRepo].Result, cancel);
                var messages = new List<string>
                {
                    UpdateVendor(false, installed["fluent"], installed["extractor"], catalog, requests[ReleaseCatalog.FluentRepo].Result, download, progress, cancel),
                    UpdateVendor(true, installed["bili"], installed["extractor"], catalog, requests[ReleaseCatalog.BiliRepo].Result, download, progress, cancel)
                };
                messages.Add(UpdateEngine(InstalledVendor(false, installed["fluent"]), requests[ReleaseCatalog.EngineRepo].Result,
                    download, progress, cancel));
                return messages.ToArray();
            }
        }

        private ReleaseCatalog.VerifiedComponents ReadVerifiedComponents(Download download, ReleaseCatalog.Release release, CancellationToken cancel)
        {
            if (release == null) return null;
            try
            {
                var manifest = ReleaseCatalog.AssetFor(release, ReleaseCatalog.VeyloRepo, ReleaseCatalog.CatalogFile, "Veylo 兼容清单", 16 * 1024);
                if (manifest == null) return null;
                string path = download.Fetch(manifest, Files.MakeDirectory(Path.Combine(Root, "download-cache")), cancel);
                var catalog = json.Deserialize<ReleaseCatalog.VerifiedComponents>(File.ReadAllText(path, Encoding.UTF8));
                return catalog != null && catalog.Schema == 1 ? catalog : null;
            }
            catch (Exception error)
            {
                if (!IsUpdateError(error)) throw;
                cancel.ThrowIfCancellationRequested();
                return null;
            }
        }

        private string UpdateVendor(bool bili, Dependency baseline, Dependency extractor, ReleaseCatalog.VerifiedComponents catalog,
            ReleaseCatalog.Release upstream, Download download, Action<string, int> progress, CancellationToken cancel)
        {
            string name = bili ? "Bili23" : "FluentYTDL";
            string repo = bili ? ReleaseCatalog.BiliRepo : ReleaseCatalog.FluentRepo;
            var active = InstalledVendor(bili, baseline);
            var host = bili ? PrepareHost(BiliHost, "bilibili/native", "BiliBridge.exe", "1.5.3")
                : PrepareHost(FluentHost, "native", "ShiguangBridge.exe", "1.7.3");
            var candidate = catalog == null ? null : (bili ? catalog.Bili : catalog.Fluent);
            if (candidate != null && host.Version == (bili ? catalog.BiliBridge : catalog.YouTubeBridge)
                && ReleaseCatalog.ValidVendor(candidate, repo) && ReleaseCatalog.Newer(candidate.Version, active.Release.Version))
            {
                try
                {
                    var staged = VendorFor(bili, candidate);
                    EnsureVendor(staged, bili, extractor, download, progress, cancel);
                    TestVendor(staged, bili, cancel);
                    if (!bili) CopyFluentLogin(active.Directory, staged.Directory);
                    string output = Path.Combine(Root, "Downloads");
                    WriteVendorPaths(staged, bili, output);
                    try { CheckBridge(host, cancel); }
                    catch { WriteVendorPaths(active, bili, output); throw; }
                    active = staged;
                }
                catch (Exception error)
                {
                    if (!IsUpdateError(error)) throw;
                    cancel.ThrowIfCancellationRequested();
                    return name + " " + candidate.Version + " 更新未通过检查，继续使用 " + active.Release.Version + "。";
                }
            }

            if (upstream == null) return name + " " + active.Release.Version + " · 暂时无法查询官方版本。";
            if (ReleaseCatalog.Newer(upstream.Version, active.Release.Version))
                return name + " " + active.Release.Version + " · 官方 " + upstream.Version + " 等待 Veylo 兼容验证。";
            return name + " " + active.Release.Version + " · 已是官方最新版。";
        }

        private void TestVendor(Vendor vendor, bool bili, CancellationToken cancel)
        {
            if (bili)
            {
                RequireVersion(Files.Inside(vendor.Directory, "bundle/ffmpeg.exe"), "-version", "ffmpeg version", cancel);
                string probe = Path.Combine(App, "bili-probe.py");
                string driver = Path.Combine(App, "bilibili", "driver", "bd_driver.py");
                string python = Files.Inside(vendor.Directory, "runtime/python.exe");
                string profile = Files.MakeDirectory(Path.Combine(App, ".stage-" + Guid.NewGuid().ToString("N")));
                try
                {
                    var arguments = new[] { probe, driver, vendor.Directory, profile }.Select(Files.Quote);
                    if (!RunTool(python, "-B " + string.Join(" ", arguments), cancel).Contains("VEYLO_BILI_COMPATIBLE"))
                        throw new IOException("Bili23 的播放器接口未通过验证。");
                }
                finally { Files.RemoveTemporary(App, profile); }
                return;
            }
            TestFluentTools(vendor.Directory, cancel);
        }

        private static void TestFluentTools(string root, CancellationToken cancel)
        {
            RequireVersion(Files.Inside(root, "bin/ffmpeg/ffmpeg.exe"), "-version", "ffmpeg version", cancel);
            RequireVersion(Files.Inside(root, "bin/ffmpeg/ffprobe.exe"), "-version", "ffprobe version", cancel);
            string deno = new[] { "bin/js/deno.exe", "bin/deno/deno.exe" }.Select(file => Files.Inside(root, file)).FirstOrDefault(File.Exists);
            if (deno == null) throw new IOException("Deno 未通过兼容检查。");
            RequireVersion(deno, "--version", "deno ", cancel);
        }

        private static void RequireVersion(string executable, string arguments, string prefix, CancellationToken cancel)
        {
            if (!RunTool(executable, arguments, cancel).StartsWith(prefix, StringComparison.Ordinal))
                throw new IOException("下载组件版本输出不符合预期。");
        }

        private static string RunTool(string executable, string arguments, CancellationToken cancel)
        {
            Files.PlainPath(executable);
            var start = new ProcessStartInfo(executable, arguments)
            {
                UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Path.GetDirectoryName(executable),
                RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8
            };
            using (var process = Process.Start(start))
            {
                var output = process.StandardOutput.ReadToEndAsync();
                var errors = process.StandardError.ReadToEndAsync();
                Files.WaitFor(process, TimeSpan.FromSeconds(20), cancel);
                errors.GetAwaiter().GetResult();
                if (process.ExitCode != 0) throw new IOException("下载组件未通过启动检查。");
                return output.GetAwaiter().GetResult().Trim();
            }
        }

        private static void CopyFluentLogin(string from, string to)
        {
            foreach (string name in new[] { "cookies_youtube.txt", "cookies_twitter.txt", "cookies_youtube.txt.meta", "cookies_twitter.txt.meta" })
            {
                string source = Files.Inside(from, "bin/" + name), target = Files.Inside(to, "bin/" + name);
                Files.PlainPath(source);
                Files.PlainPath(target);
                if (File.Exists(source) && !File.Exists(target))
                {
                    Files.MakeDirectory(Path.GetDirectoryName(target));
                    Files.CopyAtomic(source, target);
                }
            }
        }

        private string UpdateEngine(Vendor fluent, ReleaseCatalog.Release upstream, Download download,
            Action<string, int> progress, CancellationToken cancel)
        {
            var bundled = Files.Inside(fluent.Directory, "bin/yt-dlp/yt-dlp.exe");
            string current = "";
            try
            {
                Files.PlainPath(EngineSelection);
                string selected = File.Exists(EngineSelection) ? File.ReadAllText(EngineSelection).Trim() : "";
                string active = ReleaseCatalog.EngineVersion(selected) ? EnginePath(selected) : bundled;
                current = RunTool(File.Exists(active) ? active : bundled, "--version", cancel);
                if (upstream == null) return "yt-dlp " + current + " · 暂时无法查询官方版本。";
                if (!ReleaseCatalog.Newer(upstream.Version, current)) return "yt-dlp " + current + " · 已是官方最新版。";
                var candidate = ReleaseCatalog.AssetFor(upstream, ReleaseCatalog.EngineRepo, "yt-dlp.exe", "yt-dlp " + upstream.Version, 128 * 1024 * 1024);
                if (candidate == null) return "yt-dlp " + upstream.Version + " 缺少可校验的 Windows 安装文件，保留 " + current + "。";
                string engine = download.Fetch(candidate, Files.MakeDirectory(Path.GetDirectoryName(EnginePath(candidate.Version))), cancel);
                if (RunTool(engine, "--version", cancel) != candidate.Version ||
                    !new[] { "--js-runtimes", "--ffmpeg-location", "--print", "--merge-output-format", "--remux-video", "--concurrent-fragments" }
                        .All(RunTool(engine, "--help", cancel).Contains)) throw new IOException("yt-dlp 命令接口不兼容。");
                TestFluentTools(fluent.Directory, cancel);
                Files.WriteAtomic(EngineSelection, candidate.Version);
                return "yt-dlp 已更新至 " + candidate.Version + "。";
            }
            catch (Exception error)
            {
                if (!IsUpdateError(error)) throw;
                cancel.ThrowIfCancellationRequested();
                return "yt-dlp 更新未通过检查，继续使用" + (current.Length == 0 ? "当前内核。" : " " + current + "。");
            }
        }

        private string EnginePath(string version)
        {
            return Files.Inside(VendorDirectory, "yt-dlp-" + version + "/yt-dlp.exe");
        }

        public void RestoreBundledEngine()
        {
            using (LockInstall())
            {
                TrackUpdates = false;
                Files.WriteAtomic(EngineSelection, "");
            }
        }
    }
}
