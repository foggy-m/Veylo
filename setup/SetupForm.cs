using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Veylo 安装程序")]
[assembly: AssemblyProduct("Veylo")]
[assembly: AssemblyVersion("2.4.5.0")]
[assembly: AssemblyFileVersion("2.4.5.0")]

namespace VeyloSetup
{
    internal sealed class SetupForm : Form
    {
        private static readonly Color Ink = ColorTranslator.FromHtml("#15282C");
        private static readonly Color Paper = ColorTranslator.FromHtml("#F6F7F3");
        private static readonly Color Teal = ColorTranslator.FromHtml("#17645B");
        private static readonly Color Muted = ColorTranslator.FromHtml("#455C5F");
        private static readonly Color Error = ColorTranslator.FromHtml("#913A2D");
        private readonly Installer installer = new Installer();
        private readonly Label title, description, status;
        private readonly ProgressBar progress;
        private readonly Button primary, secondary;
        private readonly ComboBox browsers;
        private readonly LinkLabel accounts;
        private CancellationTokenSource install;

        private sealed class Browser
        {
            public string Name, Path, Page, ProgId;
            public override string ToString() { return Name; }
        }

        public SetupForm()
        {
            Text = "Veylo 安装程序";
            Icon = Icon.ExtractAssociatedIcon(Assembly.GetExecutingAssembly().Location);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(586, 366);
            BackColor = Paper;
            Font = new Font("Microsoft YaHei UI", 10F);

            var brand = new Panel { Dock = DockStyle.Top, Height = 80, BackColor = Ink };
            var name = new Label { Text = "Veylo", ForeColor = Color.White, AutoSize = true,
                                   Location = new Point(28, 20), Font = new Font("Segoe UI", 22F, FontStyle.Bold) };
            brand.Controls.Add(name);
            Controls.Add(brand);
            title = AddLabel("正在安装 Veylo", 28, 101, 530, 37);
            title.Font = new Font(Font.FontFamily, 17F, FontStyle.Bold);
            description = AddLabel("首次安装会联网准备三个平台的下载组件，约下载 246 MiB。", 28, 146, 530, 52);
            description.ForeColor = Muted;
            status = AddLabel("正在检查安装环境…", 28, 216, 530, 51);
            progress = new ProgressBar { Bounds = new Rectangle(28, 277, 530, 12), Style = ProgressBarStyle.Marquee, MarqueeAnimationSpeed = 25 };
            Controls.Add(progress);
            browsers = new ComboBox { Bounds = new Rectangle(28, 277, 154, 30), DropDownStyle = ComboBoxStyle.DropDownList, Visible = false };
            browsers.AccessibleName = "选择浏览器";
            Controls.Add(browsers);
            accounts = new LinkLabel { Text = "打开下载器登录", LinkColor = Teal, Bounds = new Rectangle(205, 282, 150, 24), Visible = false };
            accounts.LinkClicked += (sender, e) => ShowAccounts();
            Controls.Add(accounts);
            secondary = AddButton("取消", 306, 316, 104);
            primary = AddButton("重试", 420, 316, 138);
            primary.BackColor = Teal;
            primary.ForeColor = Color.White;
            primary.FlatStyle = FlatStyle.Flat;
            primary.Visible = false;
            secondary.Click += (sender, e) => { if (install != null) CancelInstall(); else CopyFolder(); };
            FormClosing += (sender, e) => { if (install != null) { e.Cancel = true; CancelInstall(); } };
            Shown += (sender, e) => StartInstall();
        }

        private Label AddLabel(string text, int x, int y, int width, int height)
        {
            var label = new Label { Text = text, ForeColor = Ink, Bounds = new Rectangle(x, y, width, height) };
            Controls.Add(label);
            return label;
        }

        private Button AddButton(string text, int x, int y, int width)
        {
            var button = new Button { Text = text, Bounds = new Rectangle(x, y, width, 32), ForeColor = Ink };
            Controls.Add(button);
            return button;
        }

        private async void StartInstall()
        {
            if (install != null) return;
            title.Text = "正在安装 Veylo";
            description.Text = "首次安装会联网准备三个平台的下载组件，约下载 246 MiB。";
            status.ForeColor = Ink;
            progress.Visible = true;
            secondary.Text = "取消";
            secondary.Enabled = true;
            secondary.Visible = true;
            primary.Visible = false;
            Exception failure = null;
            using (install = new CancellationTokenSource())
            {
                var update = new Progress<Tuple<string, int>>(value => UpdateProgress(value.Item1, value.Item2));
                try
                {
                    await Task.Run(() => installer.Install((message, percent) => ((IProgress<Tuple<string, int>>)update).Report(Tuple.Create(message, percent)), install.Token));
                }
                catch (Exception error) { failure = error; }
            }
            install = null;
            if (failure != null)
            {
                if (failure is OperationCanceledException) { Close(); return; }
                title.Text = "安装未完成";
                description.Text = "可以在此重试。已经校验通过的依赖会保留并继续使用。";
                status.Text = UserMessage(failure);
                status.ForeColor = Error;
                progress.Visible = false;
                secondary.Visible = false;
                primary.Text = "重试";
                primary.Visible = true;
                primary.Click -= Retry;
                primary.Click += Retry;
                AcceptButton = primary;
                primary.Focus();
                return;
            }
            ShowFinished();
        }

        private void Retry(object sender, EventArgs e) { StartInstall(); }

        private void CancelInstall()
        {
            secondary.Enabled = false;
            status.Text = "正在停止安装…";
            install.Cancel();
        }

