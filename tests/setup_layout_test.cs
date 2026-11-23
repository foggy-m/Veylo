using System;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

internal static class SetupLayoutTest
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

    [DllImport("user32")]
    private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length == 3 && args[0] == "--browser-launch")
        {
            string temporary = args[1] + ".tmp";
            File.WriteAllText(temporary, args[2]);
            File.Move(temporary, args[1]);
            return 0;
        }
        if (args.Contains("--96"))
        {
            // The DPI-unaware thread uses 96 DPI without changing the desktop settings.
            Require(SetThreadDpiAwarenessContext(new IntPtr(-1)) != IntPtr.Zero, "Could not set the test thread to 96 DPI");
        }

        Control.CheckForIllegalCrossThreadCalls = true;
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Type setup = Assembly.LoadFrom(Path.GetFullPath(args[0])).GetType("VeyloSetup.SetupForm", true);
        using (var form = (Form)Activator.CreateInstance(setup))
        {
            // Use the same message loop as Setup so asynchronous updates return to the UI thread.
            form.Opacity = 0;
            form.ShowInTaskbar = false;
            Exception failure = null;
            form.Shown += (sender, e) =>
            {
                try { CheckWindow(form, args.Contains("--installed-actions")); }
                catch (Exception error) { failure = error; }
                finally { form.Close(); }
            };
            Application.Run(form);
            if (failure != null)
            {
                Console.Error.WriteLine(failure);
                return 1;
            }
        }
        return 0;
    }

    private static void CheckWindow(Form form, bool installedActions)
    {
        Type setup = form.GetType();
        var title = Field<Label>(form, "title");
        var description = Field<Label>(form, "description");
        var status = Field<Label>(form, "status");
        var progress = Field<ProgressBar>(form, "progress");
        var cancel = Field<Button>(form, "secondary");
        var primary = Field<Button>(form, "primary");
        var destination = Field<TextBox>(form, "destination");
        var location = Field<TableLayoutPanel>(form, "location");
        CheckText(title);
        CheckText(description);
        CheckInClient(form, destination);
        CheckTextIn(form, location.Controls.OfType<Button>().Single());
        CheckTextIn(form, primary);
        Require(Path.IsPathRooted(destination.Text) && !progress.Visible, "Setup must offer a path before installing");

        string originalLocation = destination.Text;
        destination.Text = "D:relative-install-location";
        primary.PerformClick();
        Require(destination.Visible && !progress.Visible && status.Visible && primary.Text == "重试",
                "An invalid destination must leave the chooser available");
        destination.Text = originalLocation;
        setup.GetMethod("ShowInstalling", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(form, null);
        Require(!destination.Visible && progress.Visible, "Install progress must replace the chooser");
        status.Text = "下载 FluentYTDL 3.7.6 full (Windows x64)  132/182 MB";
        CheckText(title);
        CheckText(description);
        CheckText(status);
        CheckTextIn(form, cancel);
        CheckInClient(form, progress);

        int oneLineHeight = status.Height;
        float scale = form.CurrentAutoScaleDimensions.Width / 96;
        form.ClientSize = new Size((int)(350 * scale), (int)(245 * scale));
        CheckText(title);
        CheckText(description);
        CheckText(status);
        Require(status.Height > oneLineHeight, "Download status must wrap on a narrow window");
        var body = (ScrollableControl)status.Parent.Parent;
        Require(body.VerticalScroll.Visible && !body.HorizontalScroll.Visible,
                "A compact window must allow scrolling through the text");
        CheckTextIn(form, cancel);
        CheckInClient(form, progress);

        setup.GetMethod("ShowFinished", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(form, null);
        var installRoot = installedActions ? Environment.GetEnvironmentVariable("VEYLO_TEST_INSTALL_DIR")
            : Path.Combine(Path.GetTempPath(), "Veylo layout fixture");
        Require(!installedActions || (!string.IsNullOrEmpty(installRoot) && Directory.Exists(installRoot)),
                "The installed actions test needs the disposable Windows installation");
        var installer = setup.Assembly.GetType("VeyloSetup.Installer", true);
        setup.GetField("installer", Private).SetValue(form, Activator.CreateInstance(installer, installRoot));
        status.Text = "FluentYTDL 3.7.6 · 官方 3.7.7 等待 Veylo 兼容验证。\n"
                    + "Bili23 2.14.0 · 官方 2.21.0 等待 Veylo 兼容验证。\n"
                    + "yt-dlp 2026.11.20 · 已更新至官方版本。";
        CheckText(title);
        CheckText(description);
        CheckText(status);
        var accounts = Field<LinkLabel>(form, "accounts");
        var updates = Field<LinkLabel>(form, "updates");
        CheckTextIn(form, accounts);
        CheckTextIn(form, updates);
        Require(!accounts.Bounds.IntersectsWith(updates.Bounds), "Setup links must fit beside or below each other");
        var browsers = Field<ComboBox>(form, "browsers");
        if (browsers.Visible) CheckInClient(form, browsers);
        CheckTextIn(form, primary);
        CheckTextIn(form, cancel);
        CheckMenu(form, accounts, "accountMenu", 2);
        CheckMenu(form, updates, "updateMenu", 3);
        if (installedActions) CheckInstalledActions(form, installRoot);
        else
        {
            for (int option = 0; option < 2; option++)
            {
                ClickMenuItem(form, accounts, "accountMenu", option);
                Require(status.Text.StartsWith("未能打开") && primary.Enabled && cancel.Enabled,
                        "A missing account app must leave the Setup actions available");
            }
        }
        Console.WriteLine("PASS Setup layout, menus and post-install controls at {0} DPI",
                          form.CurrentAutoScaleDimensions.Width);
    }

    private static void CheckMenu(Form form, LinkLabel link, string field, int count)
    {
        foreach (var reason in new[] { ToolStripDropDownCloseReason.Keyboard, ToolStripDropDownCloseReason.AppClicked })
        {
            var menu = OpenMenu(form, link, field);
            Require(menu.Items.Count == count, "Missing items in " + link.Text);
            Require(Screen.FromControl(menu).WorkingArea.Contains(menu.Bounds), "Menu is outside the screen");
            menu.Close(reason);
            Require(!menu.Visible && !menu.IsDisposed, "Menu must close and remain available: " + link.Text);
        }
    }

    private static ContextMenuStrip OpenMenu(Form form, LinkLabel link, string field)
    {
        var clicked = new LinkLabelLinkClickedEventArgs(link.Links[0]);
        typeof(LinkLabel).GetMethod("OnLinkClicked", Private).Invoke(link, new object[] { clicked });
        var menu = Field<ContextMenuStrip>(form, field);
        Require(menu.Visible, "Failed to open " + link.Text);
        return menu;
    }

    private static void ClickMenuItem(Form form, LinkLabel link, string field, int index)
    {
        var menu = OpenMenu(form, link, field);
        menu.Items[index].PerformClick();
        if (menu.Visible) menu.Close();
    }

    private static void CheckInstalledActions(Form form, string installRoot)
    {
        var primary = Field<Button>(form, "primary");
        var secondary = Field<Button>(form, "secondary");
        var status = Field<Label>(form, "status");
        var updates = Field<LinkLabel>(form, "updates");
        var progress = Field<ProgressBar>(form, "progress");
        var browsers = Field<ComboBox>(form, "browsers");
        string extension = Path.Combine(installRoot, "app-2.4.5", "extension");

        ClickMenuItem(form, updates, "updateMenu", 0);
        Require(progress.Visible && !primary.Enabled && updates.Visible && !updates.Enabled,
                "Running updates must keep the controls in view");
        secondary.PerformClick();
        Require(status.Text == "正在停止检查更新…", "Update cancellation must identify the operation");
        WaitUntil(() => !progress.Visible, TimeSpan.FromSeconds(30));
        Require(status.Text == "已停止检查更新。" && primary.Enabled && updates.Enabled && secondary.Enabled,
                "Cancelling updates must restore the actions");

        ClickMenuItem(form, updates, "updateMenu", 0);
        WaitUntil(() => !progress.Visible, TimeSpan.FromMinutes(4));
        Require(status.Text.Contains("Bili23") && updates.Enabled, "Update results must leave the menu usable. Status: " + status.Text);
        CheckTextIn(form, updates);
        CheckTextIn(form, Field<LinkLabel>(form, "accounts"));

        ClickMenuItem(form, updates, "updateMenu", 1);
        Require(progress.Visible, "Enabling automatic updates must start a check");
        secondary.PerformClick();
        WaitUntil(() => !progress.Visible, TimeSpan.FromSeconds(30));
        ClickMenuItem(form, updates, "updateMenu", 2);
        Require(status.Text.StartsWith("已恢复随包 yt-dlp"), "Restore must report its result");
        var menu = OpenMenu(form, updates, "updateMenu");
        Require(!((ToolStripMenuItem)menu.Items[1]).Checked, "Restore must disable automatic updates");
        menu.Close();

        secondary.PerformClick();
        Require(status.Text.StartsWith("已复制路径") && Clipboard.GetText() == extension,
                "Copy must use the selected installation after updating");

        // Keep the browser action on the normal Process.Start path with a harmless test executable.
        var kind = form.GetType().GetNestedType("Browser", BindingFlags.NonPublic);
        object browser = Activator.CreateInstance(kind, true);
        kind.GetField("Name").SetValue(browser, "Setup test browser");
        string record = Path.Combine(Path.GetTempPath(), "veylo-browser-action-" + Guid.NewGuid().ToString("N") + ".txt");
        kind.GetField("Page").SetValue(browser, "--browser-launch \"" + record + "\" chrome://extensions/");
        kind.GetField("Path").SetValue(browser, record + ".exe");
        browsers.Items.Add(browser);
        browsers.SelectedItem = browser;
        primary.PerformClick();
        Require(status.Text.Contains("手动打开") && primary.Enabled, "Failed launch must leave a usable fallback");
        try
        {
            kind.GetField("Path").SetValue(browser, Assembly.GetExecutingAssembly().Location);
            primary.PerformClick();
            WaitUntil(() => File.Exists(record), TimeSpan.FromSeconds(10));
            Require(File.ReadAllText(record) == "chrome://extensions/" && Clipboard.GetText() == extension,
                    "Browser action must open the extensions page and copy the selected path");
        }
        finally { File.Delete(record); }
        Console.WriteLine("PASS update success, cancellation, automatic check, restore, clipboard and launch actions");
    }

    private static void WaitUntil(Func<bool> done, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (!done() && DateTime.UtcNow < deadline)
        {
            Application.DoEvents();
            Thread.Sleep(20);
        }
        Require(done(), "A Setup action did not finish in time");
    }

    private static T Field<T>(Form form, string name) where T : Control
    {
        return (T)form.GetType().GetField(name, Private).GetValue(form);
    }

    private static void CheckText(Control control)
    {
        Require(control.Visible, "Missing text: " + control.Text);
        using (Graphics graphics = control.CreateGraphics())
        {
            var text = TextRenderer.MeasureText(graphics, control.Text, control.Font,
                new Size(control.ClientSize.Width, int.MaxValue), TextFormatFlags.WordBreak | TextFormatFlags.TextBoxControl);
            Require(text.Width <= control.ClientSize.Width && text.Height <= control.ClientSize.Height
                    && control.Right <= control.Parent.ClientSize.Width, "Clipped text: " + control.Text);
        }
    }

    private static void CheckTextIn(Form form, Control control)
    {
        CheckInClient(form, control);
        CheckText(control);
    }

    private static void CheckInClient(Form form, Control control)
    {
        var bounds = control.RectangleToScreen(control.ClientRectangle);
        Require(control.Visible && bounds.Width > 0 && bounds.Height > 0, "Missing control: " + control.GetType().Name);
        for (Control parent = control.Parent; parent != null; parent = parent.Parent)
        {
            Require(parent.RectangleToScreen(parent.ClientRectangle).Contains(bounds), "Clipped control: " + control.Text);
            if (parent == form) break;
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
