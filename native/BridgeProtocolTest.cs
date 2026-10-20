using System;
using System.IO;
using System.Text;
using System.Diagnostics;
using System.Web.Script.Serialization;
using System.Collections.Generic;

internal static class BridgeProtocolTest
{
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static int Main()
    {
        try {
            string exe = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "ShiguangBridge.exe");
            var start = new ProcessStartInfo(exe) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true };
            using (var process = Process.Start(start)) {
                var ping = Request(process, "{\"action\":\"ping\"}");
                if (!Equals(ping["ok"], true) || !Equals(ping["version"], "1.1.0")) throw new Exception("ping failed");
                foreach (string request in new[] { "{\"action\":\"unknown\"}", "{\"action\":\"job-status\",\"jobId\":\"../invalid\"}", "{\"action\":\"x-download\",\"url\":\"https://example.invalid/中文\"}" }) {
                    if (!Equals(Request(process, request)["ok"], false)) throw new Exception("invalid request accepted");
                }
                process.StandardInput.BaseStream.Close();
                if (!process.WaitForExit(5000) || process.ExitCode != 0) throw new Exception("host failed to exit");
            }
            Console.WriteLine("Native framing, multiple requests, Unicode and rejection checks passed (offline).");
            return 0;
        } catch (Exception e) { Console.Error.WriteLine(e.Message); return 1; }
    }
    static Dictionary<string, object> Request(Process process, string request) {
        byte[] body = Encoding.UTF8.GetBytes(request), header = BitConverter.GetBytes(body.Length);
        Stream input = process.StandardInput.BaseStream, output = process.StandardOutput.BaseStream;
        byte[] framed = new byte[header.Length + body.Length];
        Buffer.BlockCopy(header, 0, framed, 0, header.Length); Buffer.BlockCopy(body, 0, framed, header.Length, body.Length);
        input.Write(framed, 0, framed.Length); input.Flush();
        int size = BitConverter.ToInt32(ReadExact(output, 4), 0);
        if (size <= 0 || size > 65536) throw new Exception("invalid response size");
        return Json.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(ReadExact(output, size)));
    }
    static byte[] ReadExact(Stream stream, int size) {
        byte[] bytes = new byte[size]; int offset = 0;
        while (offset < size) { int count = stream.Read(bytes, offset, size - offset); if (count == 0) throw new EndOfStreamException(); offset += count; }
        return bytes;
    }
}
