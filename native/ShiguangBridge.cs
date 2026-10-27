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
[assembly: System.Reflection.AssemblyVersion("1.7.2.0")]
[assembly: System.Reflection.AssemblyFileVersion("1.7.2.0")]

internal static class ShiguangBridge
{
    const string Version = "1.7.2";
    const string CanceledError = "已取消下载。";
    const string LiveCapError = "直播录制已达到时长上限，未生成文件。";
    const string SponsorAll = "sponsor,selfpromo,interaction,intro,outro,preview,music_offtopic,filler";
    const string MissingAudioProbe = "原始音频需要 FFmpeg 同目录的 ffprobe.exe，请修复完整便携版的 bin/ffmpeg 目录。";
    const string AudioProbeError = "无法读取原始音轨信息，请检查音频源及 ffprobe.exe。";
    const string AudioCopyError = "原始音频无法无损封装，未进行转码；请选择 MP3 或 M4A 后重试。";
    const int MaxMessage = 65536;
    const string ExtensionOrigin = "chrome-extension://ikaahmfclfkamdgikchmjnoalpklkddk/";
    static readonly string Home = Path.GetDirectoryName(typeof(ShiguangBridge).Assembly.Location);
#if BRIDGE_TEST
    static readonly string AppDir = Environment.GetEnvironmentVariable("SG_TEST_APP");
#else
    static readonly string AppDir = ResolveAppDir();
#endif
    static readonly string Jobs = Path.Combine(Home, "jobs");
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
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();

    static void StartWorker(string id)
    {
        string executable = typeof(ShiguangBridge).Assembly.Location;
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
                catch { response = new { ok = false, error = "本机桥接无法处理请求，请检查软件路径和文件权限。" }; }
                byte[] encoded = Encoding.UTF8.GetBytes(Json.Serialize(response));
                output.Write(BitConverter.GetBytes(encoded.Length), 0, 4);
                output.Write(encoded, 0, encoded.Length);
                output.Flush();
            }
            return 0;
        }
