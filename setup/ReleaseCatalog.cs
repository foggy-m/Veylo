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
            var release = new JavaScriptSerializer().Deserialize<Release>(download.ReadRelease(repository, cancel));
            string expression = repository == EngineRepo ? @"^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$" : @"^v\d{1,4}\.\d{1,4}\.\d{1,4}$";
            if (release == null || release.draft || release.prerelease ||
                !Regex.IsMatch(release.tag_name ?? "", expression, RegexOptions.CultureInvariant) ||
                (repository == EngineRepo && !EngineVersion(release.Version)))
                throw new IOException("官方发布版本的格式无法识别。");
            return release;
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
