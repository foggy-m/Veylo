using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32;

namespace VeyloSetup
{
    internal sealed partial class Installer
    {
        public const string Version = "2.4.8";
        // Setup-only updates reuse the directory already loaded by the browser.
        private const string AppVersion = "2.4.5";
        internal const string AppDirectory = "app-" + AppVersion;
        public const string FluentHost = "com.veylo.youtube_x";
        public const string BiliHost = "com.veylo.bilibili";
        private const string Origin = "chrome-extension://ikaahmfclfkamdgikchmjnoalpklkddk/";

        private static readonly string[] BrowserKeys = {
            @"Software\Google\Chrome\NativeMessagingHosts", @"Software\Microsoft\Edge\NativeMessagingHosts",
            @"Software\Chromium\NativeMessagingHosts", @"Software\Tabbit Browser\Tabbit Browser\NativeMessagingHosts",
            @"Software\Tabbit\NativeMessagingHosts"
        };
        private static readonly string[] FluentFiles = {
            "FluentYTDL.exe", "bin/yt-dlp/yt-dlp.exe", "bin/ffmpeg/ffmpeg.exe", "bin/ffmpeg/ffprobe.exe"
        };
        private static readonly string[] BiliFiles = {
            "Bili23.exe", "runtime/python.exe", "bundle/ffmpeg.exe", "script/util/common/config.py"
        };

        public readonly string Root;
        public string App { get { return Path.Combine(Root, AppDirectory); } }
        public string Extension { get { return Path.Combine(App, "extension"); } }

        private readonly JavaScriptSerializer json = new JavaScriptSerializer();

        public void Install(Action<string, int> progress, CancellationToken cancel)
        {
            using (LockInstall())
            {
                var youtube = PrepareHost(FluentHost, "native", "ShiguangBridge.exe", "1.7.3");
                var bili = PrepareHost(BiliHost, "bilibili/native", "BiliBridge.exe", "1.5.3");
                var registrations = CheckRegistrations(new[] { youtube, bili });
                PrepareInstallRoot();
                progress("正在安装 Veylo 扩展和本机桥接…", -1);
                Files.MakeDirectory(App);
                using (var payload = Resource("veylo.payload.zip")) Files.ExpandZip(payload, App, true, cancel);
                var dependencies = ReadDependencies();

                var fluent = InstalledVendor(false, dependencies["fluent"]);
                var bili23 = InstalledVendor(true, dependencies["bili"]);
                string cache = Files.MakeDirectory(Path.Combine(Root, "download-cache"));
                ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
                using (var download = new Download(progress))
                {
                    EnsureVendor(fluent, false, dependencies["extractor"], download, progress, cancel);
                    EnsureVendor(bili23, true, dependencies["extractor"], download, progress, cancel);
                }

                progress("正在连接浏览器与下载组件…", -1);
                string output = Files.MakeDirectory(Path.Combine(Root, "Downloads"));
                WriteVendorPaths(fluent, false, output);
                WriteVendorPaths(bili23, true, output);
                CheckBridge(youtube, cancel);
                CheckBridge(bili, cancel);
                cancel.ThrowIfCancellationRequested();
                string manager = InstallManager();
                Register(registrations);
                SaveManagerShortcut(manager);
                foreach (Dependency dependency in dependencies.Values)
                {
                    string downloaded = Files.Inside(cache, dependency.Filename);
                    // A failed cache cleanup should not invalidate a working installation.
                    try { Files.PlainPath(downloaded); if (File.Exists(downloaded)) File.Delete(downloaded); }
                    catch (IOException) { }
                    catch (UnauthorizedAccessException) { }
                }
                progress("安装完成", 100);
            }
        }

        private FileStream LockInstall()
        {
            if (!Environment.Is64BitOperatingSystem || Environment.OSVersion.Version.Major < 10)
                throw new InvalidOperationException("需要 Windows 10 或 11 的 64 位版本。");
            // All destinations share the current user's browser registrations.
            string path = Path.Combine(Files.MakeDirectory(DefaultRoot), ".install.lock");
            Files.PlainPath(path);
            try { return new FileStream(path, FileMode.OpenOrCreate, FileAccess.Write, FileShare.None); }
            catch (IOException error) { throw new InvalidOperationException("另一个 Veylo 安装程序正在运行。请关闭后重试。", error); }
        }

