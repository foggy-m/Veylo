using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Threading;

namespace VeyloSetup
{
    internal static class Files
    {
        public static string Inside(string root, string relative)
        {
            string name = relative.Replace('/', '\\').TrimEnd('\\');
            if (name.Length == 0 || Path.IsPathRooted(name) || name.IndexOf(':') >= 0 ||
                name.Split('\\').Any(part => part.Length == 0 || part.EndsWith(".") || part.EndsWith(" ")))
                throw new InvalidDataException("安装包包含无效路径。");
            string prefix = Path.GetFullPath(root).TrimEnd('\\') + "\\";
            string full = Path.GetFullPath(Path.Combine(prefix, name));
            if (!full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("文件路径超出安装目录。");
            return full;
        }

        public static void PlainPath(string path)
        {
            string full = Path.GetFullPath(path);
            string root = Path.GetPathRoot(full);
            if (root.Length != 3 || new DriveInfo(root).DriveType != DriveType.Fixed)
                throw new IOException("Veylo 需要本机固定磁盘上的安装目录。");
            for (string step = full; step != null; step = Path.GetDirectoryName(step))
            {
                try
                {
                    if ((File.GetAttributes(step) & FileAttributes.ReparsePoint) != 0)
                        throw new IOException("Veylo 安装目录不能经过链接。请检查 Windows 的本地应用目录。");
                }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
            }
        }

        public static string MakeDirectory(string path)
        {
            PlainPath(path);
            Directory.CreateDirectory(path);
            return path;
        }

        public static void WriteAtomic(string path, string value)
        {
            PlainPath(path);
            string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllText(temporary, value, new UTF8Encoding(false));
                Replace(temporary, path);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }

        public static void CopyAtomic(string source, string path)
        {
            PlainPath(path);
            string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                using (var input = File.OpenRead(source))
                using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write))
                    input.CopyTo(output);
                Replace(temporary, path);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }

        private static void Replace(string temporary, string destination)
        {
            if (File.Exists(destination)) File.Replace(temporary, destination, null);
            else File.Move(temporary, destination);
        }

        public static void ExpandZip(Stream stream, string directory, bool repair, CancellationToken cancel)
        {
            using (var archive = new ZipArchive(stream, ZipArchiveMode.Read))
            {
                var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                if (archive.Entries.Count > 20000) throw new InvalidDataException("压缩包文件数量异常。");
                long total = 0;
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    cancel.ThrowIfCancellationRequested();
                    total = checked(total + entry.Length);
                    string path = Inside(directory, entry.FullName);
                    if (!paths.Add(path) || total > 3L * 1024 * 1024 * 1024 ||
                        ((entry.ExternalAttributes >> 16) & 0xf000) == 0xa000 ||
                        (entry.ExternalAttributes & (int)FileAttributes.ReparsePoint) != 0)
                        throw new InvalidDataException("压缩包包含异常文件。");
                    if (entry.Name.Length == 0) { MakeDirectory(path); continue; }
                    MakeDirectory(Path.GetDirectoryName(path));
                    PlainPath(path);
                    if (repair && File.Exists(path) && SameContent(entry, path)) continue;
                    if (!repair)
                    {
                        using (var input = entry.Open())
                        using (var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write)) input.CopyTo(output);
                        continue;
                    }
                    string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
                    try
                    {
                        using (var input = entry.Open())
                        using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write)) input.CopyTo(output);
                        Replace(temporary, path);
                    }
                    finally { if (File.Exists(temporary)) File.Delete(temporary); }
                }
            }
        }

        private static bool SameContent(ZipArchiveEntry entry, string path)
        {
            if (new FileInfo(path).Length != entry.Length) return false;
            using (var source = entry.Open())
            using (var current = File.OpenRead(path)) return Download.Hash(source) == Download.Hash(current);
        }

        public static void Expand7z(string tool, string archive, string directory, CancellationToken cancel)
        {
            string listing = RunTool(tool, "l -slt -sccUTF-8 -- " + Quote(archive), TimeSpan.FromMinutes(2), cancel);
            bool entries = false;
            var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            long total = 0;
            foreach (string line in listing.Split('\n').Select(value => value.TrimEnd('\r')))
            {
                if (line == "----------") { entries = true; continue; }
                if (!entries) continue;
                if (line.StartsWith("Path = ", StringComparison.Ordinal) && !paths.Add(Inside(directory, line.Substring(7))))
                    throw new InvalidDataException("7z 中有重复路径。");
                if (line.StartsWith("Symbolic Link = ", StringComparison.Ordinal) || line.StartsWith("Hard Link = ", StringComparison.Ordinal))
                    throw new InvalidDataException("7z 中有链接文件。");
                if (line.StartsWith("Size = ", StringComparison.Ordinal))
                    total = checked(total + long.Parse(line.Substring(7), System.Globalization.CultureInfo.InvariantCulture));
            }
            if (paths.Count == 0 || paths.Count > 20000 || total > 3L * 1024 * 1024 * 1024)
                throw new InvalidDataException("7z 清单不符合预期。");
            RunTool(tool, "x -y -bso0 -bsp0 " + Quote("-o" + directory) + " -- " + Quote(archive), TimeSpan.FromMinutes(7), cancel);
        }

        private static string RunTool(string executable, string args, TimeSpan timeout, CancellationToken cancel)
        {
            var options = new ProcessStartInfo(executable, args)
            {
                UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Path.GetDirectoryName(executable),
                RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8
            };
            using (var process = Process.Start(options))
            {
                var output = process.StandardOutput.ReadToEndAsync();
                var error = process.StandardError.ReadToEndAsync();
                WaitFor(process, timeout, cancel);
                if (process.ExitCode != 0) throw new IOException("无法解压或读取官方依赖。解压器退出码 " + process.ExitCode, new IOException(error.GetAwaiter().GetResult()));
                return output.GetAwaiter().GetResult();
            }
        }

        public static void WaitFor(Process process, TimeSpan timeout, CancellationToken cancel)
        {
            var timer = Stopwatch.StartNew();
            while (!process.WaitForExit(150))
            {
                if (!cancel.IsCancellationRequested && timer.Elapsed < timeout) continue;
                try { process.Kill(); } catch (InvalidOperationException) { }
                process.WaitForExit(3000);
                cancel.ThrowIfCancellationRequested();
                throw new IOException("等待组件运行超时。");
            }
        }

        public static void RemoveTemporary(string parent, string path)
        {
            string full = Path.GetFullPath(path);
            string name = Path.GetFileName(full);
            Guid identifier;
            if (Path.GetDirectoryName(full) != Path.GetFullPath(parent) || !name.StartsWith(".stage-") ||
                !Guid.TryParseExact(name.Substring(7), "N", out identifier))
                throw new IOException("临时目录不在安装范围内。");
            PlainPath(full);
            if (!Directory.Exists(full)) return;
            CheckTree(full);
            Directory.Delete(full, true);
        }

        public static void CheckTree(string directory)
        {
            foreach (string entry in Directory.EnumerateFileSystemEntries(directory))
            {
                FileAttributes attributes = File.GetAttributes(entry);
                if ((attributes & FileAttributes.ReparsePoint) != 0) throw new IOException("依赖目录包含链接文件。");
                if ((attributes & FileAttributes.Directory) != 0) CheckTree(entry);
            }
        }

        public static string Quote(string value)
        {
            if (value.Contains("\"") || value.EndsWith("\\")) throw new ArgumentException("无效的组件路径。");
            return "\"" + value + "\"";
        }
    }
}
