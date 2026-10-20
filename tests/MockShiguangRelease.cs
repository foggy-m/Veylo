using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;

// Loads the unmodified release assembly, but never dispatches against real app data.
internal static class IsolatedRelease
{
    const BindingFlags Hidden = BindingFlags.Static | BindingFlags.NonPublic;

    static int Main(string[] args)
    {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        string release = Environment.GetEnvironmentVariable("SG_TEST_RELEASE");
        string app = Environment.GetEnvironmentVariable("SG_TEST_APP");
        string home = Environment.GetEnvironmentVariable("SG_TEST_RELEASE_HOME");
        if (string.IsNullOrEmpty(release) || string.IsNullOrEmpty(app) || string.IsNullOrEmpty(home)) return 2;
        var assembly = Assembly.LoadFrom(release);
        var bridge = assembly.GetType("ShiguangBridge", true);
        bridge.GetField("Home", Hidden).SetValue(null, home);
        bridge.GetField("AppDir", Hidden).SetValue(null, app);
        bridge.GetField("Jobs", Hidden).SetValue(null, Path.Combine(home, "jobs"));
        var json = new JavaScriptSerializer();
        string mode = Environment.GetEnvironmentVariable("SG_TEST_RELEASE_MODE") ?? "";
        if (mode == "resolve")
        {
            Console.WriteLine(json.Serialize(bridge.GetMethod("ResolveAppDir", Hidden).Invoke(null, null)));
            return 0;
        }
        if (mode == "metadata")
        {
            var dialog = bridge.GetNestedType("IFileDialog", BindingFlags.NonPublic);
            var create = bridge.GetMethod("SHCreateItemFromParsingName", Hidden);
            var marshal = (MarshalAsAttribute)Attribute.GetCustomAttribute(create.GetParameters().Last(), typeof(MarshalAsAttribute));
            Console.WriteLine(json.Serialize(new {
                version = assembly.GetName().Version.ToString(),
                fileVersion = System.Diagnostics.FileVersionInfo.GetVersionInfo(release).FileVersion,
                dialogMethods = dialog.GetMethods().OrderBy(method => method.MetadataToken).Select(method => method.Name).ToArray(),
                shellItemInterface = marshal != null && marshal.Value == UnmanagedType.Interface,
                liveLimitSeconds = bridge.GetMethod("LiveLimitSeconds", Hidden).Invoke(null, new object[] { 60 })
            }));
            return 0;
        }
        return (int)bridge.GetMethod("Main", Hidden).Invoke(null, new object[] { args });
    }
}
