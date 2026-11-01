using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

// 受保护外壳：
//  · 反调试（3 个 API + 定时复检）
//  · 防分析工具（进程名特征）
//  · 反虚拟机（驱动/注册表痕迹）
//  · 载荷 AES-256-CBC 加密，密钥由本机 MachineGuid 派生（换机器解不出来）
//  · 解密后校验 SHA-256，跑完自动清理
class Protector
{
    // ===== Win32 =====
    [DllImport("kernel32.dll")] static extern bool IsDebuggerPresent();
    [DllImport("kernel32.dll")] static extern bool CheckRemoteDebuggerPresent(IntPtr h, ref bool dbg);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("ntdll.dll")] static extern int NtQueryInformationProcess(IntPtr h, int cls, ref IntPtr info, int len, ref int ret);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr sec, uint disp, uint flags, IntPtr tmpl);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool MoveFileExW(string from, string to, int flags);

    static readonly string[] Tools = {
        "x64dbg", "x32dbg", "ollydbg", "ida", "ida64", "idag", "idaw", "windbg", "dnspy",
        "ilspy", "reflector", "processhacker", "procmon", "procexp", "httpdebugger",
        "fiddler", "wireshark", "charles", "cheatengine", "apimonitor", "scylla",
        "lordpe", "pestudio", "pe-bear", "ghidra", "binaryninja"
    };

    static bool Debugged()
    {
        if (IsDebuggerPresent()) return true;
        bool remote = false;
        if (CheckRemoteDebuggerPresent(GetCurrentProcess(), ref remote) && remote) return true;
        IntPtr info = IntPtr.Zero;
        int ret = 0;
        if (NtQueryInformationProcess(GetCurrentProcess(), 7, ref info, IntPtr.Size, ref ret) == 0 && info != IntPtr.Zero)
            return true;                       // ProcessDebugPort
        info = IntPtr.Zero;
        if (NtQueryInformationProcess(GetCurrentProcess(), 31, ref info, IntPtr.Size, ref ret) == 0 && info == IntPtr.Zero)
            return true;                       // ProcessDebugFlags == 0 表示在被调试
        return false;
    }

    static bool ToolRunning()
    {
        try
        {
            foreach (var p in Process.GetProcesses())
            {
                string name;
                try { name = (p.ProcessName ?? "").ToLowerInvariant(); } catch { continue; }
                if (name.Length == 0) continue;
                foreach (var t in Tools)
                    if (name == t || name.StartsWith(t)) return true;
            }
        }
        catch { }
        return false;
    }

    static bool InVm()
    {
        try
        {
            foreach (var dev in new[] { @"\\.\VBoxGuest", @"\\.\vmci", @"\\.\vmhgfs" })
            {
                IntPtr h = CreateFileW(dev, 0, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
                if (h != IntPtr.Zero && h.ToInt64() != -1) { CloseHandle(h); return true; }
            }
            foreach (var key in new[]
            {
                @"SYSTEM\CurrentControlSet\Services\VBoxGuest",
                @"SYSTEM\CurrentControlSet\Services\vmci",
                @"SYSTEM\CurrentControlSet\Services\vmhgfs",
                @"SYSTEM\CurrentControlSet\Services\VMTools"
            })
            {
                using (var k = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(key))
                    if (k != null) return true;
            }
        }
        catch { }
        return false;
    }

    static string MachineId()
    {
        try
        {
            using (var k = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Cryptography"))
                if (k != null)
                {
                    var g = k.GetValue("MachineGuid") as string;
                    if (!string.IsNullOrEmpty(g)) return g;
                }
        }
        catch { }
        return Environment.MachineName + "|" + Environment.UserName;
    }

    static byte[] Key(string saltB64)
    {
        var salt = Convert.FromBase64String(saltB64);
        var pwd = Encoding.UTF8.GetBytes("uting|" + MachineId());
        using (var kdf = new Rfc2898DeriveBytes(pwd, salt, 4096))
            return kdf.GetBytes(32);
    }

    static byte[] Decrypt(byte[] data, byte[] key)
    {
        using (var aes = new AesManaged())
        {
            aes.KeySize = 256; aes.Mode = CipherMode.CBC; aes.Padding = PaddingMode.PKCS7;
            aes.Key = key;
            int ivLen = 16;
            var iv = new byte[ivLen];
            Buffer.BlockCopy(data, 0, iv, 0, ivLen);
            aes.IV = iv;
            using (var dec = aes.CreateDecryptor())
                return dec.TransformFinalBlock(data, ivLen, data.Length - ivLen);
        }
    }

    static byte[] ReadResource(string name)
    {
        var asm = Assembly.GetExecutingAssembly();
        using (var s = asm.GetManifestResourceStream(name))
        {
            if (s == null) return null;
            using (var ms = new MemoryStream())
            {
                s.CopyTo(ms);
                return ms.ToArray();
            }
        }
    }

    static string ReadTextResource(string name)
    {
        var b = ReadResource(name);
        return b == null ? null : Encoding.UTF8.GetString(b).Trim();
    }

    static void Fail(string why)
    {
        try { File.AppendAllText(Path.Combine(Path.GetTempPath(), "uting_boot.log"), DateTime.Now + "  " + why + Environment.NewLine); } catch { }
        Environment.Exit(0);
    }

    [STAThread]
    static void Main()
    {
        if (Debugged()) Fail("debugger");
        if (InVm()) Fail("vm");
        if (ToolRunning()) Fail("tool");

        // 定时复检：运行期被附加调试器就一起退出
        var watcher = new Thread(() =>
        {
            while (true)
            {
                Thread.Sleep(3000);
                if (Debugged()) Fail("debugger-late");
            }
        });
        watcher.IsBackground = true;
        watcher.Start();

        var blob = ReadResource("payload.bin");
        var salt = ReadTextResource("salt.b64");
        var expectHash = ReadTextResource("hash.txt");
        if (blob == null || string.IsNullOrEmpty(salt)) Fail("no-payload");

        byte[] inner;
        try { inner = Decrypt(blob, Key(salt)); }
        catch { Fail("decrypt"); return; }

        using (var sha = SHA256.Create())
        {
            var got = BitConverter.ToString(sha.ComputeHash(inner)).Replace("-", "").ToLowerInvariant();
            if (!string.IsNullOrEmpty(expectHash) && got != expectHash) Fail("hash-mismatch");
        }

        string dir = Path.Combine(Path.GetTempPath(), "uting_" + Guid.NewGuid().ToString("N").Substring(0, 8));
        Directory.CreateDirectory(dir);
        string exe = Path.Combine(dir, "u" + Guid.NewGuid().ToString("N").Substring(0, 6) + ".exe");
        File.WriteAllBytes(exe, inner);
        try { File.SetAttributes(exe, FileAttributes.Hidden | FileAttributes.NotContentIndexed); } catch { }

        var psi = new ProcessStartInfo(exe)
        {
            WorkingDirectory = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location),
            UseShellExecute = false
        };
        Process child;
        try { child = Process.Start(psi); }
        catch { Fail("spawn"); return; }
        if (child == null) { Fail("spawn-null"); return; }

        // 子进程已映射进内存，删不掉就标记重启删除
        try { File.Delete(exe); } catch { MoveFileExW(exe, null, 4); }
        try { Directory.Delete(dir, true); } catch { }

        child.WaitForExit();
        try { File.Delete(exe); } catch { }
        try { Directory.Delete(dir, true); } catch { }
    }
}
