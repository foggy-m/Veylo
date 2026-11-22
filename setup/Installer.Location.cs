using System;
using System.IO;
using System.Linq;
using Microsoft.Win32;

namespace VeyloSetup
{
    internal sealed partial class Installer
    {
        private const string SetupKey = @"Software\Veylo\Setup";
        private const string RootValue = "InstallDirectory";
        private const string MarkerName = ".veylo-installation";
        private const string MarkerContent = "Veylo Setup";

        public static readonly string DefaultRoot =
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Veylo");

        public static string PreferredRoot()
        {
            using (var key = Registry.CurrentUser.OpenSubKey(SetupKey))
            {
                string saved = key == null ? null : key.GetValue(RootValue, null, RegistryValueOptions.DoNotExpandEnvironmentNames) as string;
                return string.IsNullOrWhiteSpace(saved) ? DefaultRoot : saved;
            }
        }

        public Installer() : this(PreferredRoot()) { }

        public Installer(string directory)
        {
            string value = (directory ?? "").Trim();
            // Drive-relative paths (such as D:folder) depend on the caller's working directory.
            if (value.Length < 3 || value[1] != ':' || (value[2] != '\\' && value[2] != '/'))
                throw new InvalidOperationException("请输入本机文件夹的完整路径，例如 D:\\Apps\\Veylo。");
            try { Root = Path.GetFullPath(value).TrimEnd('\\'); }
            catch (ArgumentException) { throw new InvalidOperationException("安装路径包含无效字符，请重新选择文件夹。"); }
            catch (NotSupportedException) { throw new InvalidOperationException("安装路径格式无效，请重新选择文件夹。"); }
            if (Root.Length <= 2)
                throw new InvalidOperationException("请选择磁盘里的文件夹，例如 D:\\Veylo。");
            Files.PlainPath(Root);
        }

        private static bool HasInstallMarker(string root)
        {
            string marker = Path.Combine(root, MarkerName);
            Files.PlainPath(marker);
            var file = new FileInfo(marker);
            return file.Exists && file.Length == MarkerContent.Length && File.ReadAllText(marker) == MarkerContent;
        }

        private void PrepareInstallRoot()
        {
            Files.PlainPath(Root);
            if (File.Exists(Root))
                throw new IOException("选择的位置已存在同名文件，请换一个安装文件夹。");
            if (Directory.Exists(Root) && !string.Equals(Root, DefaultRoot, StringComparison.OrdinalIgnoreCase)
                && !HasInstallMarker(Root) && Directory.EnumerateFileSystemEntries(Root).Any())
                throw new IOException("请选择空文件夹，或选择已由新版 Setup 安装的 Veylo 目录。");

            Files.MakeDirectory(Root);
            string marker = Path.Combine(Root, MarkerName);
            if (!HasInstallMarker(Root)) Files.WriteAtomic(marker, MarkerContent);
        }

        private void SaveActiveRoot()
        {
            using (var key = Registry.CurrentUser.CreateSubKey(SetupKey))
                key.SetValue(RootValue, Root, RegistryValueKind.String);
        }
    }
}
