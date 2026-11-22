using System;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal static class SetupLayoutTest
{
    [DllImport("user32")]
    private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);

    [STAThread]
    private static void Main(string[] args)
    {
        if (args.Length == 2 && args[1] == "--96")
        {
            // The DPI-unaware thread uses 96 DPI without changing the desktop settings.
            Require(SetThreadDpiAwarenessContext(new IntPtr(-1)) != IntPtr.Zero, "Could not set the test thread to 96 DPI");
        }

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Type setup = Assembly.LoadFrom(Path.GetFullPath(args[0])).GetType("VeyloSetup.SetupForm", true);
        using (var form = (Form)Activator.CreateInstance(setup))
        {
            // Exercise WinForms layout without opening the installer on the user's desktop.
            form.Opacity = 0;
            form.ShowInTaskbar = false;
            form.Show();

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
            status.Text = "FluentYTDL 3.7.6 · 官方 3.7.7 等待 Veylo 兼容验证。\r\n"
                        + "Bili23 2.14.0 · 官方 2.21.0 等待 Veylo 兼容验证。\r\n"
                        + "yt-dlp 2026.11.20 · 已更新至官方版本。";
            CheckText(title);
            CheckText(description);
            CheckText(status);
            var accounts = Field<LinkLabel>(form, "accounts");
            var updates = Field<LinkLabel>(form, "updates");
            CheckText(accounts);
            CheckText(updates);
            Require(!accounts.Bounds.IntersectsWith(updates.Bounds), "Setup links must fit beside or below each other");
            CheckTextIn(form, Field<Button>(form, "primary"));
            CheckTextIn(form, cancel);
            Console.WriteLine("PASS Setup location, retry, progress and update layout at {0} DPI",
                              form.CurrentAutoScaleDimensions.Width);
        }
    }

    private static T Field<T>(Form form, string name) where T : Control
    {
        return (T)form.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(form);
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
        var bounds = form.RectangleToClient(control.RectangleToScreen(control.ClientRectangle));
        Require(control.Visible && bounds.Width > 0 && bounds.Height > 0 && form.ClientRectangle.Contains(bounds),
                "Setup action or progress is outside the window: " + control.GetType().Name);
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
