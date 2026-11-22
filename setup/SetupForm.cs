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
[assembly: AssemblyVersion(VeyloSetup.Installer.Version + ".0")]
[assembly: AssemblyFileVersion(VeyloSetup.Installer.Version + ".0")]

namespace VeyloSetup
{
    internal sealed class SetupForm : Form
    {
        private static readonly Color Ink = ColorTranslator.FromHtml("#15282C");
        private static readonly Color Paper = ColorTranslator.FromHtml("#F6F7F3");
        private static readonly Color Teal = ColorTranslator.FromHtml("#17645B");
        private static readonly Color Muted = ColorTranslator.FromHtml("#455C5F");
        private static readonly Color Error = ColorTranslator.FromHtml("#913A2D");
        private Installer installer;
        private readonly Label title, description, status;
        private readonly TableLayoutPanel location;
        private readonly TextBox destination;
        private readonly ProgressBar progress;
        private readonly Button primary, secondary;
        private readonly ComboBox browsers;
        private readonly LinkLabel accounts, updates;
        private CancellationTokenSource install;
        private bool finished;

        private sealed class Browser
        {
            public string Name, Path, Page, ProgId;
            public override string ToString() { return Name; }
        }

        public SetupForm()
        {
            SuspendLayout();
            Text = "Veylo 安装程序";
            Icon = Icon.ExtractAssociatedIcon(Assembly.GetExecutingAssembly().Location);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            // All layout sizes are expressed at 96 DPI, even when Setup launches on a scaled display.
            AutoScaleDimensions = new SizeF(96F, 96F);
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(586, 330);
            BackColor = Paper;
            Font = new Font("Microsoft YaHei UI", 10F);

            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 4 };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            Controls.Add(layout);

            var brand = new FlowLayoutPanel
            {
                Dock = DockStyle.Fill, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
                Margin = Padding.Empty, Padding = new Padding(28, 17, 28, 17), BackColor = Ink
            };
            brand.Controls.Add(new Label
            {
                Text = "Veylo", ForeColor = Color.White, AutoSize = true,
                Margin = Padding.Empty, Font = new Font("Segoe UI", 22F, FontStyle.Bold)
            });
            layout.Controls.Add(brand, 0, 0);

            var body = new Panel { Dock = DockStyle.Fill, AutoScroll = true, Margin = Padding.Empty };
            layout.Controls.Add(body, 0, 1);
            var content = new TableLayoutPanel
            {
                Dock = DockStyle.Top, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
                ColumnCount = 1, Padding = new Padding(28, 22, 28, 10)
            };
            content.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            body.Controls.Add(content);

            title = CreateLabel("安装 Veylo", 9);
            title.Font = new Font(Font.FontFamily, 17F, FontStyle.Bold);
            content.Controls.Add(title);
            description = CreateLabel("安装器会自动准备下载组件并连接浏览器。", 18);
            description.ForeColor = Muted;
            content.Controls.Add(description);

            location = new TableLayoutPanel
            {
                Dock = DockStyle.Top, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
                ColumnCount = 2, RowCount = 2, Margin = new Padding(0, 0, 0, 12)
            };
            location.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            location.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            var locationLabel = CreateLabel("安装位置", 6);
            location.Controls.Add(locationLabel, 0, 0);
            location.SetColumnSpan(locationLabel, 2);
            destination = new TextBox
            {
                Text = Installer.PreferredRoot(), AccessibleName = "安装位置",
                Anchor = AnchorStyles.Left | AnchorStyles.Right, Margin = new Padding(0, 0, 8, 0)
            };
            location.Controls.Add(destination, 0, 1);
            var browse = CreateButton("浏览…", 78);
            browse.Margin = Padding.Empty;
            browse.Click += (sender, e) => BrowseForLocation();
            location.Controls.Add(browse, 1, 1);
            content.Controls.Add(location);

            status = CreateLabel("", 13);
            status.Visible = false;
            content.Controls.Add(status);
            progress = new ProgressBar
            {
                Dock = DockStyle.Top, Height = 12, Margin = new Padding(28, 7, 28, 8),
                Style = ProgressBarStyle.Marquee, MarqueeAnimationSpeed = 25, Visible = false
            };
            layout.Controls.Add(progress, 0, 2);

            var tools = new FlowLayoutPanel
            {
                Dock = DockStyle.Top, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Margin = Padding.Empty
            };
            browsers = new ComboBox
            {
                Width = 154, DropDownStyle = ComboBoxStyle.DropDownList, Margin = new Padding(0, 0, 22, 6),
                AccessibleName = "选择浏览器", Visible = false
            };
            tools.Controls.Add(browsers);
            accounts = new LinkLabel
            {
                Text = "打开下载器登录", LinkColor = Teal, AutoSize = true, Margin = new Padding(0, 4, 22, 6), Visible = false
            };
            accounts.LinkClicked += (sender, e) => ShowAccounts();
            tools.Controls.Add(accounts);
            updates = new LinkLabel
            {
                Text = "组件更新", LinkColor = Teal, AutoSize = true, Margin = new Padding(0, 4, 0, 6), Visible = false
            };
            updates.LinkClicked += (sender, e) => ShowUpdateMenu();
            tools.Controls.Add(updates);
            content.Controls.Add(tools);