        internal static Stream Resource(string name)
        {
            Stream content = Assembly.GetExecutingAssembly().GetManifestResourceStream(name);
            if (content == null) throw new InvalidDataException("安装程序不完整，请重新下载安装文件。");
            return content;
        }

        private Dictionary<string, Dependency> ReadDependencies()
        {
            using (var reader = new StreamReader(Resource("veylo.dependencies.json"), Encoding.UTF8))
                return json.Deserialize<Dictionary<string, Dependency>>(reader.ReadToEnd());
        }

        private static bool HasVendor(string directory, Dependency dependency, string[] required)
        {
            Files.PlainPath(directory);
            if (!Directory.Exists(directory)) return false;
            string marker = Path.Combine(directory, ".veylo-release");
            Files.PlainPath(marker);
            if (!File.Exists(marker) || File.ReadAllText(marker) != dependency.Sha256)
                throw new IOException("依赖目录来自其他安装。请先备份该目录后重新运行安装程序。");
            return HasFiles(directory, required);
        }

        private static bool HasFiles(string directory, string[] required)
        {
            foreach (string relative in required)
            {
                string file = Files.Inside(directory, relative);
                Files.PlainPath(file);
                if (!File.Exists(file)) return false;
            }
            return true;
        }

        private static void UnpackVendor(string destination, Dependency dependency, string[] required,
            string archive, string extractor, CancellationToken cancel)
        {
            string parent = Path.GetDirectoryName(destination);
            string temporary = Files.MakeDirectory(Path.Combine(parent, ".stage-" + Guid.NewGuid().ToString("N")));
            try
            {
                if (extractor == null)
                {
                    using (var input = File.OpenRead(archive)) Files.ExpandZip(input, temporary, false, cancel);
                }
                else Files.Expand7z(extractor, archive, temporary, cancel);
                Files.CheckTree(temporary);
                var matches = Directory.EnumerateFiles(temporary, required[0], SearchOption.AllDirectories).ToArray();
                if (matches.Length != 1) throw new IOException("官方依赖的目录结构有变化，无法完成安装。");
                string source = Path.GetDirectoryName(matches[0]);
                if (!HasFiles(source, required)) throw new IOException("官方依赖缺少运行所需文件。");
                Files.WriteAtomic(Path.Combine(source, ".veylo-release"), dependency.Sha256);
                string backup = null;
                if (Directory.Exists(destination))
                {
                    backup = destination + ".backup-" + Guid.NewGuid().ToString("N");
                    Files.PlainPath(destination);
                    Files.PlainPath(backup);
                    Directory.Move(destination, backup);
                }
                try { Directory.Move(source, destination); }
                catch
                {
                    if (backup != null && !Directory.Exists(destination)) Directory.Move(backup, destination);
                    throw;
                }
            }
            finally { Files.RemoveTemporary(parent, temporary); }
        }

        private sealed class Host
        {
            public string Name, Directory, Exe, Manifest, Version;
        }

        private sealed class Registration
        {
            public Host Host;
            public string Key, Previous;
        }

        private Host PrepareHost(string name, string directory, string executable, string version)
        {
            string home = Files.Inside(App, directory);
            return new Host { Name = name, Directory = home, Exe = Path.Combine(home, executable),
                              Manifest = Path.Combine(home, name + ".json"), Version = version };
        }

        private List<Registration> CheckRegistrations(IEnumerable<Host> hosts)
        {
            var values = new List<Registration>();
            string activeRoot = PreferredRoot();
            foreach (Host host in hosts)
            {
                foreach (string browser in BrowserKeys)
                {
                    string key = browser + "\\" + host.Name;
                    using (var existing = Registry.CurrentUser.OpenSubKey(key))
                    {
                        string previous = null;
                        if (existing != null)
                        {
                            object value = existing.GetValue("", null, RegistryValueOptions.DoNotExpandEnvironmentNames);
                            previous = value as string;
                            if (value != null && (string.IsNullOrEmpty(previous) || existing.GetValueKind("") != RegistryValueKind.String ||
                                                  !OwnedRegistration(host, previous, activeRoot)))
                                throw new InvalidOperationException("已有指向其他应用的浏览器桥接注册。请保留原注册并检查 Veylo 安装位置。");
                        }
                        values.Add(new Registration { Host = host, Key = key, Previous = previous });
                    }
                }
            }
            return values;
        }