#if BRIDGE_TEST
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
#else
        catch
        {
#endif
            return 1;
        }
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
    static bool Flag(Dictionary<string, object> msg, string key)
    {
        object value;
        if (!msg.TryGetValue(key, out value)) return false;
        if (!(value is bool)) throw new InvalidOperationException(key + " 必须为布尔值。");
        return (bool)value;
    }
    static void ValidatePracticalOptions(Dictionary<string, object> msg)
    {
        bool cover = Flag(msg, "embedThumbnail"), chapters = Flag(msg, "splitChapters"), archive = Flag(msg, "downloadArchive");
        EnumOption(msg, "limitRate", "off", "off", "512K", "1M", "5M", "10M");
        EnumOption(msg, "concurrentFragments", "auto", "auto", "1", "4", "8", "16");
        if (cover && Text(msg, "media", "video") == "audio-original") throw new InvalidOperationException("嵌入封面请改选视频、MP3 或 M4A。");
        if (chapters && (Text(msg, "section") != "" || Text(msg, "live", "off") != "off" || Text(msg, "media") == "audio-original"))
            throw new InvalidOperationException("按章节拆分不能与原音轨、时间片段或直播共用。");
        if (archive && Text(msg, "live", "off") != "off") throw new InvalidOperationException("直播录制不能启用防止重复下载。");
    }
    static string PracticalKey(Dictionary<string, object> job)
    {
        return Json.Serialize(new object[] { Flag(job, "embedThumbnail"), Flag(job, "splitChapters"), Flag(job, "downloadArchive"),
            Text(job, "limitRate", "off"), Text(job, "concurrentFragments", "auto") });
    }
    static string ArchivePath(Dictionary<string, object> job)
    {
        // Archive identity describes the output variant; ranges and playlists share video IDs.
        string variant = Json.Serialize(new object[] { Text(job, "downloadDir").ToUpperInvariant(), Text(job, "platform"), Text(job, "media", "video"),
            Text(job, "quality", "best"), StoredAudioQuality(job), Text(job, "subtitles", "off"), Text(job, "subLangs", "zh.*,en"),
            StoredMetadata(job), Text(job, "sponsorblock", "off"), StoredSectionKey(job), Flag(job, "embedThumbnail"), Flag(job, "splitChapters") });
        string digest;
        using (var sha = System.Security.Cryptography.SHA256.Create()) digest = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(variant))).Replace("-", "").ToLowerInvariant();
        string root = Path.Combine(Home, "archive");
        Directory.CreateDirectory(root); LocalDirectory(root); ProtectDirectory(root);
        return Path.Combine(root, digest + ".txt");
    }
    static void SyncArchive(Dictionary<string, object> job, bool commit)
    {
        if (!Flag(job, "downloadArchive")) return;
        string shared = ArchivePath(job), local = Path.Combine(Jobs, Text(job, "id") + ".archive");
        using (var mutex = new Mutex(false, "Local\\ShiguangBridgeArchive_" + User.Value))
        {
            if (!mutex.WaitOne(10000)) throw new InvalidOperationException("下载记录繁忙，请稍后重试。");
            try
            {
                foreach (string path in new[] { shared, local })
                    if (File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException("下载记录路径无效。");
                if (!commit) Atomic(local, File.Exists(shared) ? File.ReadAllText(shared, Encoding.UTF8) : "");
                else if (File.Exists(local))
                {
                    var entries = new HashSet<string>(StringComparer.Ordinal);
                    foreach (string path in new[] { shared, local })
                        if (File.Exists(path)) foreach (string line in File.ReadAllLines(path, Encoding.UTF8))
                            if (Regex.IsMatch(line, @"\A[a-zA-Z0-9_:.-]+ [a-zA-Z0-9_-]+\z")) entries.Add(line);
                    Atomic(shared, string.Join("\n", entries.OrderBy(line => line)) + "\n");
                }
            }
            finally { mutex.ReleaseMutex(); }
        }
    }
    static string StoredAudioQuality(Dictionary<string, object> job)
    {
        string quality = Text(job, "audioQuality", "auto");
        return new[] { "audio-mp3", "audio-m4a" }.Contains(Text(job, "media", "video")) && new[] { "320", "256", "192", "128" }.Contains(quality) ? quality : "auto";
    }
    static bool BooleanOption(Dictionary<string, object> msg, string key, bool fallback)
    {
        object value;
        if (!msg.TryGetValue(key, out value)) return fallback;
        if (!(value is bool)) throw new InvalidOperationException(key + " 必须为布尔值。");
        return (bool)value;
    }
    static int IntegerOption(Dictionary<string, object> msg, string key, int fallback, int minimum, int maximum)
    {
        object value;
        if (!msg.TryGetValue(key, out value)) return fallback;
        if (!(value is int) && !(value is long)) throw new InvalidOperationException(key + " 必须为 1 到 720 的整数。");
        long number = Convert.ToInt64(value, CultureInfo.InvariantCulture);
        if (number < minimum || number > maximum) throw new InvalidOperationException(key + " 必须为 1 到 720 的整数。");
        return (int)number;
    }
    static decimal SectionTime(string value)
    {
        string[] parts = value.Split(':');
        decimal number;
        if (parts.Length == 1)
        {
            if (!Regex.IsMatch(parts[0], @"\A[0-9]+(?:\.[0-9]{1,3})?\z")
                || !decimal.TryParse(parts[0], NumberStyles.AllowDecimalPoint, CultureInfo.InvariantCulture, out number))
                throw new InvalidOperationException("片段格式无效，请使用 时:分:秒-时:分:秒。");
            return number;
        }
        if ((parts.Length != 2 && parts.Length != 3)
            || !Regex.IsMatch(parts[parts.Length - 1], @"\A[0-5]?[0-9](?:\.[0-9]{1,3})?\z")
            || !Regex.IsMatch(parts[parts.Length - 2], parts.Length == 2 ? @"\A[0-9]+\z" : @"\A[0-5]?[0-9]\z")
            || (parts.Length == 3 && !Regex.IsMatch(parts[0], @"\A[0-9]+\z")))
            throw new InvalidOperationException("片段格式无效，请使用 时:分:秒-时:分:秒。");
        decimal seconds = decimal.Parse(parts[parts.Length - 1], CultureInfo.InvariantCulture);
        decimal minutes = decimal.Parse(parts[parts.Length - 2], CultureInfo.InvariantCulture);
        decimal hours = parts.Length == 3 ? decimal.Parse(parts[0], CultureInfo.InvariantCulture) : 0;
        return hours * 3600 + minutes * 60 + seconds;
    }
    static string FormatSeconds(decimal value)
    {
        return value.ToString("0.###", CultureInfo.InvariantCulture);
    }
    static string NormalizeSection(string section)
    {
        if (section == "") return "";
        Match match = Regex.Match(section, @"\A(.+)-(inf|.+)\z");
        if (!match.Success || section.Length > 32) throw new InvalidOperationException("片段格式无效，请使用 时:分:秒-时:分:秒。");
        decimal start = SectionTime(match.Groups[1].Value), end;
        bool infinite = match.Groups[2].Value == "inf";
        if (infinite) end = 0;
        else end = SectionTime(match.Groups[2].Value);
        if ((!infinite && start >= end) || start < 0 || start > 86400 || (!infinite && end > 86400))
            throw new InvalidOperationException("片段起止时间无效，结束时间必须大于开始时间且不超过 24 小时。");
        return "*" + FormatSeconds(start) + "-" + (infinite ? "inf" : FormatSeconds(end));
    }
    static bool StoredPlaylist(Dictionary<string, object> job)
    {
        object value; return job.TryGetValue("playlist", out value) && value is bool && (bool)value;
    }
    static string StoredSectionKey(Dictionary<string, object> job)
    {
        try { return NormalizeSection(Text(job, "section")); } catch { return Text(job, "section"); }
    }
    static int StoredLiveMinutes(Dictionary<string, object> job)
    {
        object value;
        return job.TryGetValue("liveMaxMinutes", out value) && (value is int || value is long) && Convert.ToInt64(value) >= 1 && Convert.ToInt64(value) <= 720
            ? Convert.ToInt32(value) : 240;
    }
    static void ValidateFeatureOptions(Dictionary<string, object> msg, string platform, out string sponsorblock, out string section,
        out string normalizedSection, out bool playlist, out string items, out string live, out int liveMaxMinutes)
    {
        sponsorblock = EnumOption(msg, "sponsorblock", "off", "off", "sponsor", "all");
        object sectionValue;
        section = "";
        if (msg.TryGetValue("section", out sectionValue))
        {
            if (!(sectionValue is string)) throw new InvalidOperationException("片段格式无效，请使用 时:分:秒-时:分:秒。");
            section = (string)sectionValue;
        }
        normalizedSection = NormalizeSection(section);
        playlist = BooleanOption(msg, "playlist", false);
        object itemValue;
        items = "";
        if (msg.TryGetValue("items", out itemValue))
        {
            if (!(itemValue is string)) throw new InvalidOperationException("items 必须为列表条目范围文本。");
            items = (string)itemValue;
        }
        if (items.Length > 120 || (items != "" && !Regex.IsMatch(items, @"\A[0-9]{1,5}(?:-[0-9]{0,5})?(?:,[0-9]{1,5}(?:-[0-9]{0,5})?){0,19}\z")))
            throw new InvalidOperationException("列表条目范围无效。");
        live = EnumOption(msg, "live", "off", "off", "from-start");
        liveMaxMinutes = IntegerOption(msg, "liveMaxMinutes", 240, 1, 720);
        if (sponsorblock != "off" && platform != "youtube") throw new InvalidOperationException("跳过片段仅支持 YouTube 视频。");
        if (sponsorblock != "off" && Text(msg, "media", "video") == "audio-original")
            throw new InvalidOperationException("原音轨不重新编码，无法移除片段；请改选 MP3 / M4A 或关闭跳过片段。");
        if (section != "" && Text(msg, "media", "video") == "audio-original")
            throw new InvalidOperationException("原音轨不重新编码，无法裁剪片段；请改选 MP3 / M4A 或下载完整视频。");
        if (playlist && platform != "youtube") throw new InvalidOperationException("播放列表仅支持 YouTube。");
        if (!playlist && items != "") throw new InvalidOperationException("items 仅支持播放列表下载。");
        if (playlist && section != "") throw new InvalidOperationException("播放列表不能使用片段裁剪。");
        if (live == "from-start" && platform != "youtube") throw new InvalidOperationException("直播录制仅支持 YouTube。");
        if (live == "from-start" && sponsorblock != "off") throw new InvalidOperationException("直播录制不能启用跳过片段。");
        if (live == "from-start" && playlist) throw new InvalidOperationException("直播录制不能与播放列表同时使用。");
        if (live == "from-start" && section != "") throw new InvalidOperationException("直播录制不能使用片段裁剪。");
        if (live == "from-start" && Text(msg, "media", "video") == "audio-original")
            throw new InvalidOperationException("直播录制不支持原音轨，请改选 MP3 / M4A。");
    }
    static void ValidateOptions(Dictionary<string, object> msg, string platform, out string media, out string audioQuality, out string subtitles, out string subLangs, out bool metadata)
    {
        media = EnumOption(msg, "media", "video", "video", "audio-original", "audio-mp3", "audio-m4a");
        audioQuality = EnumOption(msg, "audioQuality", "auto", "auto", "320", "256", "192", "128");
        if (media == "video" || media == "audio-original") audioQuality = "auto";
        subtitles = EnumOption(msg, "subtitles", "off", "off", "manual", "auto");
        subLangs = EnumOption(msg, "subLangs", "zh.*,en", "zh.*,en", "zh.*", "en");
        metadata = MetadataOption(msg);
        if (subtitles != "off" && (platform != "youtube" || media != "video"))
            throw new InvalidOperationException("字幕仅支持 YouTube 视频；请将 subtitles 设为 off。");
    }

    static string CookieState(string path, bool x)
    {
        try
        {
            if (!File.Exists(path)) return "guest";
            if ((x ? XCookies(path) : YoutubeCookies(path)).Count > 0) return "signed-in";
            long now = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
            bool valid = false, usable = false;
            var names = new Dictionary<string, HashSet<string>>();
            var expired = new HashSet<string>();
            string[] domains = x ? new[] { "x.com", "twitter.com" } : new[] { "youtube.com", "google.com" };
            foreach (string line in File.ReadLines(path, Encoding.UTF8))
            {
                if (line.Length == 0 || (line.StartsWith("#") && !line.StartsWith("#HttpOnly_"))) continue;
                string[] fields = line.Split('\t'); long expires;
                if (fields.Length != 7 || fields.Any(s => s.Any(c => c < 32 || c > 126))
                    || !long.TryParse(fields[4], out expires) || expires < 0 || fields[6].Trim() == ""
                    || !new[] { "TRUE", "FALSE" }.Contains(fields[1]) || !new[] { "TRUE", "FALSE" }.Contains(fields[3])
                    || !fields[2].StartsWith("/")) continue;
                string host = fields[0].Replace("#HttpOnly_", "").TrimStart('.').ToLowerInvariant();
                if (!domains.Any(d => host == d || host.EndsWith("." + d, StringComparison.Ordinal))) continue;
                valid = true;
                bool live = expires == 0 || expires > now;
                if (live) usable = true;
                if (x && (host == "x.com" || host == "twitter.com") && fields[2] == "/")
                {
                    if (!names.ContainsKey(host)) names[host] = new HashSet<string>();
                    names[host].Add(fields[5]);
                    if (!live && (fields[5] == "auth_token" || fields[5] == "ct0")) expired.Add(host);
                }
            }
            if (x) return names.Any(pair => pair.Value.Contains("auth_token") && pair.Value.Contains("ct0") && expired.Contains(pair.Key)) ? "expired" : "guest";
            return valid && !usable ? "expired" : "guest";
        }
        catch { return "guest"; }
    }
    static string ValidInitialDirectory(Dictionary<string, object> msg)
    {
        object value;
        if (!msg.TryGetValue("initialDir", out value) || !(value is string)) return "";
        string path = (string)value;
        if (path.Length == 0 || path.Length > 259) return "";
        try
        {
            string full = LocalDirectory(path);
            return full.Length > 3 ? full.TrimEnd('\\', '/') : full;
        }
        catch { return ""; }
    }

    static object Dispatch(Dictionary<string, object> msg)
    {
        string action = Text(msg, "action");
        if (action == "ping") return new { ok = true, version = Version, appRoot = AppDir ?? "", engineAvailable = File.Exists(Engine()) && File.Exists(Path.Combine(AppDir ?? "", "bin", "ffmpeg", "ffmpeg.exe")),
            audioQualityOptions = new[] { "auto", "320", "256", "192", "128" },
            mediaOptions = new[] { "video", "audio-original", "audio-mp3", "audio-m4a" },
            features = new[] { "cancel", "sponsorblock", "sections", "playlist", "live", "pick-folder-initial", "login-status", "thumbnail", "chapters", "archive", "transfer", "open-file" },
            login = new { youtube = CookieState(Path.Combine(AppDir ?? "", "bin", "cookies_youtube.txt"), false),
                x = CookieState(Path.Combine(AppDir ?? "", "bin", "cookies_twitter.txt"), true) } };
        if (action == "yt-download" || action == "x-download") return Accept(msg);
        if (action == "cancel-job") return CancelJob(msg);
        if (action == "open-job-file") return OpenJobFile(msg);
#if BRIDGE_TEST
        if (action == "test-pick-folder-initial")
        {
            if (msg.Keys.Any(key => key != "action" && key != "initialDir")) throw new InvalidOperationException("pick-folder initialDir 请求字段无效。");
            return new { ok = true, initialDir = ValidInitialDirectory(msg) };
        }
#endif
        if (action == "pick-folder")
        {
            if (msg.Keys.Any(key => key != "action" && key != "initialDir")) throw new InvalidOperationException("pick-folder initialDir 请求字段无效。");
            return PickFolder(ValidInitialDirectory(msg));
        }
        if (action == "open-job-folder") return OpenJobFolder(msg);
        if (action == "job-status")
        {
            string id = ValidateId(Text(msg, "jobId"));
            Dictionary<string, object> job;
            try { job = ReadJob(id); }
            catch (FileNotFoundException) { throw new InvalidOperationException("找不到此下载任务。"); }
            if (File.Exists(CancelFile(id)) && !WorkerAlive(job)) FinalizeCanceled(job);
            else if ((Text(job, "state") == "queued" || Text(job, "state") == "downloading") && !WorkerAlive(job) && Age(job) > 30)
            {
                job["state"] = "failed"; job["error"] = "下载进程已停止，请重新提交任务。"; SaveJob(job);
                try { File.Delete(Path.Combine(Jobs, id + ".cookies.txt")); } catch { }
            }
            object value; double percent = 0.0;
            if (job.TryGetValue("percent", out value) && (value is int || value is long || value is double || value is decimal))
                percent = Convert.ToDouble(value, CultureInfo.InvariantCulture);
            if (double.IsNaN(percent) || double.IsInfinity(percent)) percent = 0.0;
            percent = Math.Max(0, Math.Min(100, percent));
            string platform, url;
            try { url = Canonical(Text(job, "url"), StoredPlaylist(job), out platform); } catch { platform = ""; url = ""; }
            return new { ok = true, job = new { id = id, state = Text(job, "state"), percent = percent,
                speedBytesPerSecond = DownloadSpeed(job),
                downloadDir = Text(job, "downloadDir"), error = SafeError(Text(job, "error")), platform = platform,
                url = url, title = SafeTitle(Text(job, "title")), media = Text(job, "media", "video"), audioQuality = StoredAudioQuality(job), files = SafeFiles(job),
                sponsorblock = Text(job, "sponsorblock", "off"), section = Text(job, "section"), playlist = StoredPlaylist(job), items = Text(job, "items"),
                live = Text(job, "live", "off"), fileCount = StoredFileCount(job), warnings = SafeWarnings(job) } };
        }
        throw new InvalidOperationException("不支持的桥接操作。");
    }

    static string ResolveAppDir()
    {
        string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        string legacy = Path.Combine(desktop, "FluentYTDL-3.7.6-win64-full");
        try
        {
            string config = Path.Combine(Home, "fluent_paths.json");
            if (File.Exists(config))
            {
                var paths = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(config, Encoding.UTF8));
                string configured = LocalDirectory(Text(paths, "fluent_root"));
                if (UsableApp(configured)) return configured;
            }
        }
        catch { }
        if (UsableApp(legacy)) return legacy;
        // Only the known desktop category is searched; never scan the disk or profiles.
        try
        {
            string category = Path.Combine(desktop, "Applications");
            if (Directory.Exists(category))
            {
                string[] matches = Directory.GetDirectories(category, "FluentYTDL-*-win64-full", SearchOption.TopDirectoryOnly)
                    .Where(UsableApp).ToArray();
                if (matches.Length == 1) return LocalDirectory(matches[0]);
            }
        }
        catch { }
        return legacy;
    }
    static bool UsableApp(string root)
    {
        return !string.IsNullOrEmpty(root) && File.Exists(Path.Combine(root, "bin", "yt-dlp", "yt-dlp.exe"))
            && File.Exists(Path.Combine(root, "bin", "ffmpeg", "ffmpeg.exe"));
    }
    static string Engine() { return Path.Combine(AppDir ?? "", "bin", "yt-dlp", "yt-dlp.exe"); }
    static void RequireAudioProbe(string media)
    {
        if (media == "audio-original" && !File.Exists(Path.Combine(AppDir ?? "", "bin", "ffmpeg", "ffprobe.exe")))
            throw new InvalidOperationException(MissingAudioProbe);
    }
    static string SafeTitle(string title)
    {
        return new string((title ?? "").Where(c => !char.IsControl(c)).Take(512).ToArray());
    }
    static string SafeError(string error)
    {
        string[] known = { "", CanceledError, LiveCapError, "下载进程已停止，请重新提交任务。", "无法启动下载进程。", "下载超过两小时，已停止。",
            "视频不可访问或网络请求失败，请检查代理及 X 登录状态。", "视频不可访问或网络请求失败，请检查代理及 YouTube 登录状态。",
            "X 要求登录。请在 Tabbit 登录 X 后刷新独立的 X Cookie。", "YouTube 要求登录或验证。请确认浏览器中的 YouTube 登录状态后刷新 Cookie。",
            "这条推文没有可下载的视频。", "本机下载进程失败，请检查下载目录、内核与代理设置。", MissingAudioProbe, AudioProbeError, AudioCopyError };
        return known.Contains(error) ? error : "下载失败，请检查内容可访问性和保存目录。";
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
    static string CancelFile(string id) { return Path.Combine(Jobs, id + ".cancel"); }
    static bool Terminal(string state) { return state == "completed" || state == "failed" || state == "canceled"; }
    static int StoredFileCount(Dictionary<string, object> job)
    {
        object value;
        if (job.TryGetValue("fileCount", out value) && (value is int || value is long || value is double || value is decimal))
            return Math.Max(0, Convert.ToInt32(value, CultureInfo.InvariantCulture));
        return SafeFiles(job).Count;
    }
    static List<string> SafeWarnings(Dictionary<string, object> job)
    {
        var result = new List<string>(); object value;
        if (!job.TryGetValue("warnings", out value) || value is string || !(value is System.Collections.IEnumerable)) return result;
        foreach (object item in (System.Collections.IEnumerable)value)
        {
            string warning = item as string;
            if (warning == "部分条目下载失败。" || warning == "已达到直播录制时长上限。" || warning == "视频已存在，已跳过重复下载。" || warning == "没有可用封面，媒体已保存。") result.Add(warning);
        }
        return result;
    }
    static void AddWarning(Dictionary<string, object> job, string warning)
    {
        List<string> warnings = SafeWarnings(job);
        if (!warnings.Contains(warning)) warnings.Add(warning);
        job["warnings"] = warnings;
    }
    static List<string> FileKeys(Dictionary<string, object> job)
    {
        var result = new List<string>(); object value;
        if (job.TryGetValue("_fileKeys", out value) && value is System.Collections.IEnumerable && !(value is string))
            foreach (object item in (System.Collections.IEnumerable)value) if (item is string) result.Add((string)item);
        return result;
    }
    static void SetFileCount(Dictionary<string, object> job, int count) { job["fileCount"] = Math.Max(0, count); }
    static bool AddCompletedFile(Dictionary<string, object> job, string path)
    {
        var candidate = new Dictionary<string, object> { { "downloadDir", Text(job, "downloadDir") }, { "files", new[] { path } } };
        List<string> valid = SafeFiles(candidate);
        if (valid.Count != 1) return false;
        string file = valid[0];
        List<string> keys = FileKeys(job);
        if (keys.Contains(file, StringComparer.OrdinalIgnoreCase)) return false;
        keys.Add(file); job["_fileKeys"] = keys;
        int count = StoredFileCount(job);
        SetFileCount(job, count + 1);
        List<string> files = SafeFiles(job);
        if (files.Count < 20) files.Add(file);
        job["files"] = files;
        return true;
    }
    static void RemoveCancelArtifacts(string id, string cookieFile)
    {
        try { File.Delete(CancelFile(id)); } catch { }
        try { if (!string.IsNullOrEmpty(cookieFile)) File.Delete(cookieFile); } catch { }
        try { File.Delete(Path.Combine(Jobs, id + ".cookies.txt")); } catch { }
    }
    static void FinalizeCanceled(Dictionary<string, object> job)
    {
        string id = Text(job, "id");
        job["state"] = "canceled";
        job["error"] = CanceledError;
        job["files"] = SafeFiles(job);
        if (!job.ContainsKey("fileCount")) SetFileCount(job, job["files"] is System.Collections.IEnumerable ? SafeFiles(job).Count : 0);
        CleanupAudioStage(Path.Combine(Jobs, id + ".temp"));
        RemoveCancelArtifacts(id, "");
        SaveJob(job);
    }
    static object CancelJob(Dictionary<string, object> msg)
    {
        if (msg.Keys.Any(key => key != "action" && key != "jobId"))
            throw new InvalidOperationException("cancel-job 仅接受 jobId。");
        string id = ValidateId(Text(msg, "jobId"));
        Dictionary<string, object> job;
        try { job = ReadJob(id); } catch (FileNotFoundException) { throw new InvalidOperationException("找不到此下载任务。"); }
        string state = Text(job, "state");
        if (Terminal(state)) return new { ok = true, jobId = id, state = state };
        if (!new[] { "queued", "downloading" }.Contains(state))
            throw new InvalidOperationException("找不到此下载任务。");
        if (!File.Exists(CancelFile(id))) Atomic(CancelFile(id), "");
        return new { ok = true, jobId = id, state = "canceling" };
    }
    static void KillProcessTree(int pid)
    {
        if (pid <= 0) return;
        string systemRoot = Environment.GetEnvironmentVariable("SystemRoot") ?? Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        string taskkill = Path.Combine(systemRoot, "System32", "taskkill.exe");
        if (!File.Exists(taskkill)) return;
        try
        {
            using (var process = Process.Start(new ProcessStartInfo { FileName = taskkill, Arguments = "/PID " + pid.ToString(CultureInfo.InvariantCulture) + " /T /F",
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true }))
                process.WaitForExit(5000);
        }
        catch { }
    }
    static HashSet<string> SnapshotFiles(string root)
    {
        var result = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        try
        {
            if (!Directory.Exists(root)) return result;
            foreach (string file in Directory.GetFiles(root, "*", SearchOption.AllDirectories))
            {
                try { if ((File.GetAttributes(file) & (FileAttributes.Directory | FileAttributes.ReparsePoint)) == 0) result.Add(Path.GetFullPath(file)); } catch { }
            }
        }
        catch { }
        return result;
    }

static object OpenJobFile(Dictionary<string, object> msg)
    {
        if (msg.Keys.Any(key => key != "action" && key != "jobId" && key != "fileIndex" && key != "filePath"))
            throw new InvalidOperationException("open-job-file 仅接受 jobId 和 fileIndex。");
        string id = ValidateId(Text(msg, "jobId"));
        object raw;
        if (!msg.TryGetValue("fileIndex", out raw) || !(raw is int) || (int)raw < 0 || (int)raw >= 20)
            throw new InvalidOperationException("文件序号无效。");
        if (!File.Exists(JobFile(id))) throw new InvalidOperationException("找不到此下载任务。");
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
        if (!File.Exists(JobFile(id))) throw new InvalidOperationException("找不到此下载任务。");
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
    static string ValidateId(string id)
    {
        if (!Regex.IsMatch(id ?? "", "^[a-f0-9]{32}$")) throw new InvalidOperationException("任务标识无效。");
        return id;
    }
    static string Canonical(string raw, out string platform) { return Canonical(raw, false, out platform); }
    static string Canonical(string raw, bool playlist, out string platform)
    {
        platform = "";
        Uri uri;
        if (raw == null || raw.Length > 4096 || !Uri.TryCreate(raw.Trim(), UriKind.Absolute, out uri)
            || uri.Scheme != "https" || uri.UserInfo != "" || !uri.IsDefaultPort)
            throw new InvalidOperationException("只接受 X / Twitter 或 YouTube 的视频 HTTPS 链接。");
        string host = uri.Host.ToLowerInvariant();
        if (new[] { "x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com" }.Contains(host))
        {
            Match m = Regex.Match(uri.AbsolutePath, "^/([A-Za-z0-9_]{1,50})/status/([0-9]{1,25})(?:/(?:video|photo)/[0-9]+)?/?$");
            if (m.Success) { platform = "x"; return "https://x.com/" + m.Groups[1].Value + "/status/" + m.Groups[2].Value; }
            m = Regex.Match(uri.AbsolutePath, "^/i/web/status/([0-9]{1,25})/?$");
            if (m.Success) { platform = "x"; return "https://x.com/i/web/status/" + m.Groups[1].Value; }
            throw new InvalidOperationException("X 仅支持单条推文视频，不支持主页或时间线。");
        }
        if (new[] { "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be" }.Contains(host))
        {
            if (uri.AbsolutePath == "/playlist")
            {
                string listId = Regex.Match(uri.Query, "[?&]list=([A-Za-z0-9_-]{10,128})(?:&|$)").Groups[1].Value;
                if (!playlist || !Regex.IsMatch(listId, "^[A-Za-z0-9_-]{10,128}$"))
                    throw new InvalidOperationException("YouTube 播放列表仅支持开启 playlist 后直接下载。");
                platform = "youtube";
                return "https://www.youtube.com/playlist?list=" + listId;
            }
            if (playlist && uri.AbsolutePath == "/watch")
            {
                string listId = Regex.Match(uri.Query, "[?&]list=([A-Za-z0-9_-]{10,128})(?:&|$)").Groups[1].Value;
                if (Regex.IsMatch(listId, "^[A-Za-z0-9_-]{10,128}$"))
                {
                    platform = "youtube";
                    return "https://www.youtube.com/playlist?list=" + listId;
                }
                throw new InvalidOperationException("YouTube 播放列表仅支持带 list 参数的链接。");
            }
            if (playlist)
                throw new InvalidOperationException("YouTube 播放列表仅支持带 list 参数的链接。");
            string id = null;
            if (host.EndsWith("youtu.be")) id = Regex.Match(uri.AbsolutePath, "^/([A-Za-z0-9_-]{11})/?$").Groups[1].Value;
            else if (uri.AbsolutePath == "/watch") id = Regex.Match(uri.Query, "[?&]v=([A-Za-z0-9_-]{11})(?:&|$)").Groups[1].Value;
            else id = Regex.Match(uri.AbsolutePath, "^/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{11})/?$").Groups[1].Value;
            if (!Regex.IsMatch(id ?? "", "^[A-Za-z0-9_-]{11}$"))
                throw new InvalidOperationException("YouTube 仅支持单个视频或已启用的播放列表。");
            platform = "youtube";
            return "https://www.youtube.com/watch?v=" + id;
        }
        throw new InvalidOperationException("只接受 X / Twitter 或 YouTube 的视频链接。");
    }

    static object Accept(Dictionary<string, object> msg)
    {
        ValidatePracticalOptions(msg);
        string platform;
        object playlistValue;
        bool requestedPlaylist = msg.TryGetValue("playlist", out playlistValue) && playlistValue is bool && (bool)playlistValue;
        string url = Canonical(Text(msg, "url"), requestedPlaylist, out platform);
        string quality = Text(msg, "quality", "best");
        if (!new[] { "best", "1080", "720", "480" }.Contains(quality)) throw new InvalidOperationException("不支持的画质选项。");
        string media, audioQuality, subtitles, subLangs;
        bool metadata;
        ValidateOptions(msg, platform, out media, out audioQuality, out subtitles, out subLangs, out metadata);
        string sponsorblock, section, normalizedSection, items, live;
        bool playlist; int liveMaxMinutes;
        ValidateFeatureOptions(msg, platform, out sponsorblock, out section, out normalizedSection, out playlist, out items, out live, out liveMaxMinutes);
        string part = EnumOption(msg, "part", "current", "current");
        RequireAudioProbe(media);
        if (!File.Exists(Engine()) || !File.Exists(Path.Combine(AppDir, "bin", "ffmpeg", "ffmpeg.exe")))
            throw new InvalidOperationException("找不到 FluentYTDL 内置 yt-dlp 或 FFmpeg，请检查便携版目录。");
        using (var mutex = new Mutex(false, "Local\\ShiguangBridgeAccept_" + User.Value))
        {
            if (!mutex.WaitOne(10000)) throw new InvalidOperationException("桥接繁忙，请稍后重试。");
            try
            {
                string dir = Text(msg, "dir").Trim();
                if (dir == "")
                {
                    var cfgDefault = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(AppDir, "config.json"), Encoding.UTF8));
                    dir = Text(cfgDefault, "download_dir");
                    if (string.IsNullOrWhiteSpace(dir) || !Path.IsPathRooted(dir) || dir.IndexOfAny(new[] { '\r', '\n', '\0' }) >= 0)
                        throw new InvalidOperationException("FluentYTDL 的保存目录无效，请先在软件设置中保存目录。");
                }
                else if (!Path.IsPathRooted(dir) || dir.IndexOfAny(new[] { '\r', '\n', '\0' }) >= 0)
                    throw new InvalidOperationException("保存目录无效，请重新选择文件夹。");
                dir = Path.GetFullPath(dir);
                if (File.Exists(dir)) throw new InvalidOperationException("保存目录不是文件夹，请重新选择。");
                foreach (string file in Directory.GetFiles(Jobs, "*.json"))
                {
                    Dictionary<string, object> old;
                    try { old = ReadJob(Path.GetFileNameWithoutExtension(file)); } catch { continue; }
                    if (Text(old, "url") == url && Text(old, "quality") == quality && Text(old, "downloadDir") == dir
                        && Text(old, "part", "current") == part && Text(old, "media", "video") == media
                        && StoredAudioQuality(old) == audioQuality && Text(old, "subtitles", "off") == subtitles && Text(old, "subLangs", "zh.*,en") == subLangs
                        && StoredMetadata(old) == metadata && Text(old, "sponsorblock", "off") == sponsorblock
                        && PracticalKey(old) == PracticalKey(msg)
                        && StoredSectionKey(old) == normalizedSection && StoredPlaylist(old) == playlist && Text(old, "items") == items
                        && Text(old, "live", "off") == live && StoredLiveMinutes(old) == liveMaxMinutes && Age(old) < 86400
                        && new[] { "queued", "downloading" }.Contains(Text(old, "state")) && !File.Exists(CancelFile(Text(old, "id")))
                        && (WorkerAlive(old) || Age(old) < 30))
                        return new { ok = true, jobId = Text(old, "id"), downloadDir = Text(old, "downloadDir"), cookieStatus = Text(old, "cookieStatus") };
                }
                var cfg = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(AppDir, "config.json"), Encoding.UTF8));
                Proxy(cfg);
                Directory.CreateDirectory(dir);
                string id = Guid.NewGuid().ToString("N");
                List<string> cookies = platform == "x"
                    ? XCookies(Path.Combine(AppDir, "bin", "cookies_twitter.txt"))
                    : YoutubeCookies(Path.Combine(AppDir, "bin", "cookies_youtube.txt"));
                string cookieFile = cookies.Count == 0 ? "" : Path.Combine(Jobs, id + ".cookies.txt");
                if (cookies.Count > 0) Atomic(cookieFile, "# Netscape HTTP Cookie File\n" + string.Join("\n", cookies) + "\n");
                var job = new Dictionary<string, object> {
                    { "id", id }, { "url", url }, { "platform", platform }, { "quality", quality }, { "state", "queued" }, { "percent", 0.0 },
                    { "downloadDir", dir }, { "part", part }, { "media", media }, { "audioQuality", audioQuality }, { "subtitles", subtitles }, { "subLangs", subLangs },
                    { "metadata", metadata }, { "sponsorblock", sponsorblock }, { "section", section }, { "playlist", playlist }, { "items", items },
                    { "embedThumbnail", Flag(msg, "embedThumbnail") }, { "splitChapters", Flag(msg, "splitChapters") }, { "downloadArchive", Flag(msg, "downloadArchive") },
                    { "limitRate", Text(msg, "limitRate", "off") }, { "concurrentFragments", Text(msg, "concurrentFragments", "auto") },
            {"live", live}, {"liveMaxMinutes", liveMaxMinutes}, {"fileCount", 0}, {"warnings", new List<string>()},
                    {"title", ""}, {"files", new List<string>()}, {"_fileKeys", new List<string>()}, {"_itemIds", new List<string>()},
                    { "cookieStatus", cookies.Count > 0 ? "signed-in" : "guest" },
                    { "created", DateTime.UtcNow.Ticks }, { "updated", DateTime.UtcNow.Ticks }, { "pid", 0 }, { "error", "" }
                };
                try
                {
                    SaveJob(job);
                    StartWorker(id);
                }
                catch { job["state"] = "failed"; job["error"] = "无法启动下载进程。"; SaveJob(job); if (cookieFile != "") File.Delete(cookieFile); throw new InvalidOperationException("无法启动本机下载进程。"); }
                return new { ok = true, jobId = id, downloadDir = dir, cookieStatus = job["cookieStatus"] };
            }
            finally { mutex.ReleaseMutex(); }
        }
    }

    static string Proxy(Dictionary<string, object> cfg)
    {
        string mode = Text(cfg, "proxy_mode", "off"), value = Text(cfg, "proxy_url").Trim();
        if (mode == "off") return "";
        if (mode == "system")
        {
            Uri target = new Uri("https://x.com"); Uri resolved = WebRequest.GetSystemWebProxy().GetProxy(target);
            return resolved == target ? "" : resolved.AbsoluteUri;
        }
        if (mode != "http" && mode != "socks5") throw new InvalidOperationException("代理模式无效，请在 FluentYTDL 网络设置中重新保存。");
        if (!value.Contains("://")) value = mode + "://" + value;
        Uri proxy;
        if (value.IndexOfAny(new[] { '\r', '\n', '\0' }) >= 0 || !Uri.TryCreate(value, UriKind.Absolute, out proxy)
            || (mode == "http" && proxy.Scheme != "http" && proxy.Scheme != "https")
            || (mode == "socks5" && proxy.Scheme != "socks5" && proxy.Scheme != "socks5h") || string.IsNullOrEmpty(proxy.Host))
            throw new InvalidOperationException("代理地址无效，请检查 FluentYTDL 网络设置。");
        return value;
    }

    static List<string> XCookies(string path)
    {
        var rows = new List<string>(); var names = new Dictionary<string, HashSet<string>>();
        if (!File.Exists(path)) return rows;
        long now = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
        foreach (string line in File.ReadLines(path, Encoding.UTF8))
        {
            if (line.Length == 0 || (line.StartsWith("#") && !line.StartsWith("#HttpOnly_"))) continue;
            string[] f = line.Split('\t'); long expires;
            if (f.Length != 7 || f.Any(s => s.Any(c => c < 32 || c > 126)) || !long.TryParse(f[4], out expires)
                || expires < 0 || (expires != 0 && expires <= now) || f[6].Trim() == "") continue;
            string host = f[0].Replace("#HttpOnly_", "").TrimStart('.').ToLowerInvariant();
            if (!new[] { "x.com", "twitter.com" }.Any(d => host == d || host.EndsWith("." + d, StringComparison.Ordinal))) return new List<string>();
            if (!new[] { "TRUE", "FALSE" }.Contains(f[1]) || !new[] { "TRUE", "FALSE" }.Contains(f[3]) || !f[2].StartsWith("/")) return new List<string>();
            rows.Add(line);
            if ((host == "x.com" || host == "twitter.com") && f[2] == "/")
            {
                if (!names.ContainsKey(host)) names[host] = new HashSet<string>(); names[host].Add(f[5]);
            }
        }
        return names.Values.Any(n => n.Contains("auth_token") && n.Contains("ct0")) ? rows : new List<string>();
    }

    static List<string> YoutubeCookies(string path)
    {
        // YouTube Cookie 只允许 YouTube / Google 域，独立于 X 的凭据文件。
        var rows = new List<string>();
        if (!File.Exists(path)) return rows;
        long now = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
        foreach (string line in File.ReadLines(path, Encoding.UTF8))
        {
            if (line.Length == 0 || (line.StartsWith("#") && !line.StartsWith("#HttpOnly_"))) continue;
            string[] f = line.Split('\t'); long expires;
            if (f.Length != 7 || f.Any(s => s.Any(c => c < 32 || c > 126)) || !long.TryParse(f[4], out expires)
                || expires < 0 || (expires != 0 && expires <= now) || f[6].Trim() == "") continue;
            string host = f[0].Replace("#HttpOnly_", "").TrimStart('.').ToLowerInvariant();
            if (!new[] { "youtube.com", "google.com" }.Any(d => host == d || host.EndsWith("." + d, StringComparison.Ordinal))) return new List<string>();
            if (!new[] { "TRUE", "FALSE" }.Contains(f[1]) || !new[] { "TRUE", "FALSE" }.Contains(f[3]) || !f[2].StartsWith("/")) return new List<string>();
            rows.Add(line);
        }
        return rows;
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

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    static extern int SHCreateItemFromParsingName([MarshalAs(UnmanagedType.LPWStr)] string path, IntPtr bindContext,
        ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IShellItem item);

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
                if (!string.IsNullOrEmpty(initialDir))
                {
                    IShellItem initialItem = null;
                    try
                    {
                        Guid iid = new Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE");
                        if (SHCreateItemFromParsingName(initialDir, IntPtr.Zero, ref iid, out initialItem) == 0 && initialItem != null)
                            dialog.SetFolder(initialItem);
                    }
                    catch { }
                    finally { if (initialItem != null) Marshal.ReleaseComObject(initialItem); }
                }
                dialog.SetTitle("Veylo · 选择保存目录");
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

    static int TransferConcurrency(Dictionary<string, object> job, Dictionary<string, object> config)
    {
        string selected = Text(job, "concurrentFragments", "auto");
        if (selected != "auto") return int.Parse(selected, CultureInfo.InvariantCulture);
        object value;
        int count;
        return config != null && config.TryGetValue("concurrent_fragments", out value)
            && (value is int || value is long)
            && int.TryParse(Convert.ToString(value, CultureInfo.InvariantCulture), out count) && count >= 1
            ? Math.Min(16, count) : 4;
    }

    static List<string> DownloadArguments(Dictionary<string, object> job, string proxy, string cookieFile, string staging, Dictionary<string, object> config)
    {
        string platform;
        bool playlist = StoredPlaylist(job);
        Canonical(Text(job, "url"), playlist, out platform);
        string media, audioQuality, subtitles, subLangs; bool metadata;
        ValidateOptions(job, platform, out media, out audioQuality, out subtitles, out subLangs, out metadata);
        string sponsorblock, section, normalizedSection, items, live; int liveMinutes;
        ValidateFeatureOptions(job, platform, out sponsorblock, out section, out normalizedSection, out playlist, out items, out live, out liveMinutes);
        string height = EnumOption(job, "quality", "best", "best", "1080", "720", "480");
        string output = playlist ? "%(playlist_title).80B/%(playlist_index)03d - %(title).120B [%(id)s].%(ext)s"
            : section != "" ? "%(title).150B [%(id)s] [" + normalizedSection.Substring(1) + "].%(ext)s" : "%(title).150B [%(id)s].%(ext)s";
        var args = new List<string> { "--ignore-config", playlist ? "--yes-playlist" : "--no-playlist", "--no-simulate", "--progress", "--newline", "--no-warnings", "--windows-filenames",
            "--socket-timeout", "30", "--retries", "3", "--fragment-retries", "3", "--ffmpeg-location", Path.Combine(AppDir, "bin", "ffmpeg"),
            "--paths", staging ?? Text(job, "downloadDir"), "--output", output,
            "--progress-template", (playlist ? "download:SGPROGRESS:%(info.playlist_index)s/%(info.n_entries)s:%(progress._percent_str)s" : "download:SGPROGRESS:%(progress._percent_str)s") + "|SPEED:%(progress.speed)s|STATUS:%(progress.status)s",
            "--progress-template", "postprocess:SGPOSTPROCESS",
            "--print", "after_move:SGFILEJ:%(filepath)j", "--print", "after_move:SGTITLEJ:%(title)j",
            "--print", "after_move:SGSUBSJ:%(requested_subtitles)j" };
        if (playlist)
        {
            args.AddRange(new[] { "--playlist-items", items == "" ? "1-200" : items, "--max-downloads", "200", "--ignore-errors",
                "--print", "before_dl:SGIDJ:%(id)j", "--print", "playlist:SGPLTITLEJ:%(title)j" });
        }
        if (section != "")
        {
            args.AddRange(new[] { "--download-sections", normalizedSection });
            if (media == "video") args.Add("--force-keyframes-at-cuts");
        }
        if (sponsorblock == "sponsor") args.AddRange(new[] { "--sponsorblock-remove", "sponsor" });
        if (sponsorblock == "all") args.AddRange(new[] { "--sponsorblock-remove", SponsorAll });
        if (live == "from-start") args.AddRange(new[] { "--live-from-start", "--wait-for-video", "15" });
        if (media == "video")
        {
            string filter = height == "best" ? "" : "[height<=" + height + "]";
            args.AddRange(new[] { "--format", "bv*" + filter + "+ba/b" + filter,
                "--merge-output-format", "mp4", "--remux-video", "mp4" });
        }
        else
        {
            args.AddRange(new[] { "-f", "ba/b" });
            if (audioQuality == "auto" && media != "audio-original") args.AddRange(new[] { "-x", "--audio-format", media == "audio-mp3" ? "mp3" : "m4a", "--audio-quality", "0" });
        }
        if (subtitles != "off")
        {
            args.Add("--write-subs");
            if (subtitles == "auto") args.Add("--write-auto-subs");
            args.AddRange(new[] { "--sub-langs", subLangs, "--convert-subs", "srt" });
        }
        if (metadata) args.AddRange(new[] { "--embed-metadata", "--embed-chapters" });
        ValidatePracticalOptions(job);
        if (Flag(job, "embedThumbnail"))
        {
            args.AddRange(staging == null ? new[] { "--embed-thumbnail", "--convert-thumbnails", "jpg" } : new[] { "--write-thumbnail", "--convert-thumbnails", "jpg" });
        }
        if (Flag(job, "splitChapters"))
        {
            string chapter = Path.Combine(staging ?? Text(job, "downloadDir"), output.Replace(".%(ext)s", ".chapters/%(section_number)03d - %(section_title).80B.%(ext)s"));
            args.AddRange(new[] { "--split-chapters", "--output", "chapter:" + chapter, "--print", "after_move:SGCHAPTERSJ:%(chapters.:.filepath)j" });
        }
        if (Flag(job, "downloadArchive")) args.AddRange(new[] { "--download-archive", Path.Combine(Jobs, Text(job, "id") + ".archive"), "--no-quiet" });
        if (Text(job, "limitRate", "off") != "off") args.AddRange(new[] { "--limit-rate", Text(job, "limitRate") });
        int fragments = TransferConcurrency(job, config);
        if (fragments > 1) args.AddRange(new[] { "--concurrent-fragments", fragments.ToString(CultureInfo.InvariantCulture) });
        args.Add("--paths"); args.Add("temp:" + Path.Combine(Jobs, Text(job, "id") + ".temp"));
        args.Add("--proxy"); args.Add(proxy);
        if (cookieFile != "") { args.Add("--cookies"); args.Add(cookieFile); }
        args.Add("--"); args.Add(Text(job, "url"));
        return args;
    }
    static bool CompletedOutput(Dictionary<string, object> job, string path)
    {
        try
        {
            if (string.IsNullOrEmpty(path) || path.Any(char.IsControl) || !Path.IsPathRooted(path)) return false;
            string full = Path.GetFullPath(path), root = Path.GetFullPath(Text(job, "downloadDir")).TrimEnd('\\', '/') + "\\";
            return full.StartsWith(root, StringComparison.OrdinalIgnoreCase) && File.Exists(full)
                && (File.GetAttributes(full) & FileAttributes.Directory) == 0;
        }
        catch { return false; }
    }
    static bool FileMarker(Dictionary<string, object> job, string line, out bool producedFile)
    {
        producedFile = false;
        try
        {
            if (line.StartsWith("SGCHAPTERSJ:") && line.Length <= MaxMessage)
            {
                foreach (string path in Json.Deserialize<string[]>(line.Substring(12)) ?? new string[0])
                    if (CompletedOutput(job, path) && AddCompletedFile(job, path)) producedFile = true;
                return true;
            }
            if (line.StartsWith("SGFILE:") || (line.StartsWith("SGFILEJ:") && line.Length <= MaxMessage))
            {
                string path = line.StartsWith("SGFILE:") ? line.Substring(7).Trim() : Json.Deserialize<string>(line.Substring(8));
                producedFile = CompletedOutput(job, path);
                if (producedFile) AddCompletedFile(job, path);
            }
            else if ((line.StartsWith("SGTITLEJ:") || line.StartsWith("SGPLTITLEJ:")) && line.Length <= MaxMessage)
                job["title"] = SafeTitle(Json.Deserialize<string>(line.Substring(line.StartsWith("SGPLTITLEJ:") ? 11 : 9)));
            else if (line.StartsWith("SGIDJ:") && line.Length <= MaxMessage)
            {
                string itemId = Json.Deserialize<string>(line.Substring(6));
                if (!Regex.IsMatch(itemId ?? "", @"\A[A-Za-z0-9_-]{1,128}\z")) return false;
                object value; var itemIds = new List<string>();
                if (job.TryGetValue("_itemIds", out value) && value is System.Collections.IEnumerable && !(value is string))
                    foreach (object item in (System.Collections.IEnumerable)value) if (item is string) itemIds.Add((string)item);
                if (!itemIds.Contains(itemId, StringComparer.OrdinalIgnoreCase)) itemIds.Add(itemId);
                job["_itemIds"] = itemIds;
            }
            else if (line.StartsWith("SGSUBSJ:") && line.Length <= MaxMessage)
            {
                var subtitles = Json.Deserialize<Dictionary<string, object>>(line.Substring(8));
                if (subtitles != null)
                    foreach (object value in subtitles.Values)
                    {
                        var subtitle = value as Dictionary<string, object>;
                        if (subtitle != null) AddCompletedFile(job, Text(subtitle, "filepath"));
                    }
            }
            else return false;
        }
        catch { }
        return true;
    }

    static string AudioFile(string directory, string path)
    {
        var candidate = new Dictionary<string, object> { { "downloadDir", directory }, { "files", new[] { path } } };
        List<string> files = SafeFiles(candidate);
        if (files.Count != 1 || new FileInfo(files[0]).Length == 0)
            throw new InvalidOperationException("音频输出无效。");
        return files[0];
    }
    static void ThrowIfCanceled(Dictionary<string, object> job)
    {
        if (File.Exists(CancelFile(Text(job, "id")))) throw new OperationCanceledException(CanceledError);
    }
    static bool WaitForJobProcess(Process process, Dictionary<string, object> job, int timeout)
    {
        DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeout);
        while (!process.WaitForExit(200))
        {
            if (File.Exists(CancelFile(Text(job, "id"))))
            {
                KillProcessTree(process.Id);
                process.WaitForExit(5000);
                throw new OperationCanceledException(CanceledError);
            }
            if (DateTime.UtcNow >= deadline)
            {
                KillProcessTree(process.Id);
                process.WaitForExit(5000);
                return false;
            }
        }
        process.WaitForExit();
        ThrowIfCanceled(job);
        return true;
    }
    static string OriginalAudioExtension(string source, bool metadata, Dictionary<string, object> job)
    {
        try
        {
            ThrowIfCanceled(job);
            var args = new[] { "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "json", source };
            var psi = new ProcessStartInfo { FileName = Path.Combine(AppDir, "bin", "ffmpeg", "ffprobe.exe"),
                Arguments = string.Join(" ", args.Select(Quote)), UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8 };
            var output = new StringBuilder(); bool oversized = false;
            using (var proc = new Process { StartInfo = psi })
            {
                proc.OutputDataReceived += (s, e) => {
                    if (e.Data == null) return;
                    if (output.Length + e.Data.Length > MaxMessage) oversized = true;
                    else output.AppendLine(e.Data);
                };
                proc.ErrorDataReceived += (s, e) => { };
                proc.Start(); proc.BeginOutputReadLine(); proc.BeginErrorReadLine();
                if (!WaitForJobProcess(proc, job, 30000)) throw new InvalidOperationException();
                if (proc.ExitCode != 0 || oversized) throw new InvalidOperationException();
            }
            var result = Json.Deserialize<Dictionary<string, object>>(output.ToString());
            object streams; Dictionary<string, object> audio = null;
            if (result != null && result.TryGetValue("streams", out streams) && streams is System.Collections.IEnumerable && !(streams is string))
                foreach (object stream in (System.Collections.IEnumerable)streams) { audio = stream as Dictionary<string, object>; break; }
            string codec = Text(audio, "codec_name");
            if (codec == "") throw new InvalidOperationException();
            switch (codec)
            {
                case "aac": case "alac": return ".m4a";
                case "opus": return ".opus";
                case "mp3": return ".mp3";
                case "flac": return ".flac";
                case "vorbis": return ".ogg";
                case "ac3": return metadata ? ".mka" : ".ac3";
                case "eac3": return metadata ? ".mka" : ".eac3";
                default: return ".mka";
            }
        }
        catch (OperationCanceledException) { throw; }
        catch { throw new InvalidOperationException(AudioProbeError); }
    }
    static void EncodeAudio(Dictionary<string, object> job, List<string> sources, string staging)
    {
        if (sources.Count == 0) throw new InvalidOperationException("没有音频源文件。");
        string downloadRoot = LocalDirectory(Text(job, "downloadDir"));
        bool original = Text(job, "media") == "audio-original";
        string quality = StoredAudioQuality(job), extension = Text(job, "media") == "audio-mp3" ? ".mp3" : ".m4a";
        foreach (string source in sources)
        {
            ThrowIfCanceled(job);
            string validatedSource = AudioFile(staging, source);
            string root = downloadRoot;
            if (StoredPlaylist(job) || Path.GetDirectoryName(validatedSource).EndsWith(".chapters", StringComparison.OrdinalIgnoreCase))
            {
                string relative = Path.GetDirectoryName(validatedSource).Substring(Path.GetFullPath(staging).TrimEnd('\\', '/').Length).TrimStart('\\', '/');
                root = Path.Combine(downloadRoot, relative);
                Directory.CreateDirectory(root);
                LocalDirectory(root);
            }
            if (original) extension = OriginalAudioExtension(source, StoredMetadata(job), job);
            ThrowIfCanceled(job);
            string output = Path.Combine(staging, "encoded-" + Guid.NewGuid().ToString("N") + extension);
            string map = StoredMetadata(job) ? "0" : "-1";
            var args = new List<string> { "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-i", source,
                "-map", "0:a:0", "-vn", "-c:a", original ? "copy" : extension == ".mp3" ? "libmp3lame" : "aac" };
            if (Flag(job, "embedThumbnail"))
            {
                string image = Path.ChangeExtension(source, ".jpg");
                string parent = Path.GetDirectoryName(source);
                if (!File.Exists(image) && parent.EndsWith(".chapters", StringComparison.OrdinalIgnoreCase)) image = parent.Substring(0, parent.Length - 9) + ".jpg";
                if (File.Exists(image))
                {
                    image = AudioFile(staging, image);
                    args.Remove("-vn");
                    args.InsertRange(7, new[] { "-i", image });
                    args.AddRange(new[] { "-map", "1:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic" });
                }
                else AddWarning(job, "没有可用封面，媒体已保存。");
            }
            if (!original) args.AddRange(new[] { "-b:a", quality + "k" });
            args.AddRange(new[] { "-map_metadata", map, "-map_metadata", StoredMetadata(job) ? "0:s:a:0" : "-1", "-map_chapters", map, output });
            var psi = new ProcessStartInfo { FileName = Path.Combine(AppDir, "bin", "ffmpeg", "ffmpeg.exe"),
                Arguments = string.Join(" ", args.Select(Quote)), UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = staging };
            using (var proc = new Process { StartInfo = psi })
            {
                proc.OutputDataReceived += (s, e) => { };
                proc.ErrorDataReceived += (s, e) => { };
                proc.Start(); proc.BeginOutputReadLine(); proc.BeginErrorReadLine();
                if (!WaitForJobProcess(proc, job, 7200000)) throw new InvalidOperationException("音频编码超时。");
                if (proc.ExitCode != 0) throw new InvalidOperationException(original ? AudioCopyError : "音频编码失败。");
            }
            AudioFile(staging, output);
            string name = Path.GetFileNameWithoutExtension(source) + " [" + (original ? "original" : quality + "k") + "]";
            for (int collision = 0; ; collision++)
            {
                LocalDirectory(root);
                string destination = Path.Combine(root, name + (collision == 0 ? "" : " (" + collision + ")") + extension);
                if (File.Exists(destination) || Directory.Exists(destination)) continue;
                ThrowIfCanceled(job);
                try { File.Move(output, destination); }
                catch (IOException) { if (File.Exists(destination) || Directory.Exists(destination)) continue; throw; }
                AddCompletedFile(job, AudioFile(root, destination));
                break;
            }
        }
    }
    static void CleanupAudioStage(string staging)
    {
        if (staging == null) return;
        try
        {
            LocalDirectory(staging);
            Directory.Delete(staging, true);
        }
        catch { }
    }

    static bool ProgressValue(string line, bool playlist, out double percent)
    {
        percent = 0;
        Match match = playlist
            ? Regex.Match(line, @"\ASGPROGRESS:([0-9]+)/([0-9]+):\s*([0-9]+(?:\.[0-9]+)?)%")
            : Regex.Match(line, @"\ASGPROGRESS:\s*([0-9]+(?:\.[0-9]+)?)%");
        if (!match.Success) return playlist && ProgressValue(line, false, out percent);
        double value;
        if (playlist)
        {
            double index, total, item;
            if (!double.TryParse(match.Groups[1].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out index)
                || !double.TryParse(match.Groups[2].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out total)
                || !double.TryParse(match.Groups[3].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out item)
                || total <= 0) return false;
            value = ((index - 1) + Math.Max(0, Math.Min(100, item)) / 100.0) / total * 100.0;
        }
        else if (!double.TryParse(match.Groups[1].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out value)) return false;
        percent = Math.Max(0, Math.Min(99, value));
        return true;
    }

    static bool UpdateProgress(Dictionary<string, object> job, string line, bool playlist)
    {
        if (!line.StartsWith("SGPROGRESS:", StringComparison.Ordinal) && line != "SGPOSTPROCESS") return false;
        double percent;
        if (ProgressValue(line, playlist, out percent)) job["percent"] = percent;
        Match match = Regex.Match(line, @"\|SPEED:([^|]+)\|STATUS:downloading\z");
        double speed;
        job["speedBytesPerSecond"] = match.Success
            && double.TryParse(match.Groups[1].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out speed)
            && !double.IsNaN(speed) && !double.IsInfinity(speed) && speed >= 0 ? (object)speed : null;
        job["speedUpdated"] = DateTime.UtcNow.Ticks;
        SaveJob(job);
        return true;
    }

    static double? DownloadSpeed(Dictionary<string, object> job)
    {
        object value, updated;
        if (Text(job, "state") != "downloading" || !job.TryGetValue("speedBytesPerSecond", out value)
            || !(value is int || value is long || value is double || value is decimal)
            || !job.TryGetValue("speedUpdated", out updated) || !(updated is long)) return null;
        double age = (DateTime.UtcNow.Ticks - (long)updated) / 10000000.0;
        double speed = Convert.ToDouble(value, CultureInfo.InvariantCulture);
        return age >= 0 && age <= 5 && !double.IsNaN(speed) && !double.IsInfinity(speed) && speed >= 0 ? (double?)speed : null;
    }

    static int LiveLimitSeconds(int minutes)
    {
#if BRIDGE_TEST
        int seconds;
        if (int.TryParse(Environment.GetEnvironmentVariable("SG_TEST_LIVE_SECONDS"), out seconds) && seconds > 0) return seconds;
#endif
        return checked(minutes * 60);
    }

    static string PromoteLivePart(string temporary, string outputRoot, Dictionary<string, object> job)
    {
        foreach (string file in SnapshotFiles(temporary))
        {
            if (!file.EndsWith(".part", StringComparison.OrdinalIgnoreCase) || file.IndexOf(".part-", StringComparison.OrdinalIgnoreCase) >= 0) continue;
            string destination = Path.Combine(outputRoot, Path.GetFileName(file).Substring(0, Path.GetFileName(file).Length - 5));
            if (File.Exists(destination) || Directory.Exists(destination)) continue;
            ThrowIfCanceled(job);
            try { AudioFile(temporary, file); LocalDirectory(outputRoot); File.Move(file, destination); return destination; } catch { }
        }
        return "";
    }

    static void Worker(string id)
    {
        Dictionary<string, object> job = ReadJob(id);
        if (Terminal(Text(job, "state"))) return;
        string staging = null;
        string downloadRoot = Text(job, "downloadDir");
        string temporary = Path.Combine(Jobs, id + ".temp");
        string cookieFile = Path.Combine(Jobs, id + ".cookies.txt");
        if (!File.Exists(cookieFile)) cookieFile = "";
        try
        {
            bool playlist = StoredPlaylist(job);
            string platform;
            Canonical(Text(job, "url"), playlist, out platform);
            string media, audioQuality, subtitles, subLangs; bool metadata;
            ValidateOptions(job, platform, out media, out audioQuality, out subtitles, out subLangs, out metadata);
            string sponsorblock, section, normalizedSection, items, live; int liveMinutes;
            ValidateFeatureOptions(job, platform, out sponsorblock, out section, out normalizedSection, out playlist, out items, out live, out liveMinutes);
            RequireAudioProbe(media);
            if (File.Exists(CancelFile(id))) { FinalizeCanceled(job); return; }
            var cfg = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(AppDir, "config.json"), Encoding.UTF8));
            string proxy = Proxy(cfg);
            Directory.CreateDirectory(Text(job, "downloadDir"));
            Directory.CreateDirectory(temporary); ProtectDirectory(temporary);
            if (audioQuality != "auto" || media == "audio-original")
            {
                LocalDirectory(Text(job, "downloadDir"));
                staging = Path.Combine(Jobs, id + ".audio-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(staging); ProtectDirectory(staging);
                job["files"] = new List<string>();
            }
            job["pid"] = Process.GetCurrentProcess().Id; job["state"] = "downloading"; SaveJob(job);
            var args = DownloadArguments(job, proxy, cookieFile, staging, cfg);
            SyncArchive(job, false);
            var sources = new List<string>();
            var encodedSources = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            bool producedFile = false, invalidSource = false, skippedArchive = false;
            string error = platform == "x" ? "视频不可访问或网络请求失败，请检查代理及 X 登录状态。" : "视频不可访问或网络请求失败，请检查代理及 YouTube 登录状态。";
            var psi = new ProcessStartInfo { FileName = Engine(), Arguments = string.Join(" ", args.Select(Quote)),
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8, WorkingDirectory = AppDir };
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            using (var proc = new Process { StartInfo = psi })
            {
                proc.OutputDataReceived += (s, e) => {
                    if (e.Data == null) return;
                    lock (StateLock)
                    {
                        if (Flag(job, "downloadArchive") && e.Data.Contains("has already been recorded in the archive")) skippedArchive = true;
                        if (UpdateProgress(job, e.Data, playlist)) return;
                        if (staging != null && (e.Data.StartsWith("SGFILEJ:") || e.Data.StartsWith("SGFILE:") || e.Data.StartsWith("SGCHAPTERSJ:")))
                        {
                            try
                            {
                                if (e.Data.Length > MaxMessage) throw new InvalidOperationException();
                                var paths = e.Data.StartsWith("SGCHAPTERSJ:")
                                    ? (e.Data.Substring(12).TrimStart().StartsWith("[") ? Json.Deserialize<string[]>(e.Data.Substring(12)) : new string[0])
                                    : new[] { e.Data.StartsWith("SGFILEJ:") ? Json.Deserialize<string>(e.Data.Substring(8)) : e.Data.Substring(7).Trim() };
                                foreach (string path in paths ?? new string[0])
                                {
                                    string source = AudioFile(staging, path);
                                    if (!sources.Contains(source, StringComparer.OrdinalIgnoreCase)) sources.Add(source);
                                }
                            }
                            catch { invalidSource = true; }
                        }
                        else
                        {
                            bool outputFile;
                            if (FileMarker(job, e.Data, out outputFile)) { producedFile |= outputFile; SaveJob(job); }
                        }
                    }
                };
                proc.ErrorDataReceived += (s, e) => {
                    string line = e.Data ?? "";
                    lock (StateLock) { if (UpdateProgress(job, line, playlist)) return; }
                    if (line.IndexOf("login", StringComparison.OrdinalIgnoreCase) >= 0 || line.IndexOf("authenticate", StringComparison.OrdinalIgnoreCase) >= 0)
                        error = platform == "x"
                            ? "X 要求登录。请在 Tabbit 登录 X 后刷新独立的 X Cookie。"
                            : "YouTube 要求登录或验证。请确认浏览器中的 YouTube 登录状态后刷新 Cookie。";
                    else if (line.IndexOf("No video", StringComparison.OrdinalIgnoreCase) >= 0 || line.IndexOf("No video could", StringComparison.OrdinalIgnoreCase) >= 0)
                        error = "这条推文没有可下载的视频。";
                };
                proc.Start(); proc.BeginOutputReadLine(); proc.BeginErrorReadLine();
                DateTime started = DateTime.UtcNow;
                DateTime deadline = live == "from-start"
                    ? started.AddSeconds(LiveLimitSeconds(liveMinutes))
                    : started.AddHours(2);
                bool canceled = false, liveCap = false, timedOut = false;
                try
                {
                    while (!proc.HasExited)
                    {
                        if (File.Exists(CancelFile(id))) { canceled = true; KillProcessTree(proc.Id); break; }
                        if (live == "from-start" && DateTime.UtcNow >= deadline) { liveCap = true; KillProcessTree(proc.Id); break; }
                        if (live != "from-start" && DateTime.UtcNow >= deadline) { timedOut = true; KillProcessTree(proc.Id); break; }
                        if (playlist && staging != null)
                        {
                            List<string> pending;
                            lock (StateLock) { pending = sources.Where(source => !encodedSources.Contains(source)).ToList(); }
                            foreach (string source in pending)
                            {
                                EncodeAudio(job, new List<string> { source }, staging);
                                encodedSources.Add(source);
                                lock (StateLock) { producedFile = true; SaveJob(job); }
                            }
                        }
                        proc.WaitForExit(200);
                    }
                }
                catch
                {
                    KillProcessTree(proc.Id);
                    proc.WaitForExit(5000);
                    throw;
                }
                if (canceled || liveCap || timedOut)
                {
                    try { if (!proc.HasExited) proc.WaitForExit(5000); } catch { }
                }
                else
                {
                    proc.WaitForExit();
                    lock (StateLock) { job["speedBytesPerSecond"] = null; SaveJob(job); }
                }
                if (canceled)
                {
                    job["state"] = "canceled";
                    job["error"] = CanceledError;
                    job["files"] = SafeFiles(job);
                }
                else if (liveCap)
                {
                    string livePart = PromoteLivePart(temporary, staging ?? downloadRoot, job);
                    if (staging != null && livePart != "")
                    {
                        sources.Add(livePart);
                        EncodeAudio(job, sources, staging);
                    }
                    else if (livePart != "") AddCompletedFile(job, livePart);
                    job["files"] = SafeFiles(job);
                    bool hasOutput = StoredFileCount(job) > 0;
                    job["state"] = hasOutput ? "completed" : "failed";
                    job["error"] = hasOutput ? "" : LiveCapError;
                    if (hasOutput) { AddWarning(job, "已达到直播录制时长上限。"); job["percent"] = 100.0; }
                }
                else if (timedOut)
                {
                    job["state"] = "failed";
                    job["error"] = "下载超过两小时，已停止。";
                }
                else
                {
                    ThrowIfCanceled(job);
                    int exitCode = proc.ExitCode;
                    bool success = exitCode == 0;
                    if (staging != null && (success || (playlist && (producedFile || sources.Count > 0))))
                    {
                        if (invalidSource) throw new InvalidOperationException("音频源路径无效。");
                        var pending = sources.Where(source => !encodedSources.Contains(source)).ToList();
                        if (pending.Count > 0) EncodeAudio(job, pending, staging);
                        producedFile = StoredFileCount(job) > 0;
                    }
                    if (playlist && exitCode != 0 && producedFile) AddWarning(job, "部分条目下载失败。");
                    job["files"] = SafeFiles(job);
                    bool complete = (producedFile || skippedArchive) && (exitCode == 0 || playlist);
                    job["state"] = complete ? "completed" : "failed";
                    job["error"] = complete ? "" : error;
                    if (complete) job["percent"] = 100.0;
                    if (complete && skippedArchive) AddWarning(job, "视频已存在，已跳过重复下载。");
                    if (complete && exitCode == 0) SyncArchive(job, true);
                }
                if (Text(job, "state") == "canceled") RemoveCancelArtifacts(id, cookieFile);
            }
        }
        catch (OperationCanceledException)
        {
            job["state"] = "canceled";
            job["error"] = CanceledError;
            job["files"] = SafeFiles(job);
        }
#if BRIDGE_TEST
        catch (Exception exception) {
            Atomic(Path.Combine(Jobs, id + ".test-error.txt"), exception.ToString());
#else
        catch (Exception exception) {
#endif
            job["state"] = "failed";
            job["error"] = Text(job, "media") == "audio-original" && new[] { MissingAudioProbe, AudioProbeError, AudioCopyError }.Contains(exception.Message)
                ? exception.Message : "本机下载进程失败，请检查下载目录、内核与代理设置。";
        }
        finally
        {
            CleanupAudioStage(staging);
            CleanupAudioStage(temporary);
            try { if (cookieFile != "") File.Delete(cookieFile); } catch { }
            try { File.Delete(Path.Combine(Jobs, id + ".archive")); } catch { }
            if (Text(job, "state") == "canceled") RemoveCancelArtifacts(id, cookieFile);
            lock (StateLock) { job.Remove("proxy"); job.Remove("cookieFile"); SaveJob(job); }
        }
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
            catch (IOException) { if (attempt == 3) throw; Thread.Sleep(25 * (attempt + 1)); }
        }
    }
    static void SaveJob(Dictionary<string, object> job) { if (Text(job, "state") != "downloading") job["speedBytesPerSecond"] = null; job["updated"] = DateTime.UtcNow.Ticks; Atomic(JobFile(Text(job, "id")), Json.Serialize(job)); }
    static double Age(Dictionary<string, object> job)
    {
        object v; return job.TryGetValue("updated", out v) ? (DateTime.UtcNow.Ticks - Convert.ToInt64(v)) / 10000000.0 : double.MaxValue;
    }
    static bool WorkerAlive(Dictionary<string, object> job)
    {
        object value;
        try { if (!job.TryGetValue("pid", out value) || Convert.ToInt32(value) == 0) return false;
            using (var p = Process.GetProcessById(Convert.ToInt32(value))) { return !p.HasExited && p.ProcessName == Path.GetFileNameWithoutExtension(typeof(ShiguangBridge).Assembly.Location); } }
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
