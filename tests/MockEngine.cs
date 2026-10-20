using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Web.Script.Serialization;

internal static class MockEngine
{
    static string Option(string[] args, string flag) {
        int index = Array.IndexOf(args, flag);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : "";
    }
    static int Main(string[] args) {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        string app = Environment.GetEnvironmentVariable("SG_TEST_APP");
        if (string.IsNullOrEmpty(app)) return 90;
        string url = args.Last(), dir = Option(args, "--paths");
        File.WriteAllText(Path.Combine(app, "invocation.json"), new JavaScriptSerializer().Serialize(args));
        if (url.EndsWith("/102")) { Console.Error.WriteLine("SGPROGRESS:25.0%"); Console.Error.Flush(); }
        else { Console.WriteLine("SGPROGRESS:25.0%"); Console.Out.Flush(); }
        Thread.Sleep(3000);
        if (url.EndsWith("/9998")) { Console.Error.WriteLine("login required mock-sensitive-value-do-not-echo"); return 1; }
        if (url.EndsWith("/9997")) { Console.Error.WriteLine("No video found mock-sensitive-value-do-not-echo"); return 1; }
        if (url.EndsWith("/9999")) return 0;
        string file = Path.Combine(dir, "模拟 output.mp4"); File.WriteAllText(file, "offline mock media");
        Console.WriteLine("SGPROGRESS:75.0%"); Console.WriteLine("SGFILE:" + file);
        return 0;
    }
}
