using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

[assembly: System.Reflection.AssemblyTitle("Veylo")]
[assembly: System.Reflection.AssemblyProduct("Veylo")]
[assembly: System.Reflection.AssemblyVersion("1.5.1.0")]
[assembly: System.Reflection.AssemblyFileVersion("1.5.1.0")]

internal static class BiliBridge
{
    const string Version = "1.5.1";
    const string CanceledError = "已取消下载。";
    const int MaxMessage = 65536;
    const string ExtensionOrigin = "chrome-extension://ikaahmfclfkamdgikchmjnoalpklkddk/";
    static readonly string Home = Path.GetDirectoryName(typeof(BiliBridge).Assembly.Location);
    static readonly string Jobs = Path.Combine(Home, "jobs");
    static readonly string DriverScript = Path.GetFullPath(Path.Combine(Home, "..", "driver", "bd_driver.py"));
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 1048576 };
    static readonly object StateLock = new object();
    static readonly SecurityIdentifier User = WindowsIdentity.GetCurrent().User;
    static readonly SecurityIdentifier SystemSid = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct StartupInfo
    {
        public int cb;
        public string reserved, desktop, title;
        public int x, y, xSize, ySize, xCountChars, yCountChars, fillAttribute, flags;
        public short showWindow, reservedSize;
        public IntPtr reservedData, standardInput, standardOutput, standardError;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct ProcessInfo { public IntPtr process, thread; public int processId, threadId; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CreateProcess(string application, StringBuilder commandLine, IntPtr processAttributes,
        IntPtr threadAttributes, bool inheritHandles, uint flags, IntPtr environment, string directory,
        ref StartupInfo startup, out ProcessInfo info);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);
    [DllImport("user32.dll", SetLastError = true)]
    static extern bool OpenClipboard(IntPtr owner);
    [DllImport("user32.dll", SetLastError = true)]
    static extern bool CloseClipboard();
    [DllImport("user32.dll", SetLastError = true)]
    static extern IntPtr SetClipboardData(uint format, IntPtr memory);
    [DllImport("user32.dll", SetLastError = true)]
    static extern bool EmptyClipboard();
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern int SHCreateItemFromParsingName([MarshalAs(UnmanagedType.LPWStr)] string path, IntPtr bindContext,
        ref Guid interfaceId, [MarshalAs(UnmanagedType.Interface)] out IShellItem item);

    static void StartWorker(string id)
    {
        string executable = typeof(BiliBridge).Assembly.Location;
        var startup = new StartupInfo { cb = Marshal.SizeOf(typeof(StartupInfo)) };
        ProcessInfo process;
        // Browser message pipes must not survive in the detached download process.
        if (!CreateProcess(executable, new StringBuilder(Quote(executable) + " --worker " + id),
            IntPtr.Zero, IntPtr.Zero, false, 0x08000000, IntPtr.Zero, Home, ref startup, out process))
            throw new InvalidOperationException("无法启动本机下载进程。");
        CloseHandle(process.thread); CloseHandle(process.process);
    }

    static int Main(string[] args)
    {
        try
        {
            ProtectDirectory(Home);
            Directory.CreateDirectory(Jobs);
            ProtectDirectory(Jobs);
            if (args.Length == 2 && args[0] == "--worker") { Worker(ValidateId(args[1])); return 0; }
            if (args.Length > 0 && args[0] != ExtensionOrigin) return 1;
            var input = Console.OpenStandardInput();
            var output = Console.OpenStandardOutput();
            while (true)
            {
                byte[] header = ReadExact(input, 4);
                if (header == null) break;
                int size = BitConverter.ToInt32(header, 0);
                if (size <= 0 || size > MaxMessage) return 1;
                byte[] body = ReadExact(input, size);
                if (body == null) return 1;
                object response;
                try { response = Dispatch(Json.Deserialize<Dictionary<string, object>>(new UTF8Encoding(false, true).GetString(body))); }
                catch (InvalidOperationException e) { response = new { ok = false, error = e.Message }; }
                catch { response = new { ok = false, error = "本机桥接无法处理请求，请检查 Bili23 路径和文件权限。" }; }
                byte[] encoded = Encoding.UTF8.GetBytes(Json.Serialize(response));
                output.Write(BitConverter.GetBytes(encoded.Length), 0, 4);
                output.Write(encoded, 0, encoded.Length);
                output.Flush();
            }
            return 0;
        }
        catch { return 1; }
    }

    static byte[] ReadExact(Stream stream, int size)
    {
        byte[] data = new byte[size]; int offset = 0;
        while (offset < size) { int n = stream.Read(data, offset, size - offset); if (n == 0) { if (offset != 0) throw new EndOfStreamException(); return null; } offset += n; }
        return data;
    }

    static string Text(Dictionary<string, object> obj, string key, string fallback = "")
    {
        object v; return obj != null && obj.TryGetValue(key, out v) && v is string ? (string)v : fallback;
    }

    static string EnumOption(Dictionary<string, object> msg, string key, string fallback, params string[] allowed)
    {
        object value;
        if (!msg.TryGetValue(key, out value)) return fallback;
        if (!(value is string) || !allowed.Contains((string)value))
            throw new InvalidOperationException("不支持的下载选项：" + key + "。");
        return (string)value;
    }
    static bool MetadataOption(Dictionary<string, object> msg)
    {
        object value;
        if (!msg.TryGetValue("metadata", out value)) return false;
        if (!(value is bool)) throw new InvalidOperationException("metadata 必须为布尔值。");
        return (bool)value;
    }
    static bool StoredMetadata(Dictionary<string, object> job)
    {
        object value; return job.TryGetValue("metadata", out value) && value is bool && (bool)value;
    }
    static string StoredAudioQuality(Dictionary<string, object> job)
    {
        string media = Text(job, "media", "video"), value = Text(job, "audioQuality", "auto");
        return (media == "audio-mp3" || media == "audio-m4a")
            && new[] { "auto", "320", "256", "192", "128" }.Contains(value) ? value : "auto";
    }
    static string[] MetadataWarnings(bool requested)
    {
        return requested ? new[] { "Bili23 本轮不支持 metadata，已忽略该选项。" } : new string[0];
    }
    static string StoredDanmaku(Dictionary<string, object> job)
    {
        string value = Text(job, "danmaku", "off");
        return new[] { "off", "ass", "xml" }.Contains(value) ? value : "off";
    }
    static string StoredBiliSubtitles(Dictionary<string, object> job)
    {
        string value = Text(job, "biliSubtitles", "off");
        return new[] { "off", "srt" }.Contains(value) ? value : "off";
    }
    static int StoredFileCount(Dictionary<string, object> job, int fallback)
    {
        object value;
        if (job.TryGetValue("fileCount", out value) && (value is int || value is long || value is decimal || value is double))
        {
            try
            {
                long count = Convert.ToInt64(value, CultureInfo.InvariantCulture);
                if (count >= 1 && count <= 100000) return (int)count;
            }
            catch { }
        }
        return Math.Max(0, Math.Min(100000, fallback));
    }
    static string[] SafeWarnings(Dictionary<string, object> job)
    {
        string[] allowed = {
            "Bili23 本轮不支持 metadata，已忽略该选项。", "弹幕下载失败，视频已保存。",
            "字幕下载失败，视频已保存。", "该视频不属于合集，已按全部分 P 下载。", "部分条目下载失败。"
        };
        var result = new List<string>(); object value;
        if (job.TryGetValue("warnings", out value) && value is System.Collections.IEnumerable && !(value is string))
            foreach (object item in (System.Collections.IEnumerable)value)
            {
                string warning = item as string;
                if (warning != null && allowed.Contains(warning) && !result.Contains(warning)) result.Add(warning);
            }
        foreach (string warning in MetadataWarnings(StoredMetadata(job)))
            if (!result.Contains(warning)) result.Add(warning);
        return result.ToArray();
    }
    static string BiliLoginStatus()
    {
        try
        {
#if BRIDGE_TEST
            string config = Environment.GetEnvironmentVariable("BILI_TEST_CONFIG");
#else
            string config = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "Bili23 Downloader", "config.json");
#endif
            if (string.IsNullOrEmpty(config) || !File.Exists(config)) return "unknown";
            var data = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(config, Encoding.UTF8));
            if (data == null) return "unknown";
            object section;
            if (!data.TryGetValue("Cookie", out section) || !(section is Dictionary<string, object>)) return "guest";
            object flag;
            if (!((Dictionary<string, object>)section).TryGetValue("is_login", out flag)) return "guest";
            if (flag is bool) return (bool)flag ? "signed-in" : "guest";
            if (flag is string) return string.Equals((string)flag, "true", StringComparison.OrdinalIgnoreCase) ? "signed-in" : "guest";
            return "guest";
        }
        catch { return "unknown"; }
    }

    static object Dispatch(Dictionary<string, object> msg)
    {
        string action = Text(msg, "action");
        if (action == "ping")
        {
            string root = BiliRoot();
            return new
            {
                ok = true,
                version = Version,
                mediaOptions = new[] { "video", "audio-original", "audio-mp3", "audio-m4a" },
                audioQualityOptions = new[] { "auto", "320", "256", "192", "128" },
                features = new[] { "cancel", "danmaku", "subtitles", "collection", "pick-folder-initial", "login-status", "open-file" },
                login = new { bilibili = BiliLoginStatus() },
                biliRoot = root,
                engineAvailable = File.Exists(Path.Combine(root ?? "", "Bili23.exe"))
                    && File.Exists(Path.Combine(root ?? "", "runtime", "python.exe"))
                    && File.Exists(Path.Combine(root ?? "", "bundle", "ffmpeg.exe"))
                    && File.Exists(DriverScript)
            };
        }
        if (action == "bili-download") return AcceptDownload(msg);
        if (action == "bili-handoff") return Handoff(msg);
        if (action == "pick-folder")
        {
            if (msg.Keys.Any(key => key != "action" && key != "initialDir")) throw new InvalidOperationException("pick-folder initialDir 请求字段无效。");
            string initialDir = ValidInitialDirectory(msg);
#if BRIDGE_TEST
            return new { ok = true, canceled = true, initialDir = initialDir };
#else
            return PickFolder(initialDir);
#endif
        }
        if (action == "cancel-job") return CancelJob(msg);
        if (action == "open-job-folder") return OpenJobFolder(msg);
        if (action == "open-job-file") return OpenJobFile(msg);
        if (action == "job-status")
        {
            string id = ValidateId(Text(msg, "jobId"));
            var job = ReadJob(id);
            if ((Text(job, "state") == "queued" || Text(job, "state") == "downloading") && File.Exists(CancelFile(id)))
            {
                if (!WorkerAlive(job)) FinalizeCanceled(job);
            }
            if ((Text(job, "state") == "queued" || Text(job, "state") == "downloading") && !WorkerAlive(job) && Age(job) > 30)
            {
                job["state"] = "failed"; job["error"] = "下载进程已停止，请重新提交任务。"; SaveJob(job);
            }
            object value; double percent = 0.0;
            if (job.TryGetValue("percent", out value) && (value is int || value is long || value is double || value is decimal))
                percent = Convert.ToDouble(value, CultureInfo.InvariantCulture);
            if (double.IsNaN(percent) || double.IsInfinity(percent)) percent = 0.0;
            percent = Math.Max(0, Math.Min(100, percent));
            string url;
            try { url = CanonicalDownload(Text(job, "url")); } catch { url = ""; }
            List<string> files = SafeFiles(job);
            return new { ok = true, job = new { id = id, state = Text(job, "state"), percent = percent,
                downloadDir = Text(job, "downloadDir"), error = SafeError(Text(job, "error")), platform = "bilibili",
                url = url, title = SafeTitle(Text(job, "title")), media = Text(job, "media", "video"),
                audioQuality = StoredAudioQuality(job), files = files, warnings = SafeWarnings(job),
                danmaku = StoredDanmaku(job), biliSubtitles = StoredBiliSubtitles(job),
                fileCount = StoredFileCount(job, files.Count) } };
        }
        throw new InvalidOperationException("不支持的桥接操作。");
    }

    static string BiliRoot()
    {
        try
        {
            string config = Path.Combine(Home, "bili_paths.json");
            if (!File.Exists(config)) return "";
            var data = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(config, Encoding.UTF8));
            string root = Text(data, "bili_root").Trim();
            if (root == "" || !Path.IsPathRooted(root) || root.IndexOfAny(new[] { '\r', '\n', '\0' }) >= 0) return "";
            return Path.GetFullPath(root);
        }
        catch { return ""; }
    }

    static string SafeTitle(string title)
    {
        return new string((title ?? "").Where(c => !char.IsControl(c)).Take(512).ToArray());
    }
    static string SafeError(string error)
    {
        string[] known = { "", "下载进程已停止，请重新提交任务。", "无法启动下载进程。", "下载超过两小时，已停止。",
            "下载进程异常退出，请查看任务日志。", "下载进程异常退出，请重新提交任务。",
            "本机下载进程失败，请检查下载目录、Bili23 内核与网络设置。", "需要登录：请在 Bili23 中重新登录 B 站后重试。",
            "内容不可访问或需要相应权限，请在 Bili23 中确认账号权限。", "内容不存在或不可访问。",
            "网络请求失败，请检查网络或代理设置。", "FFmpeg 处理失败，请检查媒体流和保存目录。",
            "指定音频码率需要支持 aac/libmp3lame 的完整 FFmpeg；请准备完整编码器或改选自动音质后重试。",
            "原音轨需要现有完整 FFmpeg 和同目录 ffprobe；请准备工具后重试，或选择 MP3/M4A 转换。",
            "无法确认或无损封装此原音轨；请检查媒体流，或选择 MP3/M4A 转换。", CanceledError,
            "下载失败，请检查内容可访问性、Bili23 内核和保存目录。" };
        return known.Contains(error) ? error : "下载失败，请检查内容可访问性、Bili23 内核和保存目录。";
    }
    static string LocalDirectory(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || path.Any(char.IsControl) || !Regex.IsMatch(path, @"^[A-Za-z]:[\\/]"))
            throw new InvalidOperationException("任务保存目录必须是现存的本机普通文件夹。");
        string full = Path.GetFullPath(path);
        if (!Directory.Exists(full) || File.Exists(full))
            throw new InvalidOperationException("任务保存目录必须是现存的本机普通文件夹。");
        var drive = new DriveInfo(Path.GetPathRoot(full));
        if (drive.DriveType == DriveType.Network || drive.DriveType == DriveType.Unknown || drive.DriveType == DriveType.NoRootDirectory)
            throw new InvalidOperationException("不支持打开远程任务目录。");
        for (var dir = new DirectoryInfo(full); dir != null; dir = dir.Parent)
            if ((dir.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("任务目录不能是链接或重解析点。");
        return full;
    }
    static List<string> SafeFiles(Dictionary<string, object> job)
    {
        var files = new List<string>(); object value;
        if (!job.TryGetValue("files", out value) || value is string || !(value is System.Collections.IEnumerable)) return files;
        try
        {
            string root = LocalDirectory(Text(job, "downloadDir")).TrimEnd('\\', '/') + "\\";
            foreach (object item in (System.Collections.IEnumerable)value)
            {
                string path = item as string;
                if (string.IsNullOrEmpty(path) || path.Any(char.IsControl) || !Regex.IsMatch(path, @"^[A-Za-z]:[\\/]")) continue;
                try
                {
                    string full = Path.GetFullPath(path);
                    if (!full.StartsWith(root, StringComparison.OrdinalIgnoreCase) || !File.Exists(full)
                        || (File.GetAttributes(full) & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0) continue;
                    LocalDirectory(Path.GetDirectoryName(full));
                    if (!files.Contains(full, StringComparer.OrdinalIgnoreCase)) files.Add(full);
                    if (files.Count == 20) break;
                }
                catch { }
            }
        }
        catch { }
        return files;
    }
static object OpenJobFile(Dictionary<string, object> msg)
    {
        if (msg.Keys.Any(key => key != "action" && key != "jobId" && key != "fileIndex" && key != "filePath"))
            throw new InvalidOperationException("open-job-file 仅接受 jobId 和 fileIndex。");
        string id = ValidateId(Text(msg, "jobId"));
        object raw;
        if (!msg.TryGetValue("fileIndex", out raw) || !(raw is int) || (int)raw < 0 || (int)raw >= 20)
            throw new InvalidOperationException("文件序号无效。");
        var job = ReadJob(id);
        var paths = new List<string>();
        if (job.TryGetValue("files", out raw) && raw is System.Collections.IEnumerable && !(raw is string))
            foreach (object value in (System.Collections.IEnumerable)raw) paths.Add(value as string ?? "");
        int index = (int)msg["fileIndex"];
        if (index >= paths.Count) throw new InvalidOperationException("文件已不存在，请刷新任务记录。");
        string path = paths[index];
        if (msg.ContainsKey("filePath"))
        {
            path = Text(msg, "filePath");
            if (!paths.Contains(path, StringComparer.OrdinalIgnoreCase)) throw new InvalidOperationException("文件记录已变化，请刷新任务记录。");
        }
        var candidate = new Dictionary<string, object> { { "downloadDir", Text(job, "downloadDir") }, { "files", new[] { path } } };
        string[] extensions = { ".mp4", ".mkv", ".webm", ".mov", ".ts", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".aac", ".mka", ".wav", ".ac3", ".eac3" };
        if (SafeFiles(candidate).Count != 1 || !extensions.Contains(Path.GetExtension(path).ToLowerInvariant()))
            throw new InvalidOperationException("文件已不存在或不是可打开的媒体文件。");
#if !BRIDGE_TEST
        Process.Start(new ProcessStartInfo { FileName = path, UseShellExecute = true, WorkingDirectory = LocalDirectory(Text(job, "downloadDir")) });
#endif
        return new { ok = true, jobId = id, fileIndex = index };
    }
    static object OpenJobFolder(Dictionary<string, object> msg)
    {
        if (msg.Keys.Any(key => key != "action" && key != "jobId"))
            throw new InvalidOperationException("open-job-folder 仅接受 jobId。");
        string id = ValidateId(Text(msg, "jobId"));
        var job = ReadJob(id);
        string dir = LocalDirectory(Text(job, "downloadDir"));
        List<string> files = SafeFiles(job);
        bool selected = files.Count > 0;
#if !BRIDGE_TEST
        Process.Start(new ProcessStartInfo {
            FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "explorer.exe"),
            Arguments = selected ? "/select," + Quote(files[0]) : Quote(dir), UseShellExecute = false
        });
#endif
        return new { ok = true, jobId = id, downloadDir = dir, selected = selected };
    }

    static string JobFile(string id) { return Path.Combine(Jobs, id + ".json"); }
    static string CancelFile(string id) { return Path.Combine(Jobs, id + ".cancel"); }
    static string ValidateId(string id)
    {
        if (!Regex.IsMatch(id ?? "", "^[a-f0-9]{32}$")) throw new InvalidOperationException("任务标识无效。");
        return id;
    }
    static bool CancelRequested(string id) { return File.Exists(CancelFile(id)); }
    static void RemoveCancelMarker(string id)
    {
        try { File.Delete(CancelFile(id)); } catch { }
    }
    static void DeletePrivateTree(string path)
    {
        try
        {
            if (!Directory.Exists(path)) return;
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) return;
            foreach (string child in Directory.GetFiles(path, "*", SearchOption.AllDirectories))
            {
                try { if ((File.GetAttributes(child) & FileAttributes.ReparsePoint) == 0) File.Delete(child); } catch { }
            }
            Directory.Delete(path, true);
        }
        catch { }
    }
    static void CleanupCanceled(Dictionary<string, object> job)
    {
        string id = Text(job, "id");
        DeletePrivateTree(Path.Combine(Jobs, id + ".work"));
        string root = Text(job, "downloadDir");
        try
        {
            if (Directory.Exists(root) && Regex.IsMatch(root, @"^[A-Za-z]:[\\/]"))
            {
                foreach (string directory in Directory.GetDirectories(root, ".veylo-original-" + id + "-*", SearchOption.AllDirectories))
                    DeletePrivateTree(directory);
                foreach (string file in Directory.GetFiles(root, ".veylo-partial-" + id + "-*", SearchOption.AllDirectories))
                    try { if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) == 0) File.Delete(file); } catch { }
            }
        }
        catch { }
    }
    static void FinalizeCanceled(Dictionary<string, object> job)
    {
        CleanupCanceled(job);
        List<string> files = SafeFiles(job);
        job["files"] = files;
        job["fileCount"] = StoredFileCount(job, files.Count);
        job["state"] = "canceled";
        job["error"] = CanceledError;
        RemoveCancelMarker(Text(job, "id"));
        SaveJob(job);
    }
    static object CancelJob(Dictionary<string, object> msg)
    {
        if (msg.Keys.Any(key => key != "action" && key != "jobId"))
            throw new InvalidOperationException("cancel-job 仅接受 jobId。");
        string id = ValidateId(Text(msg, "jobId"));
        var job = ReadJob(id);
        string state = Text(job, "state");
        if (new[] { "completed", "failed", "canceled" }.Contains(state))
            return new { ok = true, jobId = id, state = state };
        if (File.Exists(CancelFile(id))) return new { ok = true, jobId = id, state = "canceling" };
        using (var stream = new FileStream(CancelFile(id), FileMode.CreateNew, FileAccess.Write, FileShare.Read)) { stream.Flush(true); }
        return new { ok = true, jobId = id, state = "canceling" };
    }

    static string CanonicalDownload(string raw)
    {
        Uri uri;
        if (raw == null || raw.Length > 4096 || !Uri.TryCreate(raw.Trim(), UriKind.Absolute, out uri)
            || uri.Scheme != "https" || uri.UserInfo != "" || !uri.IsDefaultPort
            || !new[] { "bilibili.com", "www.bilibili.com", "m.bilibili.com" }.Contains(uri.Host.ToLowerInvariant()))
            throw new InvalidOperationException("只接受 B 站播放页的 HTTPS 链接。");
        Match m = Regex.Match(uri.AbsolutePath, "^/video/(BV[0-9A-Za-z]{10}|av[0-9]{1,12})/?$", RegexOptions.IgnoreCase);
        if (m.Success)
        {
            string id = m.Groups[1].Value;
            if (id.StartsWith("bv", StringComparison.OrdinalIgnoreCase)) id = "BV" + id.Substring(2);
            else id = "av" + id.Substring(2);
            Match page = Regex.Match(uri.Query, "[?&]p=([0-9]{1,4})");
            return "https://www.bilibili.com/video/" + id + (page.Success ? "?p=" + page.Groups[1].Value : "");
        }
        m = Regex.Match(uri.AbsolutePath, "^/(bangumi|cheese)/play/(ep|ss|md)([0-9]{1,12})/?$", RegexOptions.IgnoreCase);
        if (m.Success) return "https://www.bilibili.com/" + m.Groups[1].Value.ToLowerInvariant() + "/play/"
            + m.Groups[2].Value.ToLowerInvariant() + m.Groups[3].Value;
        throw new InvalidOperationException("只支持 B 站视频、番剧和课程播放页。");
    }

    static string CanonicalAny(string raw)
    {
        Uri uri;
        if (raw == null || raw.Length > 4096 || !Uri.TryCreate(raw.Trim(), UriKind.Absolute, out uri)
            || (uri.Scheme != "https" && uri.Scheme != "http") || uri.UserInfo != "" || !uri.IsDefaultPort)
            throw new InvalidOperationException("链接无效。");
        string host = uri.Host.ToLowerInvariant();
        if (host != "b23.tv" && host != "bilibili.com" && host != "www.bilibili.com" && host != "m.bilibili.com")
            throw new InvalidOperationException("只接受 B 站链接。");
        return uri.AbsoluteUri;
    }

    static object AcceptDownload(Dictionary<string, object> msg)
    {
        string url = CanonicalDownload(Text(msg, "url"));
        string quality = Text(msg, "quality", "best");
        string part = EnumOption(msg, "part", "current", "current", "all", "collection");
        string danmaku = EnumOption(msg, "danmaku", "off", "off", "ass", "xml");
        string biliSubtitles = EnumOption(msg, "biliSubtitles", "off", "off", "srt");
        if (!new[] { "best", "120", "112", "80", "64", "32", "16" }.Contains(quality)) throw new InvalidOperationException("不支持的画质选项。");
        string media = EnumOption(msg, "media", "video", "video", "audio-original", "audio-mp3", "audio-m4a");
        string audioQuality = EnumOption(msg, "audioQuality", "auto", "auto", "320", "256", "192", "128");
        if (media == "video" || media == "audio-original") audioQuality = "auto";
        string subtitles = EnumOption(msg, "subtitles", "off", "off", "manual", "auto");
        string subLangs = EnumOption(msg, "subLangs", "zh.*,en", "zh.*,en", "zh.*", "en");
        bool metadata = MetadataOption(msg);
        if (subtitles != "off")
            throw new InvalidOperationException("字幕仅支持 YouTube 视频；请将 subtitles 设为 off。");
        string root = BiliRoot();
        if (!File.Exists(Path.Combine(root, "Bili23.exe")) || !File.Exists(Path.Combine(root, "runtime", "python.exe"))
            || !File.Exists(Path.Combine(root, "bundle", "ffmpeg.exe")) || !File.Exists(DriverScript))
            throw new InvalidOperationException("找不到 Bili23 内核（Bili23.exe / runtime\\python.exe / bundle\\ffmpeg.exe），请检查便携版目录。");
        using (var mutex = new Mutex(false, "Local\\BiliBridgeAccept_" + User.Value))
        {
            if (!mutex.WaitOne(10000)) throw new InvalidOperationException("桥接繁忙，请稍后重试。");
            try
            {
                bool login;
                string dir;
                login = BiliLogin(out dir);
                string custom = Text(msg, "dir").Trim();
                if (custom != "")
                {
                    if (!Path.IsPathRooted(custom) || custom.IndexOfAny(new[] { '\r', '\n', '\0' }) >= 0)
                        throw new InvalidOperationException("保存目录无效，请重新选择文件夹。");
                    dir = Path.GetFullPath(custom);
                    if (File.Exists(dir)) throw new InvalidOperationException("保存目录不是文件夹，请重新选择。");
                }
                foreach (string file in Directory.GetFiles(Jobs, "*.json"))
                {
                    Dictionary<string, object> old;
                    try { old = ReadJob(Path.GetFileNameWithoutExtension(file)); } catch { continue; }
                    if (Text(old, "url") == url && Text(old, "quality") == quality && Text(old, "part") == part
                        && Text(old, "downloadDir") == dir && Text(old, "media", "video") == media
                        && StoredAudioQuality(old) == audioQuality
                        && Text(old, "subtitles", "off") == subtitles && Text(old, "subLangs", "zh.*,en") == subLangs
                        && StoredDanmaku(old) == danmaku && StoredBiliSubtitles(old) == biliSubtitles
                        && StoredMetadata(old) == metadata && Age(old) < 86400
                        && new[] { "queued", "downloading" }.Contains(Text(old, "state")) && !File.Exists(CancelFile(Text(old, "id"))) && (WorkerAlive(old) || Age(old) < 30))
                        return new { ok = true, jobId = Text(old, "id"), downloadDir = Text(old, "downloadDir"), login = old["login"], warnings = SafeWarnings(old) };
                }
                Directory.CreateDirectory(dir);
                string id = Guid.NewGuid().ToString("N");
                var job = new Dictionary<string, object> {
                    { "id", id }, { "url", url }, { "part", part }, { "quality", quality }, { "platform", "bilibili" },
                    { "media", media }, { "audioQuality", audioQuality }, { "subtitles", subtitles }, { "subLangs", subLangs },
                    { "danmaku", danmaku }, { "biliSubtitles", biliSubtitles }, { "metadata", metadata },
                    { "title", "" }, { "files", new List<string>() }, { "fileCount", 0 }, { "warnings", MetadataWarnings(metadata) },
                    { "state", "queued" }, { "percent", 0.0 }, { "downloadDir", dir },
                    { "login", login },
                    { "created", DateTime.UtcNow.Ticks }, { "updated", DateTime.UtcNow.Ticks }, { "pid", 0 }, { "error", "" }
                };
                try
                {
                    SaveJob(job);
                    StartWorker(id);
                }
                catch { job["state"] = "failed"; job["error"] = "无法启动下载进程。"; SaveJob(job); throw new InvalidOperationException("无法启动本机下载进程。"); }
                return new { ok = true, jobId = id, downloadDir = dir, login = login, warnings = MetadataWarnings(metadata) };
            }
            finally { mutex.ReleaseMutex(); }
        }
    }

    static bool BiliLogin(out string downloadDir)
    {
        // 只读 Bili23 的 config.json：下载目录 + 登录标记，不读取、不输出 Cookie 值。
        downloadDir = "";
#if BRIDGE_TEST
        string config = Environment.GetEnvironmentVariable("BILI_TEST_CONFIG");
        if (string.IsNullOrEmpty(config)) throw new InvalidOperationException("缺少隔离测试配置。");
#else
        string config = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "Bili23 Downloader", "config.json");
