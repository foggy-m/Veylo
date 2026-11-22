using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Threading;

namespace VeyloSetup
{
    internal sealed class Dependency
    {
        public string Name { get; set; }
        public string Version { get; set; }
        public string Filename { get; set; }
        public string Url { get; set; }
        public long Size { get; set; }
        public string Sha256 { get; set; }
    }

    internal sealed class Download : IDisposable
    {
        private readonly HttpClient http = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false })
        {
            Timeout = TimeSpan.FromSeconds(60)
        };
        private readonly Action<string, int> progress;

        public Download(Action<string, int> progress)
        {
            this.progress = progress;
            http.DefaultRequestHeaders.UserAgent.ParseAdd("Veylo-Setup/2.4.5");
        }

        internal static string Hash(Stream input)
        {
            using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
        }

        public string ReadRelease(string repository, CancellationToken cancel)
        {
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancel))
            {
                deadline.CancelAfter(TimeSpan.FromSeconds(12));
                using (var request = new HttpRequestMessage(HttpMethod.Get,
                    "https://api.github.com/repos/" + repository + "/releases/latest"))
                {
                    request.Headers.Accept.ParseAdd("application/vnd.github+json");
                    using (var response = http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, deadline.Token).GetAwaiter().GetResult())
                        return ReadText(response, 1024 * 1024, deadline.Token);
                }
            }
        }

        public string LatestTag(string repository, CancellationToken cancel)
        {
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancel))
            {
                deadline.CancelAfter(TimeSpan.FromSeconds(12));
                var page = new Uri("https://github.com/" + repository + "/releases/latest");
                using (var response = http.GetAsync(page, HttpCompletionOption.ResponseHeadersRead, deadline.Token).GetAwaiter().GetResult())
                {
                    if (!Redirect(response) || response.Headers.Location == null)
                        throw new IOException("无法从官方发布页取得版本。");
                    var redirect = new Uri(page, response.Headers.Location);
                    string prefix = "https://github.com/" + repository + "/releases/tag/";
                    if (!redirect.AbsoluteUri.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) || redirect.Query != "" || redirect.Fragment != "")
                        throw new IOException("官方发布页返回了意外的版本地址。");
                    return redirect.AbsoluteUri.Substring(prefix.Length);
                }
            }
        }

        public string ReadAssetText(string url, int limit, CancellationToken cancel)
        {
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancel))
            {
                deadline.CancelAfter(TimeSpan.FromSeconds(18));
                using (var response = Open(new Uri(url), HttpMethod.Get, 0, deadline.Token))
                    return ReadText(response, limit, deadline.Token);
            }
        }

        public long AssetLength(string url, CancellationToken cancel)
        {
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancel))
            {
                deadline.CancelAfter(TimeSpan.FromSeconds(18));
                using (var response = Open(new Uri(url), HttpMethod.Head, 0, deadline.Token))
                {
                    response.EnsureSuccessStatusCode();
                    return response.Content.Headers.ContentLength ?? 0;
                }
            }
        }

        private static string ReadText(HttpResponseMessage response, int limit, CancellationToken cancel)
        {
            response.EnsureSuccessStatusCode();
            using (var input = response.Content.ReadAsStreamAsync().GetAwaiter().GetResult())
            using (var output = new MemoryStream())
            using (cancel.Register(input.Dispose))
            {
                var bytes = new byte[8192];
                int count;
                while ((count = input.ReadAsync(bytes, 0, bytes.Length, cancel).GetAwaiter().GetResult()) > 0)
                {
                    if (output.Length + count > limit) throw new IOException("GitHub 版本清单过大。");
                    output.Write(bytes, 0, count);
                }
                return System.Text.Encoding.UTF8.GetString(output.ToArray());
            }
        }

        private static bool Matches(string path, Dependency dependency)
        {
            if (!File.Exists(path)) return false;
            Files.PlainPath(path);
            if (new FileInfo(path).Length != dependency.Size) return false;
            using (var input = File.OpenRead(path)) return Hash(input) == dependency.Sha256;
        }

        public string Fetch(Dependency dependency, string folder, CancellationToken cancel)
        {
            string file = Files.Inside(folder, dependency.Filename);
            string partial = file + ".part";
            Files.PlainPath(file);
            Files.PlainPath(partial);
            if (Matches(file, dependency)) return file;
            if (File.Exists(file)) File.Delete(file); // This is the versioned installer cache, never a user file.
            for (int attempt = 1; attempt <= 3; attempt++)
            {
                cancel.ThrowIfCancellationRequested();
                try
                {
                    if (!Matches(partial, dependency))
                    {
                        if (File.Exists(partial) && new FileInfo(partial).Length >= dependency.Size) File.Delete(partial);
                        Receive(dependency, partial, cancel);
                    }
                    if (!Matches(partial, dependency))
                    {
                        File.Delete(partial);
                        throw new IOException("校验不匹配");
                    }
                    File.Move(partial, file);
                    progress(dependency.Name + " 已下载并校验", 100);
                    return file;
                }
                catch (Exception error)
                {
                    if (!(error is IOException || error is HttpRequestException || error is OperationCanceledException || error is ObjectDisposedException)) throw;
                    cancel.ThrowIfCancellationRequested();
                    if (attempt == 3)
                        throw new InvalidOperationException("无法下载或校验 " + dependency.Name + "。请检查 GitHub 网络连接，再点击重试。", error);
                    progress(dependency.Name + " 下载中断，正在重试", -1);
                    if (cancel.WaitHandle.WaitOne(TimeSpan.FromSeconds(attempt * 2))) cancel.ThrowIfCancellationRequested();
                }
            }
            throw new InvalidOperationException("下载未完成。");
        }

        private static bool Redirect(HttpResponseMessage response)
        {
            int status = (int)response.StatusCode;
            return status >= 300 && status < 400;
        }

        private HttpResponseMessage Open(Uri url, HttpMethod method, long offset, CancellationToken cancel)
        {
            for (int redirects = 0; redirects <= 5; redirects++)
            {
                if (url.Scheme != "https" || !url.IsDefaultPort || url.UserInfo.Length != 0 ||
                    !(url.Host == "github.com" || url.Host == "release-assets.githubusercontent.com" || url.Host == "objects.githubusercontent.com"))
                    throw new InvalidOperationException("官方发布资源重定向到未知地址，已停止下载。");
                using (var request = new HttpRequestMessage(method, url))
                {
                    if (offset > 0) request.Headers.Range = new RangeHeaderValue(offset, null);
                    var response = http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancel).GetAwaiter().GetResult();
                    if (!Redirect(response)) return response;
                    using (response)
                    {
                        if (response.Headers.Location == null) throw new HttpRequestException("Missing redirect location");
                        url = new Uri(url, response.Headers.Location);
                    }
                }
            }
            throw new HttpRequestException("Too many redirects");
        }

        private void Receive(Dependency dependency, string partial, CancellationToken cancel)
        {
            long offset = File.Exists(partial) ? new FileInfo(partial).Length : 0;
            using (var response = Open(new Uri(dependency.Url), HttpMethod.Get, offset, cancel))
            {
                if (offset > 0 && response.StatusCode == HttpStatusCode.RequestedRangeNotSatisfiable)
                {
                    File.Delete(partial);
                    throw new IOException("Server rejected download resume");
                }
                response.EnsureSuccessStatusCode();
                bool append = offset > 0 && response.StatusCode == HttpStatusCode.PartialContent;
                if (append)
                {
                    var range = response.Content.Headers.ContentRange;
                    if (range == null || range.From != offset || range.To != dependency.Size - 1 || range.Length != dependency.Size)
                    {
                        File.Delete(partial);
                        throw new IOException("Invalid download range");
                    }
                }
                else if (response.StatusCode != HttpStatusCode.OK) throw new IOException("Unexpected download response");
                long received = append ? offset : 0;
                var timer = Stopwatch.StartNew();
                long last = -300;
                using (var input = response.Content.ReadAsStreamAsync().GetAwaiter().GetResult())
                using (var output = new FileStream(partial, append ? FileMode.Append : FileMode.Create, FileAccess.Write, FileShare.None))
                using (var readDeadline = CancellationTokenSource.CreateLinkedTokenSource(cancel))
                {
                    var buffer = new byte[128 * 1024];
                    for (;;)
                    {
                        if (timer.Elapsed > TimeSpan.FromMinutes(20)) throw new IOException("Download deadline expired");
                        readDeadline.CancelAfter(TimeSpan.FromSeconds(60));
                        // Disposing also interrupts a stream read when the underlying HTTP handler ignores cancellation.
                        int read;
                        using (readDeadline.Token.Register(input.Dispose))
                            read = input.ReadAsync(buffer, 0, buffer.Length, readDeadline.Token).GetAwaiter().GetResult();
                        if (read == 0) break;
                        received += read;
                        if (received > dependency.Size) throw new IOException("Unexpected download length");
                        output.Write(buffer, 0, read);
                        if (timer.ElapsedMilliseconds - last >= 275)
                        {
                            progress(string.Format("下载 {0}  {1:F0}/{2:F0} MB", dependency.Name, received / 1048576.0, dependency.Size / 1048576.0),
                                (int)(100 * received / dependency.Size));
                            last = timer.ElapsedMilliseconds;
                        }
                    }
                }
                if (received != dependency.Size) throw new IOException("Incomplete download");
            }
        }

        public void Dispose() { http.Dispose(); }
    }
}
