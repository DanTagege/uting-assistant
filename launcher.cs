using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;

// 一键启动：检查服务 → 未启动则后台拉起 python（自带环境变量）→ 等健康检查 → 打开浏览器
class Launcher
{
    const string LocalUrl = "http://127.0.0.1:8710/";
    const string HealthUrl = "http://127.0.0.1:8710/api/health";

    static string Root
    {
        get { return Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location); }
    }

    static bool Healthy()
    {
        try
        {
            var req = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(HealthUrl);
            req.Timeout = 2000;
            req.ReadWriteTimeout = 2000;
            using (var resp = (System.Net.HttpWebResponse)req.GetResponse())
            {
                return (int)resp.StatusCode >= 200 && (int)resp.StatusCode < 300;
            }
        }
        catch { return false; }
    }

    static string LanIp()
    {
        try
        {
            foreach (var ni in System.Net.NetworkInformation.NetworkInterface.GetAllNetworkInterfaces())
            {
                if (ni.OperationalStatus != System.Net.NetworkInformation.OperationalStatus.Up) continue;
                var name = ni.Name ?? "";
                if (!(name.Contains("WLAN") || name.ToLower().Contains("wi-fi")
                      || name.Contains("Ethernet") || name.ToLower().Contains("ethernet"))) continue;
                foreach (var ua in ni.GetIPProperties().UnicastAddresses)
                {
                    if (ua.Address.AddressFamily != System.Net.Sockets.AddressFamily.InterNetwork) continue;
                    var s = ua.Address.ToString();
                    if (s.StartsWith("127.") || s.StartsWith("169.254.")) continue;
                    return s;
                }
            }
        }
        catch { }
        return "";
    }

    static void Open(string url)
    {
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch { }
    }

    static void Append(string file, string line)
    {
        try
        {
            var dir = Path.GetDirectoryName(file);
            if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
            File.AppendAllText(file, line + Environment.NewLine, Encoding.UTF8);
        }
        catch { }
    }

    static void Main()
    {
        Console.Title = "U听说助手";
        try { Console.OutputEncoding = Encoding.UTF8; } catch { }

        if (Healthy())
        {
            Console.WriteLine("Service already running, opening page.");
            Open(LocalUrl);
            Thread.Sleep(1200);
            return;
        }

        var py = Path.Combine(Root, @".venv\Scripts\python.exe");
        if (!File.Exists(py))
        {
            Console.WriteLine("python.exe not found: .venv\\Scripts\\python.exe  (put this exe in D:\\Ucampus)");
            Console.ReadKey();
            return;
        }

        Console.WriteLine("Starting service ...");
        var psi = new ProcessStartInfo(py, "run.py");
        psi.WorkingDirectory = Root;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        psi.StandardOutputEncoding = Encoding.UTF8;
        psi.StandardErrorEncoding = Encoding.UTF8;
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
        psi.EnvironmentVariables["UC_HOST"] = "0.0.0.0";
        psi.EnvironmentVariables["UC_UPSTREAM"] = "unipus";
        psi.EnvironmentVariables["UC_REAL_SUBMIT"] = "1";

        var logOut = Path.Combine(Root, @"data\logs\server.log");
        var logErr = Path.Combine(Root, @"data\logs\server.err.log");
        Append(logOut, "===== " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " launcher start =====");

        Process proc;
        try
        {
            proc = new Process();
            proc.StartInfo = psi;
            proc.EnableRaisingEvents = true;
            proc.OutputDataReceived += delegate(object s, DataReceivedEventArgs e) { if (e.Data != null) Append(logOut, e.Data); };
            proc.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e) { if (e.Data != null) Append(logErr, e.Data); };
            proc.Start();
            proc.BeginOutputReadLine();
            proc.BeginErrorReadLine();
        }
        catch (Exception ex)
        {
            Console.WriteLine("start failed: " + ex.Message);
            Console.ReadKey();
            return;
        }

        bool ok = false;
        for (int i = 0; i < 120; i++)
        {
            if (Healthy()) { ok = true; break; }
            if (proc.HasExited) break;
            Thread.Sleep(500);
            if (i % 10 == 0) Console.Write(".");
        }
        Console.WriteLine();

        if (!ok)
        {
            Console.WriteLine("service did not come up in 60s. see data\\logs\\server.err.log");
            Console.ReadKey();
            return;
        }

        Console.WriteLine("Running: " + LocalUrl + "   (PID " + proc.Id + ")");
        var ip = LanIp();
        if (ip.Length > 0)
            Console.WriteLine("Phone:   http://" + ip + ":8710/   (same Wi-Fi; Safari > Add to Home Screen)");
        Console.WriteLine("Stop with stop.cmd. Closing this window keeps the service running.");
        Open(LocalUrl);
        Thread.Sleep(2500);
    }
}