        private bool OwnedRegistration(Host host, string previous, string activeRoot)
        {
            if (!Path.IsPathRooted(previous)) return false;
            string full = Path.GetFullPath(previous);
            if (string.Equals(full, host.Manifest, StringComparison.OrdinalIgnoreCase)) return true;
            string suffix = host.Manifest.Substring(App.Length);
            if (!full.EndsWith(suffix, StringComparison.OrdinalIgnoreCase)) return false;
            string app = full.Substring(0, full.Length - suffix.Length);
            string previousRoot = Path.GetDirectoryName(app);
            if (previousRoot == null || !Regex.IsMatch(Path.GetFileName(app), @"\Aapp-\d+\.\d+\.\d+\z")) return false;
            Files.PlainPath(full);
            return string.Equals(previousRoot, activeRoot, StringComparison.OrdinalIgnoreCase) ||
                   string.Equals(previousRoot, DefaultRoot, StringComparison.OrdinalIgnoreCase) ||
                   (File.Exists(full) && HasInstallMarker(previousRoot));
        }

        private void CheckBridge(Host host, CancellationToken cancel)
        {
            Files.PlainPath(host.Exe);
            var start = new ProcessStartInfo(host.Exe, Origin)
            {
                WorkingDirectory = host.Directory, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true
            };
            using (var process = Process.Start(start))
            using (var output = new MemoryStream())
            {
                var read = process.StandardOutput.BaseStream.CopyToAsync(output);
                var drain = process.StandardError.ReadToEndAsync();
                var body = Encoding.UTF8.GetBytes("{\"action\":\"ping\"}");
                var input = process.StandardInput.BaseStream;
                input.Write(BitConverter.GetBytes(body.Length), 0, 4);
                input.Write(body, 0, body.Length);
                input.Flush();
                process.StandardInput.Close();
                Files.WaitFor(process, TimeSpan.FromSeconds(15), cancel);
                read.GetAwaiter().GetResult();
                drain.GetAwaiter().GetResult();
                byte[] result = output.ToArray();
                if (process.ExitCode != 0 || result.Length < 4 || result.Length > 65540 || BitConverter.ToInt32(result, 0) != result.Length - 4)
                    throw new IOException("本机下载组件未能启动。请确认 Windows 允许运行 Veylo，再点击重试。");
                var status = json.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(result, 4, result.Length - 4));
                if (!Equals(status["ok"], true) || !Equals(status["engineAvailable"], true) || !Equals(status["version"], host.Version))
                    throw new IOException("本机组件与下载器连接失败。请重新运行安装程序以检查依赖。");
            }
        }

        private void Register(List<Registration> registrations)
        {
            foreach (Host host in registrations.Select(item => item.Host).Distinct())
                Files.WriteAtomic(host.Manifest, json.Serialize(new { name = host.Name, description = "Veylo Download Bridge",
                                  type = "stdio", path = host.Exe, allowed_origins = new[] { Origin } }));
            var changed = new List<Registration>();
            try
            {
                foreach (Registration item in registrations)
                {
                    using (var registry = Registry.CurrentUser.CreateSubKey(item.Key))
                    {
                        var actual = registry.GetValue("", null, RegistryValueOptions.DoNotExpandEnvironmentNames);
                        if (!Equals(actual, item.Previous)) throw new InvalidOperationException("浏览器注册已改变。请重新运行安装程序。");
                        registry.SetValue("", item.Host.Manifest, RegistryValueKind.String);
                        changed.Add(item);
                    }
                }
                SaveActiveRoot();
            }
            catch
            {
                foreach (Registration item in changed)
                {
                    using (var registry = Registry.CurrentUser.OpenSubKey(item.Key, true))
                    {
                        if (registry == null || !Equals(registry.GetValue(""), item.Host.Manifest)) continue;
                        if (item.Previous == null) registry.DeleteValue("", false);
                        else registry.SetValue("", item.Previous, RegistryValueKind.String);
                    }
                }
                throw;
            }
        }
    }
}