        private void UpdateProgress(string message, int value)
        {
            if (install == null || install.IsCancellationRequested) return;
            status.Text = message;
            progress.Style = value < 0 ? ProgressBarStyle.Marquee : ProgressBarStyle.Continuous;
            if (value >= 0) progress.Value = value;
        }

        private void ShowFinished()
        {
            title.Text = "安装完成，加载浏览器扩展";
            description.Text = "打开扩展管理页，开启「开发者模式」，选择「加载已解压的扩展程序」。";
            status.Text = "安装器会复制扩展目录。在浏览器的文件夹窗口里粘贴路径并确认。";
            progress.Visible = false;
            browsers.Items.AddRange(InstalledBrowsers().Cast<object>().ToArray());
            browsers.Visible = browsers.Items.Count > 0;
            if (browsers.Visible) browsers.SelectedIndex = 0;
            accounts.Visible = true;
            primary.Text = browsers.Visible ? "打开扩展管理页" : "打开扩展文件夹";
            primary.Click -= Retry;
            primary.Click += OpenExtensions;
            primary.Visible = true;
            secondary.Text = "复制路径";
            secondary.Enabled = true;
            secondary.Visible = true;
            AcceptButton = primary;
            primary.Focus();
        }

        private void ShowAccounts()
        {
            var menu = new ContextMenuStrip();
            menu.Items.Add("登录 YouTube / X", null, (sender, e) => OpenAccountApp("vendor/FluentYTDL-3.7.6/FluentYTDL.exe"));
            menu.Items.Add("登录哔哩哔哩", null, (sender, e) => OpenAccountApp("vendor/Bili23-2.14.0/Bili23.exe"));
            menu.Closed += (sender, e) => menu.Dispose();
            menu.Show(accounts, new Point(0, accounts.Height));
        }

        private void OpenAccountApp(string relative)
        {
            try
            {
                string application = Files.Inside(installer.Root, relative);
                Files.PlainPath(application);
                Process.Start(new ProcessStartInfo(application) { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(application) });
            }
            catch (Exception error) { status.Text = UserMessage(error); }
        }

        private void CopyFolder()
        {
            try { Clipboard.SetText(installer.Extension); status.Text = "已复制路径。在浏览器的文件夹窗口里粘贴并确认。"; }
            catch (System.Runtime.InteropServices.ExternalException) { status.Text = "请手动复制扩展目录 " + installer.Extension; }
        }

        private void OpenExtensions(object sender, EventArgs e)
        {
            CopyFolder();
            try
            {
                var browser = browsers.SelectedItem as Browser;
                if (browser != null) Process.Start(new ProcessStartInfo(browser.Path, browser.Page) { UseShellExecute = false });
                else Process.Start(new ProcessStartInfo("explorer.exe", Files.Quote(installer.Extension)) { UseShellExecute = false });
            }
            catch (System.ComponentModel.Win32Exception) { status.Text = "未能打开浏览器。请在所用浏览器中打开扩展管理页并粘贴已复制的路径。"; }
        }

        private static IEnumerable<Browser> InstalledBrowsers()
        {
            var candidates = new[] {
                new Browser { Name = "Chrome", Path = @"Google\Chrome\Application\chrome.exe", Page = "chrome://extensions/", ProgId = "ChromeHTML" },
                new Browser { Name = "Edge", Path = @"Microsoft\Edge\Application\msedge.exe", Page = "edge://extensions/", ProgId = "MSEdgeHTM" },
                new Browser { Name = "Tabbit", Path = @"Tabbit Browser\Application\Tabbit Browser.exe", Page = "chrome://extensions/", ProgId = "Tabbit" }
            };
            var roots = new[] { Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData) };
            var found = new List<Browser>();
            foreach (Browser browser in candidates)
            {
                string installed = roots.Where(root => root != "").Select(root => Path.Combine(root, browser.Path)).FirstOrDefault(File.Exists);
                if (installed != null) { browser.Path = installed; found.Add(browser); }
            }
            using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"))
            {
                string current = key == null ? "" : (key.GetValue("ProgId") as string ?? "");
                return found.OrderByDescending(browser => current.StartsWith(browser.ProgId, StringComparison.OrdinalIgnoreCase)).ToArray();
            }
        }

        internal static string UserMessage(Exception error)
        {
            if (error is UnauthorizedAccessException) return "无法写入当前用户的安装目录或浏览器注册。请检查 Windows 账号权限后重试。";
            if (error is IOException || error is InvalidOperationException) return error.Message;
            return "安装程序未能完成。请确认系统是 Windows 10 或 11 x64，再运行安装程序。";
        }

        [STAThread]
        public static int Main(string[] args)
        {
            if (args.Length == 1 && (args[0] == "--quiet" || args[0] == "--version"))
            {
                // A GUI executable may have redirected standard streams but no console window.
                // Encode the streams directly instead of changing a nonexistent console's code page.
                using (var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true })
                using (var errors = new StreamWriter(Console.OpenStandardError(), new UTF8Encoding(false)) { AutoFlush = true })
                {
                    if (args[0] == "--version") { output.WriteLine(Installer.Version); return 0; }
                    try { new Installer().Install((message, percent) => output.WriteLine(message), CancellationToken.None); return 0; }
                    catch (Exception error) { errors.WriteLine(UserMessage(error)); return 1; }
                }
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (var window = new SetupForm()) Application.Run(window);
            return 0;
        }
    }
}