#endif
        if (!File.Exists(config)) throw new InvalidOperationException("找不到 Bili23 配置（%APPDATA%\\Bili23 Downloader\\config.json），请先运行一次 Bili23 并设置下载目录。");
        var data = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(config, Encoding.UTF8));
        object section;
        string dir = "";
        if (data.TryGetValue("Download", out section) && section is Dictionary<string, object>)
            dir = Text((Dictionary<string, object>)section, "download_path").Trim();
        if (dir == "" || !Path.IsPathRooted(dir) || dir.IndexOfAny(new[] { '\r', '\n', '\0' }) >= 0)
            throw new InvalidOperationException("Bili23 的下载目录无效，请先在软件设置中保存下载目录。");
        downloadDir = Path.GetFullPath(dir);
        bool login = false;
        if (data.TryGetValue("Cookie", out section) && section is Dictionary<string, object>)
        {
            object flag;
            if (((Dictionary<string, object>)section).TryGetValue("is_login", out flag))
            {
                if (flag is bool) login = (bool)flag;
                else login = Text((Dictionary<string, object>)section, "is_login").Trim().ToLowerInvariant() == "true";
            }
        }
        return login;
    }

    static object Handoff(Dictionary<string, object> msg)
    {
        string url = CanonicalAny(Text(msg, "url"));
        string root = BiliRoot();
        string exe = Path.Combine(root, "Bili23.exe");
        if (!File.Exists(exe)) throw new InvalidOperationException("找不到 Bili23.exe，请检查便携版目录。");
        SetClipboardText(url);
        bool launched = false;
        if (Process.GetProcessesByName("Bili23").Length == 0)
        {
            Process.Start(new ProcessStartInfo { FileName = exe, WorkingDirectory = root, UseShellExecute = true });
            launched = true;
        }
        return new { ok = true, launched = launched };
    }

    static void SetClipboardText(string text)
    {
        IntPtr memory = Marshal.StringToHGlobalUni(text + "\0");
        try
        {
            for (int attempt = 0; attempt < 20; attempt++)
            {
                if (OpenClipboard(IntPtr.Zero))
                {
                    try
                    {
                        if (!EmptyClipboard()) throw new InvalidOperationException("写入剪贴板失败。");
                        if (SetClipboardData(13, memory) == IntPtr.Zero) throw new InvalidOperationException("写入剪贴板失败。");
                        return;
                    }
                    finally { CloseClipboard(); }
                }
                Thread.Sleep(100);
            }
            throw new InvalidOperationException("剪贴板被其他程序占用，请稍后重试。");
        }
        catch { Marshal.FreeHGlobal(memory); throw; }
        // 成功时内存由系统接管，不释放
    }

    [ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
    class FileOpenDialog { }

    // The full vtable order matters: SetFileTypes through Unadvise precede
    // the option methods even though this host only uses a few of them.
    [ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IFileDialog
    {
        [PreserveSig] int Show(IntPtr owner);
        [PreserveSig] int SetFileTypes(uint count, IntPtr filterSpec);
        [PreserveSig] int SetFileTypeIndex(uint index);
        [PreserveSig] int GetFileTypeIndex(out uint index);
        [PreserveSig] int Advise(IntPtr events, out uint cookie);
        [PreserveSig] int Unadvise(uint cookie);
        [PreserveSig] int SetOptions(uint options);
        [PreserveSig] int GetOptions(out uint options);
        [PreserveSig] int SetDefaultFolder(IShellItem item);
        [PreserveSig] int SetFolder(IShellItem item);
        [PreserveSig] int GetFolder(out IShellItem item);
        [PreserveSig] int GetCurrentSelection(out IShellItem item);
        [PreserveSig] int SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        [PreserveSig] int GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
        [PreserveSig] int SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        [PreserveSig] int SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        [PreserveSig] int SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        [PreserveSig] int GetResult(out IShellItem item);
        [PreserveSig] int AddPlace(IShellItem item, int alignment);
        [PreserveSig] int SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string extension);
        [PreserveSig] int Close(int hr);
        [PreserveSig] int SetClientGuid(ref Guid guid);
        [PreserveSig] int ClearClientData();
        [PreserveSig] int SetFilter(IntPtr filter);
    }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellItem
    {
        void BindToHandler(IntPtr bindContext, ref Guid handler, ref Guid interfaceId, out IntPtr result);
        void GetParent(out IShellItem parent);
        void GetDisplayName(uint format, [MarshalAs(UnmanagedType.LPWStr)] out string name);
        void GetAttributes(uint mask, out uint attributes);
        void Compare(IShellItem item, uint hint, out int order);
    }

    static string ValidInitialDirectory(Dictionary<string, object> msg)
    {
        object value;
        if (!msg.TryGetValue("initialDir", out value)) return "";
        if (!(value is string)) throw new InvalidOperationException("pick-folder initialDir 必须为字符串。");
        string path = (string)value;
        if (string.IsNullOrWhiteSpace(path) || path.Any(char.IsControl) || path.Length > 260) return "";
        try
        {
            if (!Regex.IsMatch(path, @"^[A-Za-z]:[\\/]")) return "";
            return LocalDirectory(path);
        }
        catch { return ""; }
    }

    static object PickFolder(string initialDir)
    {
        string picked = null; bool canceled = false; bool failed = false;
        var thread = new Thread(delegate()
        {
            try
            {
                // The Explorer-style common item dialog replaces the legacy
                // tree-only SHBrowseForFolder used by .NET Framework.
                var dialog = (IFileDialog)new FileOpenDialog();
                dialog.SetOptions(0x20u | 0x40u | 0x800u); // PICKFOLDERS | FORCEFILESYSTEM | PATHMUSTEXIST
                dialog.SetTitle("Veylo · 选择保存目录");
                if (initialDir != "")
                {
                    IShellItem folder;
                    Guid iid = typeof(IShellItem).GUID;
                    if (SHCreateItemFromParsingName(initialDir, IntPtr.Zero, ref iid, out folder) == 0)
                    {
                        try { dialog.SetFolder(folder); }
                        finally { Marshal.ReleaseComObject(folder); }
                    }
                }
                if (dialog.Show(GetForegroundWindow()) == 0)
                {
                    IShellItem item;
                    dialog.GetResult(out item);
                    try { item.GetDisplayName(0x80058000u, out picked); } // SIGDN_FILESYSPATH
                    finally { Marshal.ReleaseComObject(item); }
                }
                else canceled = true;
                Marshal.ReleaseComObject(dialog);
            }
            catch { failed = true; }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failed) throw new InvalidOperationException("无法打开目录选择窗口。");
        if (canceled || string.IsNullOrEmpty(picked)) return new { ok = true, canceled = true };
        return new { ok = true, path = Path.GetFullPath(picked) };
    }

    static void Worker(string id)
    {
        Dictionary<string, object> job = ReadJob(id);
        if (new[] { "completed", "failed", "canceled" }.Contains(Text(job, "state"))) return;
        if (CancelRequested(id))
        {
            FinalizeCanceled(job);
            return;
        }
        try
        {
            CanonicalDownload(Text(job, "url"));
            string media = EnumOption(job, "media", "video", "video", "audio-original", "audio-mp3", "audio-m4a");
            string audioQuality = EnumOption(job, "audioQuality", "auto", "auto", "320", "256", "192", "128");
            if (media == "video" || media == "audio-original") audioQuality = "auto";
            string part = EnumOption(job, "part", "current", "current", "all", "collection");
            string danmaku = EnumOption(job, "danmaku", "off", "off", "ass", "xml");
            string biliSubtitles = EnumOption(job, "biliSubtitles", "off", "off", "srt");
            string quality = EnumOption(job, "quality", "best", "best", "120", "112", "80", "64", "32", "16");
            job["audioQuality"] = audioQuality;
            job["danmaku"] = danmaku;
            job["biliSubtitles"] = biliSubtitles;
            string root = BiliRoot();
            Directory.CreateDirectory(Text(job, "downloadDir"));
            job["pid"] = Process.GetCurrentProcess().Id; job["state"] = "downloading"; SaveJob(job);
            var args = new List<string> {
                Quote(DriverScript),
                "--bili-root", Quote(root),
                "--url", Quote(Text(job, "url")),
                "--part", Quote(part),
                "--quality", Quote(quality),
                "--media", Quote(media),
                "--audio-quality", Quote(audioQuality),
                "--danmaku", Quote(danmaku),
                "--subtitles", Quote(biliSubtitles),
                "--job-id", Quote(id),
                "--jobs-dir", Quote(Jobs),
                "--output-dir", Quote(Text(job, "downloadDir"))
            };
            var psi = new ProcessStartInfo
            {
                FileName = Path.Combine(root, "runtime", "python.exe"),
                Arguments = string.Join(" ", args),
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
                WorkingDirectory = root
            };
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            using (var proc = new Process { StartInfo = psi })
            {
                var log = new StringBuilder();
                proc.OutputDataReceived += (s, e) => { if (e.Data != null) lock (StateLock) log.AppendLine(e.Data); };
                proc.ErrorDataReceived += (s, e) => { if (e.Data != null) lock (StateLock) log.AppendLine(e.Data); };
                proc.Start(); proc.BeginOutputReadLine(); proc.BeginErrorReadLine();
                var elapsed = Stopwatch.StartNew();
                bool timedOut = false;
                while (!proc.WaitForExit(500))
                {
                    if (CancelRequested(id))
                    {
                        KillProcessTree(proc.Id);
                        proc.WaitForExit();
                        job = ReadJob(id);
                        FinalizeCanceled(job);
                        return;
                    }
                    if (elapsed.ElapsedMilliseconds >= 7200000)
                    {
                        KillProcessTree(proc.Id);
                        proc.WaitForExit();
                        timedOut = true;
                        break;
                    }
                }
                proc.WaitForExit();
                if (!timedOut && CancelRequested(id))
                {
                    if (!proc.HasExited) KillProcessTree(proc.Id);
                    proc.WaitForExit();
                    job = ReadJob(id);
                    FinalizeCanceled(job);
                    return;
                }
                // Always keep driver-written title/files/progress, including abnormal exits.
                job = ReadJob(id);
                if (timedOut)
                {
                    CleanupCanceled(job);
                    job["state"] = "failed"; job["error"] = "下载超过两小时，已停止。";
                }
                else if (Text(job, "state") != "completed" && Text(job, "state") != "failed" && Text(job, "state") != "canceled")
                {
                    job["state"] = proc.ExitCode == 0 ? "completed" : "failed";
                    job["error"] = proc.ExitCode == 0 ? "" : "下载进程异常退出，请查看任务日志。";
                    if (proc.ExitCode == 0) job["percent"] = 100.0;
                }
                else if (proc.ExitCode != 0 && Text(job, "state") == "completed")
                {
                    job["state"] = "failed"; job["error"] = "下载进程异常退出，请重新提交任务。";
                }
                lock (StateLock)
                {
                    try { File.AppendAllText(Path.Combine(Jobs, id + ".bridge.log"), log.ToString(), Encoding.UTF8); } catch { }
                }
            }
        }
        catch
        {
            try { job = ReadJob(id); } catch { }
            job["state"] = "failed"; job["error"] = "本机下载进程失败，请检查下载目录、Bili23 内核与网络设置。";
        }
        job["files"] = SafeFiles(job);
        job["fileCount"] = StoredFileCount(job, ((List<string>)job["files"]).Count);
        job["title"] = SafeTitle(Text(job, "title"));
        SaveJob(job);
    }

    static string Quote(string value)
    {
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) { if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1).Append(c);
            else result.Append('\\', slashes).Append(c); slashes = 0; }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }
    static Dictionary<string, object> ReadJob(string id)
    {
        string path = JobFile(ValidateId(id));
        for (int attempt = 0; ; attempt++)
        {
            try
            {
                using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var reader = new StreamReader(stream, Encoding.UTF8))
                    return Json.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
            }
            catch (FileNotFoundException) {
                if (attempt == 3) throw new InvalidOperationException("找不到此下载任务。");
                Thread.Sleep(25 * (attempt + 1));
            }
            catch (IOException) { if (attempt == 3) throw; Thread.Sleep(25 * (attempt + 1)); }
        }
    }
    static void KillProcessTree(int pid)
    {
        try
        {
            string systemRoot = Environment.GetEnvironmentVariable("SystemRoot") ?? "C:\\Windows";
            string taskkill = Path.Combine(systemRoot, "System32", "taskkill.exe");
            if (!File.Exists(taskkill)) return;
            using (var killer = Process.Start(new ProcessStartInfo {
                FileName = taskkill,
                Arguments = "/PID " + pid.ToString(CultureInfo.InvariantCulture) + " /T /F",
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true
            })) { killer.WaitForExit(15000); }
        }
        catch { }
    }
    static void SaveJob(Dictionary<string, object> job) { job["updated"] = DateTime.UtcNow.Ticks; Atomic(JobFile(Text(job, "id")), Json.Serialize(job)); }
    static double Age(Dictionary<string, object> job)
    {
        object v; return job.TryGetValue("updated", out v) ? (DateTime.UtcNow.Ticks - Convert.ToInt64(v)) / 10000000.0 : double.MaxValue;
    }
    static bool WorkerAlive(Dictionary<string, object> job)
    {
        object value;
        try { if (!job.TryGetValue("pid", out value) || Convert.ToInt32(value) == 0) return false;
            using (var p = Process.GetProcessById(Convert.ToInt32(value))) { return !p.HasExited && p.ProcessName == Path.GetFileNameWithoutExtension(typeof(BiliBridge).Assembly.Location); } }
        catch { return false; }
    }
    static void ProtectDirectory(string dir)
    {
        var acl = new DirectorySecurity(); acl.SetAccessRuleProtection(true, false);
        foreach (var sid in new[] { User, SystemSid }) acl.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl,
            InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
        Directory.SetAccessControl(dir, acl);
    }
    static void Atomic(string path, string data)
    {
        string temp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try { using (var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                byte[] bytes = new UTF8Encoding(false).GetBytes(data); stream.Write(bytes, 0, bytes.Length); stream.Flush(true); }
            for (int attempt = 0; ; attempt++)
            {
                try { if (File.Exists(path)) File.Replace(temp, path, null); else File.Move(temp, path); break; }
                catch (IOException) { if (attempt == 3) throw; Thread.Sleep(25 * (attempt + 1)); }
            }
        } finally { if (File.Exists(temp)) File.Delete(temp); }
    }
}