            var actions = new FlowLayoutPanel
            {
                Dock = DockStyle.Fill, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
                FlowDirection = FlowDirection.RightToLeft, Margin = Padding.Empty, Padding = new Padding(28, 7, 28, 18)
            };
            layout.Controls.Add(actions, 0, 3);
            primary = CreateButton("安装", 138);
            primary.BackColor = Teal;
            primary.ForeColor = Color.White;
            primary.FlatStyle = FlatStyle.Flat;
            primary.Click += (sender, e) => { if (finished) OpenExtensions(); else StartInstall(); };
            actions.Controls.Add(primary);
            secondary = CreateButton("取消", 104);
            actions.Controls.Add(secondary);
            secondary.Click += (sender, e) =>
            {
                if (install != null) CancelInstall();
                else if (finished) CopyFolder();
                else Close();
            };
            AcceptButton = primary;
            FormClosing += (sender, e) => { if (install != null) { e.Cancel = true; CancelInstall(); } };
            ResumeLayout(true);
        }

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            Rectangle workArea = Screen.FromControl(this).WorkingArea;
            Size = new Size(Math.Min(Width, workArea.Width), Math.Min(Height, workArea.Height));
        }

        private static Label CreateLabel(string text, int bottomMargin)
        {
            return new Label
            {
                Text = text, ForeColor = Ink, AutoSize = true, Dock = DockStyle.Fill,
                Margin = new Padding(0, 0, 0, bottomMargin)
            };
        }

        private static Button CreateButton(string text, int width)
        {
            return new Button
            {
                Text = text, ForeColor = Ink, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
                MinimumSize = new Size(width, 32), Margin = new Padding(8, 0, 0, 0)
            };
        }

        private void BrowseForLocation()
        {
            using (var dialog = new FolderBrowserDialog { Description = "选择 Veylo 安装文件夹", ShowNewFolderButton = true })
            {
                if (Directory.Exists(destination.Text)) dialog.SelectedPath = destination.Text;
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                string selected = dialog.SelectedPath;
                destination.Text = string.Equals(selected, Path.GetPathRoot(selected), StringComparison.OrdinalIgnoreCase)
                    ? Path.Combine(selected, "Veylo") : selected;
            }
        }

        private void ShowInstalling()
        {
            title.Text = "正在安装 Veylo";
            description.Text = "首次安装会联网准备三个平台的下载组件，约下载 246 MiB。";
            location.Visible = false;
            status.Text = "正在检查安装环境…";
            status.ForeColor = Ink;
            status.Visible = true;
            progress.Visible = true;
            secondary.Text = "取消";
            secondary.Enabled = true;
            primary.Visible = false;
        }

        private async void StartInstall()
        {
            if (install != null) return;
            try { installer = new Installer(destination.Text); }
            catch (Exception error) { ShowInstallFailure(error); return; }
            ShowInstalling();
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
                ShowInstallFailure(failure);
                return;
            }
            ShowFinished();
            try { if (installer.TrackUpdates) UpdateDownloads(); }
            catch (Exception error) { status.Text = UserMessage(error); }
        }

        private void ShowInstallFailure(Exception failure)
        {
            title.Text = "安装未完成";
            description.Text = "可以更换安装位置，或重试当前目录。";
            location.Visible = true;
            status.Text = UserMessage(failure);
            status.ForeColor = Error;
            status.Visible = true;
            progress.Visible = false;
            primary.Text = "重试";
            primary.Visible = true;
            primary.Focus();
        }

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
            finished = true;
            title.Text = "安装完成，加载浏览器扩展";
            description.Text = "打开扩展管理页，开启「开发者模式」，选择「加载已解压的扩展程序」。";
            status.Text = "安装器会复制扩展目录。在浏览器的文件夹窗口里粘贴路径并确认。";
            progress.Visible = false;
            browsers.Items.AddRange(InstalledBrowsers().Cast<object>().ToArray());
            browsers.Visible = browsers.Items.Count > 0;
            if (browsers.Visible) browsers.SelectedIndex = 0;
            accounts.Visible = true;
            updates.Visible = true;
            primary.Text = browsers.Visible ? "打开扩展管理页" : "打开扩展文件夹";
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
            menu.Items.Add("登录 YouTube / X", null, (sender, e) => OpenAccountApp(false));
            menu.Items.Add("登录哔哩哔哩", null, (sender, e) => OpenAccountApp(true));
            menu.Closed += (sender, e) => menu.Dispose();
            menu.Show(accounts, new Point(0, accounts.Height));
        }

        private void OpenAccountApp(bool bili)
        {
            try { installer.OpenVendor(bili); }
            catch (Exception error) { status.Text = UserMessage(error); }
        }

        private void ShowUpdateMenu()
        {
            var menu = new ContextMenuStrip();
            menu.Items.Add("检查并更新兼容组件", null, (sender, e) => UpdateDownloads());
            var track = new ToolStripMenuItem("打开管理器时自动更新") { CheckOnClick = true };
            try { track.Checked = installer.TrackUpdates; }
            catch (Exception error) { status.Text = UserMessage(error); }
            track.CheckedChanged += (sender, e) =>
            {
                try
                {
                    installer.TrackUpdates = track.Checked;
                    status.Text = track.Checked ? "每次打开管理器会自动检查并安装兼容的组件更新。" : "可随时从「组件更新」检查新版。";
                    if (track.Checked) UpdateDownloads();
                }
                catch (Exception error) { status.Text = UserMessage(error); }
            };
            menu.Items.Add(track);
            menu.Items.Add("恢复随包 yt-dlp", null, (sender, e) =>
            {
                try { installer.RestoreBundledEngine(); status.Text = "已恢复随包 yt-dlp，并关闭管理器自动更新。"; }
                catch (Exception error) { status.Text = UserMessage(error); }
            });
            menu.Closed += (sender, e) => menu.Dispose();
            menu.Show(updates, new Point(0, updates.Height));
        }

        private async void UpdateDownloads()
        {
            if (install != null) return;
            title.Text = "下载组件更新";
            description.Text = "官方新版本通过兼容检查后会用于新任务。";
            status.ForeColor = Ink;
            status.Text = "正在查询版本…";
            primary.Enabled = false;
            bool hasBrowser = browsers.Visible;
            browsers.Visible = accounts.Visible = updates.Visible = false;
            progress.Visible = true;
            progress.Value = 0;
            secondary.Text = "取消";
            using (install = new CancellationTokenSource())
            {
                var update = (IProgress<Tuple<string, int>>)new Progress<Tuple<string, int>>(value => UpdateProgress(value.Item1, value.Item2));
                try
                {
                    var result = await Task.Run(() => installer.UpdateComponents((message, percent) =>
                        update.Report(Tuple.Create(message, percent)), install.Token));
                    status.Text = string.Join(Environment.NewLine, result);
                }
                catch (OperationCanceledException) { status.Text = "已停止检查更新。"; }
                catch (Exception error) { status.Text = UserMessage(error); status.ForeColor = Error; }
            }
            install = null;
            progress.Visible = false;
            accounts.Visible = updates.Visible = true;
            browsers.Visible = hasBrowser;
            primary.Enabled = secondary.Enabled = true;
            secondary.Text = "复制路径";
        }

        private void CopyFolder()
        {
            try { Clipboard.SetText(installer.Extension); status.Text = "已复制路径。在浏览器的文件夹窗口里粘贴并确认。"; }
            catch (System.Runtime.InteropServices.ExternalException) { status.Text = "请手动复制扩展目录 " + installer.Extension; }
        }

        private void OpenExtensions()
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
            if (args.Length > 0 && new[] { "--quiet", "--version", "--update-components", "--restore-engine" }.Contains(args[0]))
            {
                // A GUI executable may have redirected standard streams but no console window.
                // Encode the streams directly instead of changing a nonexistent console's code page.
                using (var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true })
                using (var errors = new StreamWriter(Console.OpenStandardError(), new UTF8Encoding(false)) { AutoFlush = true })
                {
                    bool custom = args.Length == 3 && args[0] == "--quiet" && args[1] == "--install-dir";
                    if (args.Length != 1 && !custom)
                    {
                        errors.WriteLine("安装参数无效。请使用 --quiet 或 --quiet --install-dir <目录>。");
                        return 2;
                    }
                    if (args[0] == "--version") { output.WriteLine(Installer.Version); return 0; }
                    try
                    {
                        var setup = custom ? new Installer(args[2]) : new Installer();
                        if (args[0] == "--restore-engine") setup.RestoreBundledEngine();
                        else if (args[0] == "--update-components")
                        {
                            foreach (string message in setup.UpdateComponents((name, percent) => output.WriteLine(name), CancellationToken.None))
                                output.WriteLine(message);
                        }
                        else setup.Install((message, percent) => output.WriteLine(message), CancellationToken.None);
                        return 0;
                    }
                    catch (Exception error) { errors.WriteLine(UserMessage(error)); return 1; }
                }
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (var window = new SetupForm())
            {
                string manager = window.destination.Text.TrimEnd('\\') + "\\" + Installer.AppDirectory + "\\Veylo-Setup.exe";
                if (string.Equals(Assembly.GetExecutingAssembly().Location, manager, StringComparison.OrdinalIgnoreCase))
                    window.Shown += (sender, e) => window.StartInstall();
                Application.Run(window);
            }
            return 0;
        }
    }
}
