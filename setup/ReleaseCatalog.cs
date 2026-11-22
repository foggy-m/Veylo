using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

namespace VeyloSetup
{
    internal static class ReleaseCatalog
    {
        internal const string FluentRepo = "SakuraForgot/FluentYTDL";
        internal const string BiliRepo = "ScottSloan/Bili23-Downloader";
        internal const string EngineRepo = "yt-dlp/yt-dlp";
        internal const string VeyloRepo = "foggy-m/Veylo";
        internal const string CatalogFile = "Veylo-verified-components.json";

        internal sealed class Release
        {
            public string tag_name { get; set; }
            public bool draft { get; set; }
            public bool prerelease { get; set; }
            public Asset[] assets { get; set; }

            public string Version { get { return tag_name.TrimStart('v'); } }

            internal string AssetUrl(string repository, string file)
            {
                return "https://github.com/" + repository + "/releases/download/" + tag_name + "/" + file;
            }
        }

        internal sealed class Asset
        {
            public string name { get; set; }
            public string state { get; set; }
            public string browser_download_url { get; set; }
            public long size { get; set; }
            public string digest { get; set; }
        }

        internal sealed class VerifiedComponents
        {
            public int Schema { get; set; }
            public string YouTubeBridge { get; set; }
            public string BiliBridge { get; set; }
            public Dependency Fluent { get; set; }
            public Dependency Bili { get; set; }
        }

        internal static Release Latest(Download download, string repository, CancellationToken cancel)
        {
            return ValidateRelease(new JavaScriptSerializer().Deserialize<Release>(download.ReadRelease(repository, cancel)), repository);
        }

        internal static Release LatestFromTag(Download download, string repository, CancellationToken cancel)
        {
            return ValidateRelease(new Release { tag_name = download.LatestTag(repository, cancel) }, repository);
        }

        private static Release ValidateRelease(Release release, string repository)
        {
            string expression = repository == EngineRepo ? @"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$" : @"^v\d{1,4}\.\d{1,4}\.\d{1,4}$";
            if (release == null || release.draft || release.prerelease ||
                !Regex.IsMatch(release.tag_name ?? "", expression, RegexOptions.CultureInvariant) ||
                (repository == EngineRepo && !EngineVersion(release.Version)))
                throw new IOException("官方发布版本的格式无法识别。");
            return release;
        }

        internal static Dependency EngineAsset(Release release, Download download, CancellationToken cancel)
        {
            const long limit = 128 * 1024 * 1024;
            const string file = "yt-dlp.exe";
            string name = "yt-dlp " + release.Version;
            var api = AssetFor(release, EngineRepo, file, name, limit);
            if (api != null) return api;
            string sums = download.ReadAssetText(release.AssetUrl(EngineRepo, "SHA2-256SUMS"), 16 * 1024, cancel);
            var lines = Regex.Matches(sums, @"(?m)^([a-f0-9]{64})[ \t]+\*?yt-dlp\.exe[ \t]*\r?$");
            if (lines.Count != 1) return null;
            string url = release.AssetUrl(EngineRepo, file);
            var fallback = new Dependency
            {
                Name = name, Version = release.Version, Filename = file, Url = url,
                Sha256 = lines[0].Groups[1].Value, Size = download.AssetLength(url, cancel)
            };
            return ValidAsset(fallback, EngineRepo, release.tag_name, file, limit) ? fallback : null;
        }

        internal static Dependency AssetFor(Release release, string repository, string filename, string name, long maxSize)
        {
            var matches = (release.assets ?? new Asset[0]).Where(file => file != null && file.name == filename).ToArray();
            var asset = matches.Length == 1 ? matches[0] : null;
            var dependency = asset == null ? null : new Dependency
            {
                Name = name,
                Version = release.Version,
                Filename = asset.name,
                Sha256 = (asset.digest ?? "").StartsWith("sha256:", StringComparison.Ordinal) ? asset.digest.Substring(7) : "",
                Size = asset.size,
                Url = asset.browser_download_url
            };
            return asset != null && asset.state == "uploaded" &&
                ValidAsset(dependency, repository, release.tag_name, filename, maxSize) ? dependency : null;
        }

        internal static bool ValidVendor(Dependency dependency, string repository)
        {
            Version version;
            if (dependency == null || !Version.TryParse(dependency.Version, out version) || version.Revision != -1 || version.Build < 0 ||
                !Regex.IsMatch(dependency.Version ?? "", @"^\d{1,4}\.\d{1,4}\.\d{1,4}$")) return false;
            string filename = repository == FluentRepo ? "FluentYTDL-" + dependency.Version + "-win64-full.7z"
                : "Bili23-Downloader_" + dependency.Version + "_windows_x64_portable.zip";
            return ValidAsset(dependency, repository, "v" + dependency.Version, filename, 1024L * 1024 * 1024);
        }

        private static bool ValidAsset(Dependency dependency, string repository, string tag, string filename, long maxSize)
        {
            return dependency.Filename == filename && dependency.Size > 0 && dependency.Size <= maxSize
                && Regex.IsMatch(dependency.Sha256 ?? "", @"\A[a-f0-9]{64}\z", RegexOptions.CultureInvariant)
                && dependency.Url == "https://github.com/" + repository + "/releases/download/" + tag + "/" + filename;
        }

        internal static bool Newer(string candidate, string current)
        {
            Version a, b;
            return Version.TryParse(candidate, out a) && Version.TryParse(current, out b) && a > b;
        }

        internal static bool EngineVersion(string version)
        {
            DateTime date;
            return DateTime.TryParseExact(version, "yyyy.MM.dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out date);
        }
    }
}
