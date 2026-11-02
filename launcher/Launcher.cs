using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// 版本号特性（AssemblyVersion / FileVersion / InformationalVersion）已移到
// `Version.cs`，由 `version-gen.ps1` 从包根 VERSION **自动生成**。
//
// 为什么改：这两个特性原先手写在这里，一天之内漂了两次（Launcher.cs 停在 1.1.11
// 而 VERSION 已经 1.1.16），而漂移只有右键看 exe 属性才发现。构建步骤不会忘。
//
// 下面两个带中文，仍留在这里（生成脚本保持 ASCII，避免编码问题）。
[assembly: System.Reflection.AssemblyProduct("简籍（Jianji）")]
[assembly: System.Reflection.AssemblyTitle("简籍启动器")]
[assembly: System.Reflection.AssemblyCompany("zhaocibaidi233")]
[assembly: System.Reflection.AssemblyCopyright("Copyright (c) 2026 zhaocibaidi233")]

// 自绘控件与调色板已拆到 Ui.cs（2026-10-26）。本文件只留 Program 与主窗体。
namespace JianjiLauncher
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            // 单实例：两个启动器会各自起一份后端、抢同一个端口，第二个必然只看到
            // 「服务没有完全启动」，用户却以为程序坏了。用命名 Mutex 挡在前面。
            // 作用域用默认（每会话）：不拦「另一个 Windows 用户同时跑」，那是另一个
            // 权限问题，拦了反而会让人以为程序被占用。
            bool first = true;
            Mutex single = null;
            try
            {
                single = new Mutex(true, "JianjiLauncher.SingleInstance", out first);
            }
            catch (Exception)
            {
                first = true;       // 拿不到命名内核对象（安全软件拦截等）时不能卡住启动
            }
            if (!first)
            {
                MessageBox.Show(
                    "简籍启动器已经在运行。\n\n请在任务栏找到已打开的窗口。"
                    + "重复启动会占用同一端口，第二个窗口将提示「服务没有完全启动」。",
                    "简籍启动器", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            try
            {
                RunLauncher();
            }
            finally
            {
                if (single != null)
                {
                    try { single.ReleaseMutex(); }
                    catch { }
                    single.Dispose();
                }
            }
        }

        private static void RunLauncher()
        {
            try { SetProcessDPIAware(); }
            catch { }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.ThreadException += delegate(object s, ThreadExceptionEventArgs e)
            {
                WriteCrash(e.Exception);
            };
            try
            {
                Application.Run(new LauncherForm());
            }
            catch (Exception ex)
            {
                WriteCrash(ex);
            }
        }

        internal static void WriteCrash(Exception ex)
        {
            try
            {
                File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                    "launcher-crash.log"), ex.ToString(), Encoding.UTF8);
            }
            catch { }
            MessageBox.Show(ex.Message, "简籍启动器错误",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
        }

        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();
    }

    /// <summary>
    /// 启动器的「青简纸感」色板。集中在一个地方，暗色/亮色调整时不用满文件找 hex。
    /// </summary>
    internal sealed class LauncherForm : Form
    {
        private int backendPort = 8000;
        private int frontendPort = 5173;
        private string BackendUrl { get { return "http://127.0.0.1:" + backendPort; } }
        private string FrontendUrl { get { return "http://127.0.0.1:" + frontendPort; } }

        // —— 数据库连接（启动器管理）——
        //
        // dbConfigured=false 时不干预后端：继续沿用系统环境里的 JIANJI_*，
        // 保证升级启动器不会改变已有部署。用户在「数据库」页保存过一次之后，
        // 启动器才把连接串显式注入子进程。
        private string dbHost = "localhost";
        private int dbPort = 5432;
        private string dbName = "jianji";
        private string dbUser = "postgres";
        private string dbPassword = "";
        private bool dbConfigured;
        private bool dbPasswordError;
        private bool dbBusy;
        private bool runPageActive = true;
        private string dbMode = "portable";
        private int pgPort = 55432;
        private string pgDataDir;
        private string pgPassword = "";
        private string pgVersion = "17.8";
        private readonly string oldConfigPath;

        /// <summary>
        /// 前端是否由后端托管（`frontend/dist/index.html` 存在 = 单端口形态）。
        ///
        /// 单端口形态下**不再启动独立的前端进程**：后端把 dist 直接挂在根路径，
        /// 静态资源与 /api 同源。收益不只是少一个进程——现场不需要 Node.js，
        /// 也不需要 `node_modules`（两百多 MB 的开发依赖），更不会有一个
        /// 「没有任何鉴权、却把请求原样转发给后端」的开发服务器对外监听。
        /// </summary>
        private bool SinglePort
        {
            get { return File.Exists(Path.Combine(root, "frontend", "dist", "index.html")); }
        }

        /// <summary>浏览器该打开的地址：单端口 = 后端端口，否则 = 前端端口。</summary>
        private string WebUrl { get { return SinglePort ? BackendUrl : FrontendUrl; } }

        /// <summary>
        /// 局域网开关。开启后两个服务都绑 0.0.0.0 而不是 127.0.0.1，
        /// 并把本机内网地址加进后端 CORS 白名单；关闭时恢复成只有本机能访问。
        /// 平时勾着不管等于把图书馆数据暴露给同网段所有人，所以默认关闭、改完需要重启服务才生效。
        /// </summary>
        private bool lanAccess;
        private ToggleSwitch lanSwitch;
        private Label lanLabel;
        private Label lanHint;
        private Button lanCopy;
        private Label lanNote;
        private Label logJump;
        private System.Windows.Forms.Timer copyTip;

        private string BindHost { get { return lanAccess ? "0.0.0.0" : "127.0.0.1"; } }

        /// <summary>本机内网地址，只解析一次：界面提示与 CORS 白名单必须用同一个值。</summary>
        private string lanIp;
        private string LanUrl
        {
            get
            {
                string ip = LanAddress();
                return ip == null ? null : "http://" + ip + ":" + (SinglePort ? backendPort : frontendPort);
            }
        }

        private string LanAddress()
        {
            if (lanIp == null)
            {
                List<string> all = LanAddresses();
                lanIp = all.Count > 0 ? all[0] : "";
            }
            return lanIp.Length == 0 ? null : lanIp;
        }

        /// <summary>
        /// 候选内网 IPv4，按「能用」程度排序。
        ///
        /// 为什么不能只取第一个私有地址：一台机器常有多块网卡（虚拟网卡、VPN、蓝牙），
        /// 取错就会把人引到一个连不上的地址上（实测本机 vgate0 的 172.30.x 排在以太网
        /// 192.168.x 前面）。判据是**有没有默认网关**：有网关的那块才真在往外通。
        /// 依次降级：有网关的私有地址 → 其它私有地址 → 非回环地址。
        /// </summary>
        private static List<string> LanAddresses()
        {
            List<string> withGateway = new List<string>();
            List<string> privates = new List<string>();
            List<string> others = new List<string>();
            try
            {
                foreach (NetworkInterface ni in NetworkInterface.GetAllNetworkInterfaces())
                {
                    if (ni.OperationalStatus != OperationalStatus.Up) continue;
                    if (ni.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
                    if (ni.NetworkInterfaceType == NetworkInterfaceType.Tunnel) continue;

                    IPInterfaceProperties props = ni.GetIPProperties();
                    bool hasGateway = false;
                    foreach (GatewayIPAddressInformation g in props.GatewayAddresses)
                    {
                        if (g.Address != null && g.Address.AddressFamily == AddressFamily.InterNetwork)
                        {
                            hasGateway = true;
                            break;
                        }
                    }

                    foreach (UnicastIPAddressInformation u in props.UnicastAddresses)
                    {
                        IPAddress ip = u.Address;
                        if (ip.AddressFamily != AddressFamily.InterNetwork) continue;
                        if (IPAddress.IsLoopback(ip)) continue;
                        string text = ip.ToString();
                        if (text.StartsWith("169.254.")) continue;   // APIPA：没拿到 DHCP 时的自分配地址
                        byte[] b = ip.GetAddressBytes();
                        bool internalNet = b[0] == 10 ||
                                           (b[0] == 192 && b[1] == 168) ||
                                           (b[0] == 172 && b[1] >= 16 && b[1] <= 31);
                        if (!internalNet) others.Add(text);
                        else if (hasGateway) withGateway.Add(text);
                        else privates.Add(text);
                    }
                }
            }
            catch { }

            List<string> ordered = new List<string>();
            ordered.AddRange(withGateway);
            ordered.AddRange(privates);
            ordered.AddRange(others);
            return ordered;
        }

        private readonly string root;
        private readonly string logsDir;
        private readonly string launcherLog;
        private readonly string configPath;
        private readonly object logLock = new object();
        /// <summary>日志轮转阈值：5 MB。现场 2 天就写到 941 KB，一学期能把磁盘写满，
        /// 而且日志框要全量读进内存才显示——文件越大打开越慢。</summary>
        private const long LogRotateBytes = 5L * 1024 * 1024;
        private int logWrites;
        /// <summary>现有多行日志里最长一行的字符数（判断是否需要横向滚动条）。</summary>
        private int logMaxChars;
        /// <summary>等宽字体单字宽（像素），量一次缓存。</summary>
        private int logCharWidth;
        private static readonly Regex Ansi = new Regex(
            "\u001b\\[[0-9;?]*[A-Za-z]", RegexOptions.Compiled);

        /// <summary>
        /// 健康检查产生的访问日志。启动器每 2.5 秒探一次 /api/health，
        /// uvicorn 的 access log 就跟着刷一行，一小时 1400 多行纯噪声，把有用的日志全冲走了。
        /// </summary>
        private static readonly Regex HealthNoise = new Regex(
            "\"(GET|OPTIONS) /api/health[^\"]*\" [0-9]{3}", RegexOptions.Compiled);

        /// <summary>
        /// 从 JIANJI_DATABASE_URL 还原表单初始值。只解析展示需要的信息，
        /// 客户端参数（如 ?sslmode=require）原样保留在环境里，不参与显示。
        /// </summary>
        private static readonly Regex DbUrlPattern = new Regex(
            @"^(?<scheme>[A-Za-z][A-Za-z0-9+.\-]*)://" +
            @"(?:(?<user>[^:@/]*)(?::(?<pass>[^@/]*))?@)?" +
            @"(?<host>\[[^\]]+\]|[^:/]+)(?::(?<port>[0-9]+))?/(?<db>[^?]+)",
            RegexOptions.Compiled);

        private const int EmGetFirstVisibleLine = 0xCE;
        private const int EmLineScroll = 0xB6;

        private Process backendProc;
        private Process frontendProc;
        private bool starting;
        private bool stopping;
        private bool polling;
        private bool everStarted;

        private Label backendStatus;
        private Label backendDetail;
        private Label backendDbInfo;
        private string actualDbLabel = "";
        private Label frontendStatus;
        private Label frontendDetail;
        private Label footer;
        private Panel backendDot;
        private Panel frontendDot;
        private TextBox backendPortBox;
        private TextBox frontendPortBox;
        private Control frontendPortWrap;
        private PillLabel frontendBadge;
        private TextBox logBox;
        private RoundedButton startBtn;
        private RoundedButton stopBtn;
        private System.Windows.Forms.Timer poll;
        private PillLabel livePill;
        private IconLink logOpen;
        private Label logAuto;

        private Label heading;
        private Label subheading;
        private SegmentedTabs pageTabs;
        private IconChip pageIconChip;
        private readonly List<Control> runPageControls = new List<Control>();
        private readonly List<Rectangle> runCardShadows = new List<Rectangle>();
        private readonly List<Rectangle> dbCardShadows = new List<Rectangle>();
        private readonly List<Rectangle> commonCardShadows = new List<Rectangle>();

        private RoundedPanel dbCard;
        private TextBox dbHostBox;
        private TextBox dbPortBox;
        private TextBox dbNameBox;
        private TextBox dbUserBox;
        private TextBox dbPasswordBox;
        private CheckBox dbModeCheck;
        private PillLabel dbStatusStrip;
        private RoundedButton dbTestBtn;
        private RoundedButton dbSaveBtn;
        private RoundedButton dbNewBtn;
        private RoundedButton dbInitBtn;
        private RoundedButton dbPickBtn;
        private bool dbFormLoading;
        private string dbStatusText = "";
        private Color dbStatusColor = Palette.Faint;
        /// <summary>最近一次健康检查：库结构是否落后于代码（见 CheckBackend）。</summary>
        private bool schemaOk = true;
        private string schemaHead = "";
        /// <summary>上一次的提示条状态，用来把日志降成「只在变化时记一行」。</summary>
        private bool schemaWarned;
        /// <summary>库结构落后时在运行页顶部常驻的提示条（不落后就隐藏）。</summary>
        private Label schemaWarning;
        private RoundedButton schemaUpgradeBtn;

        public LauncherForm()
        {
            root = FindRoot();
            logsDir = Path.Combine(root, "launcher-logs");
            launcherLog = Path.Combine(logsDir, "launcher.log");
            oldConfigPath = Path.Combine(root, "launcher-config.json");
            configPath = Path.Combine(root, "data", "launcher-config.json");
            try { Directory.CreateDirectory(Path.Combine(root, "data")); }
            catch { }
            try { Directory.CreateDirectory(logsDir); }
            catch { }
            RotateLogIfNeeded();        // 开窗之前先轮转，避免「打开就一大坨历史」
            LoadConfig();

            BuildUi();
            Load += delegate { Log("启动器已打开：" + root); };
            Shown += delegate
            {
                Log("就绪，等待启动。");
                // 只检查已有服务，不自动启动（用户 2026-10-22 要求）
                BeginInvoke(new Action(delegate { PollTick(null, EventArgs.Empty); }));
            };
            FormClosing += OnFormClosing;

            poll = new System.Windows.Forms.Timer();
            poll.Interval = 2500;
            poll.Tick += PollTick;
            poll.Start();

            copyTip = new System.Windows.Forms.Timer();
            copyTip.Interval = 1600;
            copyTip.Tick += delegate { copyTip.Stop(); ResetCopyLabel(); };
        }

        private static string FindRoot()
        {
            string dir = AppDomain.CurrentDomain.BaseDirectory;
            for (int i = 0; i < 5 && !String.IsNullOrEmpty(dir); i++)
            {
                if (File.Exists(Path.Combine(dir, "backend", "app", "main.py")) &&
                    File.Exists(Path.Combine(dir, "frontend", "package.json")))
                    return dir;
                DirectoryInfo parent = Directory.GetParent(dir);
                dir = parent == null ? null : parent.FullName;
            }
            return AppDomain.CurrentDomain.BaseDirectory;
        }

        private void LoadConfig()
        {
            // 没有保存过数据库设置时，先用系统环境里的连接串填表单：
            // 本机 JIANJI_DATABASE_URL 可能已指向某个现有库，若这里写死
            // jianji，界面显示的库和实际连的库会不是同一个。
            bool hasExternalEnv = !String.IsNullOrEmpty(
                Environment.GetEnvironmentVariable("JIANJI_DATABASE_URL"));
            dbMode = LauncherConfig.ReadDbMode(null, hasExternalEnv);
            LoadDbFromEnvironment();
            string loadPath = File.Exists(configPath)
                ? configPath
                : (File.Exists(oldConfigPath) ? oldConfigPath : null);
            if (loadPath == oldConfigPath && !File.Exists(configPath))
            {
                try
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(configPath));
                    File.Copy(oldConfigPath, configPath);
                    Log("已把 launcher-config.json 迁移到 data/launcher-config.json。");
                }
                catch (Exception ex)
                {
                    Log("⚠ 迁移 launcher-config.json 失败：" + ex.Message);
                }
            }
            if (loadPath == null)
            {
                pgDataDir = LauncherConfig.ResolvePortableDataDir(root, "data\\pgdata");
                return;
            }
            try
            {
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                Dictionary<string, object> cfg = serializer.Deserialize<Dictionary<string, object>>(
                    File.ReadAllText(loadPath));
                if (cfg == null) return;
                int bp = backendPort, fp = frontendPort;
                if (cfg.ContainsKey("backend_port") &&
                    Int32.TryParse(Convert.ToString(cfg["backend_port"]), out bp) == false)
                    bp = backendPort;
                if (cfg.ContainsKey("frontend_port") &&
                    Int32.TryParse(Convert.ToString(cfg["frontend_port"]), out fp) == false)
                    fp = frontendPort;
                if (bp >= 1 && bp <= 65535 && fp >= 1 && fp <= 65535 && bp != fp)
                {
                    backendPort = bp;
                    frontendPort = fp;
                }
                lanAccess = LauncherConfig.ReadBool(cfg, "lan_access", lanAccess);
                dbMode = LauncherConfig.ReadDbMode(cfg, hasExternalEnv);
                pgPort = LauncherConfig.ReadInt(cfg, "pg_port", 55432);
                pgDataDir = LauncherConfig.ResolvePortableDataDir(root,
                    ConfigText(cfg, "pg_data_dir", "data\\pgdata"));
                pgVersion = ConfigText(cfg, "pg_version", "17.8");
                if (cfg.ContainsKey("pg_password"))
                {
                    string stored = Convert.ToString(cfg["pg_password"]) ?? "";
                    if (stored.StartsWith("dpapi:", StringComparison.Ordinal))
                    {
                        try { pgPassword = UnprotectDbPassword(stored); }
                        catch (Exception ex)
                        {
                            pgPassword = "";
                            Log("⚠ 内置数据库口令无法解密：" + ex.Message);
                        }
                    }
                }

                bool hasDbHost = cfg.ContainsKey("db_host");
                if (hasDbHost || cfg.ContainsKey("db_name") || cfg.ContainsKey("db_configured"))
                {
                    bool configured = true;
                    if (cfg.ContainsKey("db_configured"))
                        configured = LauncherConfig.ReadBool(cfg, "db_configured", configured);
                    dbConfigured = configured && (hasDbHost || cfg.ContainsKey("db_name"));

                    if (hasDbHost) dbHost = ConfigText(cfg, "db_host", dbHost);
                    if (cfg.ContainsKey("db_name")) dbName = ConfigText(cfg, "db_name", dbName);
                    if (cfg.ContainsKey("db_user")) dbUser = ConfigText(cfg, "db_user", dbUser);
                    int port = dbPort;
                    if (cfg.ContainsKey("db_port"))
                        Int32.TryParse(Convert.ToString(cfg["db_port"]), out port);
                    if (port >= 1 && port <= 65535) dbPort = port;

                    if (cfg.ContainsKey("db_password"))
                    {
                        string stored = Convert.ToString(cfg["db_password"]) ?? "";
                        if (stored.Length == 0)
                        {
                            dbPassword = "";
                        }
                        else if (stored.StartsWith("dpapi:", StringComparison.Ordinal))
                        {
                            try
                            {
                                dbPassword = UnprotectDbPassword(stored);
                            }
                            catch (Exception ex)
                            {
                                dbPassword = "";
                                dbPasswordError = true;
                                Log("⚠ 保存的数据库口令无法解密（" + ex.Message +
                                    "），请在「数据库」页重新输入。");
                            }
                        }
                        else
                        {
                            // 不接收明文口令：宁可让用户重输一次，也不把旧格式继续用下去
                            dbPassword = "";
                            dbPasswordError = true;
                            Log("⚠ launcher-config.json 中的数据库口令不是加密格式，已忽略。");
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Log("⚠ 读取 launcher-config.json 失败（" + ex.Message + "），使用默认配置。");
            }
            if (String.IsNullOrEmpty(pgDataDir))
                pgDataDir = LauncherConfig.ResolvePortableDataDir(root, "data\\pgdata");
        }

        private static string ConfigText(Dictionary<string, object> cfg, string key, string fallback)
        {
            string value = Convert.ToString(cfg[key]);
            return String.IsNullOrEmpty(value) ? fallback : value;
        }

        /// <summary>
        /// 首次打开数据库页时，把系统环境里的连接还原成表单初始值。
        /// 只用于“显示实际会连到哪里”，不会自动把环境配置写成启动器配置。
        /// </summary>
        private void LoadDbFromEnvironment()
        {
            string url = Environment.GetEnvironmentVariable("JIANJI_DATABASE_URL");
            string envPassword = Environment.GetEnvironmentVariable("JIANJI_DB_PASSWORD");
            bool parsed = false;
            if (!String.IsNullOrEmpty(url))
            {
                Match match = DbUrlPattern.Match(url.Trim());
                if (match.Success)
                {
                    parsed = true;
                    string host = Uri.UnescapeDataString(match.Groups["host"].Value.Trim());
                    if (host.StartsWith("[") && host.EndsWith("]"))
                        host = host.Substring(1, host.Length - 2);
                    if (host.Length > 0) dbHost = host;

                    int port;
                    if (Int32.TryParse(match.Groups["port"].Value, out port) &&
                        port >= 1 && port <= 65535)
                        dbPort = port;

                    string user = Uri.UnescapeDataString(match.Groups["user"].Value);
                    if (user.Length > 0) dbUser = user;

                    string name = Uri.UnescapeDataString(match.Groups["db"].Value);
                    if (name.Length > 0) dbName = name;

                    string password = Uri.UnescapeDataString(match.Groups["pass"].Value);
                    if (password.Length > 0) dbPassword = password;
                    else if (!String.IsNullOrEmpty(envPassword)) dbPassword = envPassword;
                }
            }
            if (!parsed && !String.IsNullOrEmpty(envPassword)) dbPassword = envPassword;
            if (!String.IsNullOrEmpty(url) && !parsed)
                Log("⚠ 无法解析 JIANJI_DATABASE_URL，数据库页使用默认值。");
        }

        private string EffectiveDbPassword()
        {
            if (!String.IsNullOrEmpty(dbPassword)) return dbPassword;
            string env = Environment.GetEnvironmentVariable("JIANJI_DB_PASSWORD");
            return env == null ? "" : env;
        }

        private string DbTargetLabel()
        {
            return dbUser + "@" + dbHost + ":" + dbPort + "/" + dbName;
        }

        private string BuildDatabaseUrl(string host, int port, string name,
            string user, string password)
        {
            string hostPart = host;
            if (hostPart.StartsWith("[") && hostPart.EndsWith("]"))
            {
                // 已经是 IPv6 字面量
            }
            else if (hostPart.IndexOf(':') >= 0)
            {
                hostPart = "[" + hostPart + "]";
            }
            string auth = Uri.EscapeDataString(user);
            if (!String.IsNullOrEmpty(password))
                auth += ":" + Uri.EscapeDataString(password);
            return "postgresql+psycopg2://" + auth + "@" + hostPart + ":" + port + "/" +
                Uri.EscapeDataString(name);
        }

        private static readonly byte[] DbPasswordEntropy =
            Encoding.UTF8.GetBytes("JianjiLauncher.Database");

        private static string ProtectDbPassword(string password)
        {
            if (String.IsNullOrEmpty(password)) return "";
            byte[] data = ProtectedData.Protect(Encoding.UTF8.GetBytes(password),
                DbPasswordEntropy, DataProtectionScope.CurrentUser);
            return "dpapi:" + Convert.ToBase64String(data);
        }

        private static string UnprotectDbPassword(string stored)
        {
            if (String.IsNullOrEmpty(stored)) return "";
            if (!stored.StartsWith("dpapi:", StringComparison.Ordinal))
                throw new CryptographicException("不是受保护的口令");
            byte[] data = Convert.FromBase64String(stored.Substring("dpapi:".Length));
            byte[] clear = ProtectedData.Unprotect(data, DbPasswordEntropy,
                DataProtectionScope.CurrentUser);
            return Encoding.UTF8.GetString(clear);
        }

        private bool TryReadPorts(out int bp, out int fp)
        {
            bp = backendPort;
            fp = frontendPort;
            if (!Int32.TryParse(backendPortBox.Text.Trim(), out bp) ||
                bp < 1 || bp > 65535)
            {
                ShowError("后端端口必须是 1–65535 的数字。");
                return false;
            }
            if (!Int32.TryParse(frontendPortBox.Text.Trim(), out fp) ||
                fp < 1 || fp > 65535)
            {
                ShowError("前端端口必须是 1–65535 的数字。");
                return false;
            }
            if (bp == fp)
            {
                ShowError("后端与前端不能使用同一个端口。");
                return false;
            }
            return true;
        }

        private void SaveConfig()
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                sb.Append("{\r\n");
                sb.Append("  \"backend_port\": ").Append(backendPort).Append(",\r\n");
                sb.Append("  \"frontend_port\": ").Append(frontendPort).Append(",\r\n");
                sb.Append("  \"lan_access\": ").Append(lanAccess ? "true" : "false");
                sb.Append(",\r\n  \"db_mode\": ").Append(JsonString(dbMode));
                if (dbMode == "portable")
                {
                    sb.Append(",\r\n  \"pg_port\": ").Append(pgPort);
                    sb.Append(",\r\n  \"pg_data_dir\": ").Append(
                        JsonString(RelativeToRoot(pgDataDir)));
                    sb.Append(",\r\n  \"pg_version\": ").Append(JsonString(pgVersion));
                    if (!String.IsNullOrEmpty(pgPassword))
                    {
                        sb.Append(",\r\n  \"pg_password\": ").Append(
                            JsonString(ProtectDbPassword(pgPassword)));
                    }
                }
                if (dbMode == "external" && dbConfigured)
                {
                    string protectedPassword = "";
                    try { protectedPassword = ProtectDbPassword(dbPassword); }
                    catch (Exception ex)
                    {
                        Log("⚠ 数据库口令加密失败，本次不保存口令：" + ex.Message);
                    }
                    sb.Append(",\r\n  \"db_configured\": true");
                    sb.Append(",\r\n  \"db_host\": ").Append(JsonString(dbHost));
                    sb.Append(",\r\n  \"db_port\": ").Append(dbPort);
                    sb.Append(",\r\n  \"db_name\": ").Append(JsonString(dbName));
                    sb.Append(",\r\n  \"db_user\": ").Append(JsonString(dbUser));
                    sb.Append(",\r\n  \"db_password\": ").Append(JsonString(protectedPassword));
                }
                sb.Append("\r\n}\r\n");
                File.WriteAllText(configPath, sb.ToString(), new UTF8Encoding(false));
            }
            catch (Exception ex)
            {
                Log("保存设置失败：" + ex.Message);
            }
        }

        private static string JsonString(string value)
        {
            return new JavaScriptSerializer().Serialize(value ?? "");
        }

        private string RelativeToRoot(string path)
        {
            try
            {
                string full = Path.GetFullPath(path);
                string prefix = Path.GetFullPath(root).TrimEnd('\\') + "\\";
                return full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)
                    ? full.Substring(prefix.Length) : full;
            }
            catch { return path; }
        }

        private void ResetPortBoxes()
        {
            backendPortBox.Text = backendPort.ToString();
            frontendPortBox.Text = frontendPort.ToString();
        }

        private void PortBoxLeave(object sender, EventArgs e)
        {
            if (starting) return;
            int bp;
            int fp;
            if (!TryReadPorts(out bp, out fp))
            {
                ResetPortBoxes();
                return;
            }
            if (bp == backendPort && fp == frontendPort) return;
            backendPort = bp;
            frontendPort = fp;
            SaveConfig();
            UpdateLanHint();
            Log("端口已更新：后端 " + backendPort + "，前端 " + frontendPort);
            PollTick(null, EventArgs.Empty);
        }

        /// <summary>
        /// 局域网开关：只改绑定地址与 CORS 来源，不改端口。
        /// 已经跑起来的进程参数是启动那一刻定的，所以运行中切换必须重启服务才生效。
        /// </summary>
        private void LanToggled(object sender, EventArgs e)
        {
            lanAccess = lanSwitch.Checked;
            SaveConfig();
            UpdateLanHint();
            Log(lanAccess ? "已开启局域网访问。" : "已关闭局域网访问，恢复为仅本机。");
            if (lanAccess) EnsureFirewallRule();
            if (starting) return;
            bool dbOk;
            bool running = CheckBackend(out dbOk) || CheckFrontend();
            if (running) Log("该开关在启动时生效，重启服务后才会实际改变。");
        }

        // —— 防火墙入站放行 ——
        //
        // 2026-10-26 真机踩到：开了局域网，手机同网段却连不上（ping 与 TCP 全超时），
        // 但本机用内网 IP 访问是通的。关掉防火墙立刻通 —— 是 Windows 防火墙拦的。
        //
        // 为什么光加「端口放行」不够：这台机器上有两条**名为 Python 的阻止规则**
        // （其他软件自带的 python 路径）。Windows 防火墙里**阻止优先于允许**，
        // 而且程序规则会按进程名匹配到我们自己的 python.exe。实测：
        //   - 加 TCP 8781 放行 → 仍超时
        //   - 加程序级放行   → 仍超时
        //   - 禁用那两条阻止 → 通
        // 所以这里要同时做两件事：放行端口 + **把命中 python 的阻止规则临时禁用**。
        //
        // 禁用而不是删除：那是别的软件的规则，用户可能还要用；我们的放行规则也不该
        // 越权去改别人的配置。日志里写明改了哪几条，便于回退。

        private const string FirewallRuleName = "简籍（Jianji） 后端";

        /// <summary>确保「本端口可被局域网访问」：缺规则就加，有冲突的阻止规则就禁用。</summary>
        private void EnsureFirewallRule()
        {
            try
            {
                int port = backendPort;
                // 已有同名规则且端口一致 → 什么都不用做（避免每次勾选都弹 UAC）
                string existing = RunNetsh("advfirewall firewall show rule name=\""
                                           + FirewallRuleName + "\"");
                if (existing != null && existing.IndexOf("LocalPort:", StringComparison.OrdinalIgnoreCase) < 0)
                {
                    // 中文系统上字段名是「本地端口」；两种都认
                }
                bool hasRule = existing != null
                    && (existing.Contains(port.ToString())
                        || existing.IndexOf("本地端口", StringComparison.Ordinal) >= 0);

                if (!hasRule)
                {
                    string add = RunNetsh("advfirewall firewall add rule name=\""
                                          + FirewallRuleName + "\" dir=in action=allow protocol=TCP localport="
                                          + port + " profile=any");
                    if (add == null)
                    {
                        Log("⚠ 无法自动添加防火墙规则（可能缺少管理员权限）。"
                            + "如局域网访问不通，请手动放行 TCP " + port + "。");
                        return;
                    }
                    Log("已添加防火墙放行规则：" + FirewallRuleName + "（TCP " + port + "）");
                }

                DisableConflictingPythonBlocks();
            }
            catch (Exception ex)
            {
                // 防火墙配置失败不能挡住启动：日志留痕，让用户知道去手动处理
                Log("⚠ 防火墙自动配置失败：" + ex.Message
                    + "。如局域网访问不通，请手动放行 TCP " + backendPort + "。");
            }
        }

        /// <summary>
        /// 禁用「程序为 python.exe 的阻止规则」。
        ///
        /// 这类规则通常是别的软件安装时创建的，但 Windows 防火墙**按进程名匹配**，
        /// 会把本程序的 python.exe 一起拦掉，且**阻止优先于允许**——只加放行规则
        /// 永远打不开。只禁用、不删除，并在日志里逐条列出，用户可自行恢复。
        /// </summary>
        /// <summary>
        /// 禁用「程序为 python.exe 的阻止规则」。
        ///
        /// 这类规则通常是别的软件安装时创建的，但 Windows 防火墙**按进程名匹配**，
        /// 会把本程序的 python.exe 一起拦掉，且**阻止优先于允许**——只加放行规则
        /// 永远打不开。只禁用、不删除，并在日志里逐条列出，用户可自行恢复。
        ///
        /// 为什么用 PowerShell 而不是解析 netsh 文本：netsh 的 verbose 输出里
        /// 规则块之间**没有固定分隔符**（分隔线只出现在第一条后面），按行状态机
        /// 解析会漏。第一版就是这么写的，42 条含 python 的行一条都没认出来。
        /// PowerShell 直接返回结构化对象，可靠得多。
        /// </summary>
        private void DisableConflictingPythonBlocks()
        {
            // 只取「入站 + 阻止 + 已启用 + 程序名含 python」的规则，其余不管。
            string script =
                "Get-NetFirewallRule -Direction Inbound -Action Block -Enabled True -ErrorAction SilentlyContinue | " +
                "Where-Object { $_.DisplayName -eq 'Python' } | " +
                "ForEach-Object { $_.Name + '|' + $_.DisplayName }";
            string listing = RunPowerShell(script);
            if (listing == null)
            {
                Log("[防火墙] 无法查询阻止规则（PowerShell 不可用）。");
                return;
            }

            int disabled = 0;
            foreach (string raw in listing.Split(new string[] { "\r\n", "\n" }, StringSplitOptions.None))
            {
                string line = raw.Trim();
                if (line.Length == 0 || line.IndexOf('|') < 0) continue;
                string[] parts = line.Split('|');
                string ruleName = parts[0].Trim();
                string display = parts.Length > 1 ? parts[1].Trim() : ruleName;
                if (ruleName.Length == 0) continue;

                // 用 PowerShell 直接禁用：规则名里带 {GUID} 和完整路径，
                // 拼进 netsh 命令行会被引号与花括号搞坏（实测「无法禁用」）。
                // PowerShell 传对象过去，不经过命令行解析。
                string safe = ruleName.Replace("'", "''");
                string off = RunPowerShell(
                    "$r = Get-NetFirewallRule -Direction Inbound -Action Block -Enabled True -ErrorAction SilentlyContinue"
                    + " | Where-Object { $_.Name -eq '" + safe + "' };"
                    + " if ($r) { $r | Disable-NetFirewallRule; 'OK' }");
                if (off != null && off.Trim().Length > 0)
                {
                    disabled++;
                    Log("已禁用冲突的阻止规则：" + display);
                }
                else
                {
                    Log("⚠ 无法禁用阻止规则：" + display
                        + "。如局域网访问不通，请在「高级安全 Windows Defender 防火墙」里手动禁用。");
                }
            }
            if (disabled == 0) Log("未发现冲突的 python 阻止规则。");
        }

        /// <summary>跑一段 PowerShell，返回 stdout；失败返回 null。</summary>
        private static string RunPowerShell(string script)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "powershell.exe";
                psi.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \""
                                + script.Replace("\"", "\\\"") + "\"";
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.StandardOutputEncoding = Encoding.UTF8;
                using (Process p = Process.Start(psi))
                {
                    string output = p.StandardOutput.ReadToEnd();
                    p.WaitForExit(15000);
                    return p.HasExited && p.ExitCode == 0 ? output : null;
                }
            }
            catch
            {
                return null;
            }
        }

        /// <summary>
        /// 跑一条 netsh 命令，成功返回输出，失败返回 null。
        /// 用 CreateNoWindow + 捕获输出，避免弹黑框。
        /// </summary>
        private static string RunNetsh(string args)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "netsh.exe";
                psi.Arguments = args;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.StandardOutputEncoding = Encoding.Default;   // netsh 输出是 ANSI
                using (Process p = Process.Start(psi))
                {
                    string output = p.StandardOutput.ReadToEnd();
                    p.WaitForExit(8000);
                    return p.HasExited && p.ExitCode == 0 ? output : null;
                }
            }
            catch
            {
                return null;
            }
        }

        private void UpdateLanHint()
        {
            if (lanHint == null) return;
            if (!lanAccess)
            {
                lanHint.Text = "仅本机可访问。勾选「局域网访问」后，同网段的电脑可用内网地址打开（重启服务生效）。";
                lanHint.ForeColor = Palette.Muted;
                lanHint.Width = 700;
                lanHint.Visible = runPageActive;
                lanCopy.Visible = false;
                lanNote.Visible = false;
                return;
            }
            string url = LanUrl;
            if (url == null)
            {
                lanHint.Text = "已开启局域网访问，但没取到本机内网地址，请检查网络连接。";
                lanHint.ForeColor = Palette.Danger;
                lanHint.Width = 700;
                lanHint.Visible = runPageActive;
                lanCopy.Visible = false;
                lanNote.Visible = false;
                return;
            }
            lanHint.Text = "局域网地址：" + url;
            lanHint.ForeColor = Palette.AccentDeep;
            int w = TextRenderer.MeasureText(lanHint.Text, lanHint.Font).Width;
            lanHint.Width = w + 4;
            lanHint.Visible = runPageActive;
            lanCopy.Left = lanHint.Right + 8;
            lanCopy.Top = lanHint.Top;
            lanCopy.Visible = runPageActive;
            lanNote.Left = lanCopy.Right + 10;
            lanNote.Top = lanHint.Top;
            lanNote.Visible = runPageActive;
        }

        /// <summary>
        /// 复制局域网地址。剪贴板可能被别的程序短暂占用（Win32 的经典问题），
        /// 所以重试几次再判失败，而不是第一次被拒就报错。
        /// </summary>
        private void CopyLanUrl()
        {
            string url = LanUrl;
            if (url == null) return;
            for (int attempt = 0; attempt < 3; attempt++)
            {
                try
                {
                    Clipboard.SetDataObject(url, true, 4, 60);
                    lanCopy.Text = "已复制";
                    lanCopy.ForeColor = Palette.Success;
                    copyTip.Stop();
                    copyTip.Start();
                    Log("已复制局域网地址：" + url);
                    return;
                }
                catch (Exception ex)
                {
                    if (attempt == 2)
                    {
                        lanCopy.Text = "复制失败";
                        lanCopy.ForeColor = Palette.Danger;
                        copyTip.Stop();
                        copyTip.Start();
                        Log("复制局域网地址失败：" + ex.Message);
                    }
                    else
                    {
                        Thread.Sleep(60);
                    }
                }
            }
        }

        private void ResetCopyLabel()
        {
            if (lanCopy == null || lanCopy.IsDisposed) return;
            lanCopy.Text = "复制";
            lanCopy.ForeColor = Palette.Primary;
        }

        private TextBox AddPortBox(Panel parent, int port, int x, int y)
        {
            RoundedPanel wrap = new RoundedPanel();
            wrap.Location = new Point(x, y);
            wrap.Size = new Size(72, 30);
            wrap.Radius = 8;
            wrap.FillColor = Palette.SurfaceWarm;
            wrap.BorderColor = Palette.LineStrong;
            wrap.BackColor = Palette.Surface;
            parent.Controls.Add(wrap);

            TextBox box = new TextBox();
            box.Text = port.ToString();
            box.Location = new Point(8, 4);
            box.Size = new Size(56, 22);
            box.MaxLength = 5;
            box.TextAlign = HorizontalAlignment.Center;
            box.BorderStyle = BorderStyle.None;
            box.BackColor = Palette.SurfaceWarm;
            box.ForeColor = Palette.Text;
            box.Font = new Font("Consolas", 10F, FontStyle.Regular, GraphicsUnit.Point);
            box.KeyPress += delegate(object s, KeyPressEventArgs e)
            {
                if (!Char.IsControl(e.KeyChar) && !Char.IsDigit(e.KeyChar))
                    e.Handled = true;
            };
            box.Enter += delegate
            {
                wrap.BorderColor = Palette.Primary;
                wrap.FillColor = Color.White;
                box.BackColor = Color.White;
                wrap.Invalidate();
            };
            box.Leave += delegate
            {
                wrap.BorderColor = Palette.LineStrong;
                wrap.FillColor = Palette.SurfaceWarm;
                box.BackColor = Palette.SurfaceWarm;
                wrap.Invalidate();
            };
            box.Leave += PortBoxLeave;
            wrap.Controls.Add(box);
            return box;
        }

        private void BuildUi()
        {
            SuspendLayout();
            Text = "简籍启动器";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.None;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(800, 660);
            BackColor = Palette.BgTop;
            Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            AutoScaleMode = AutoScaleMode.Dpi;
            DoubleBuffered = true;
            SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.UserPaint, true);

            string iconPath = Path.Combine(root, "launcher", "jianji.ico");
            if (File.Exists(iconPath))
            {
                try { Icon = new Icon(iconPath); }
                catch { }
            }

            BuildTitleBar(iconPath);

            pageIconChip = new IconChip();
            pageIconChip.Location = new Point(30, 70);
            pageIconChip.Icon = UiIcon.Server;
            Controls.Add(pageIconChip);

            heading = new Label();
            heading.Text = "运行控制台";
            heading.Font = new Font("Microsoft YaHei UI", 22F, FontStyle.Bold, GraphicsUnit.Point);
            heading.ForeColor = Palette.Ink;
            heading.AutoSize = true;
            heading.BackColor = Palette.BgTop;
            heading.Location = new Point(78, 70);
            Controls.Add(heading);

            subheading = new Label();
            subheading.Text = "服务状态与启动";
            subheading.Font = new Font("Microsoft YaHei UI", 9.5F, FontStyle.Regular, GraphicsUnit.Point);
            subheading.ForeColor = Palette.Muted;
            subheading.AutoSize = true;
            subheading.BackColor = Palette.BgTop;
            subheading.Location = new Point(81, 112);
            Controls.Add(subheading);

            pageTabs = new SegmentedTabs();
            pageTabs.Location = new Point(576, 74);
            pageTabs.Size = new Size(192, 36);
            pageTabs.SetLabels("运行控制", "数据库");
            pageTabs.SetIcons(UiIcon.Power, UiIcon.Database);
            pageTabs.SelectedIndexChanged += delegate
            {
                SwitchPage(pageTabs.SelectedIndex);
            };
            Controls.Add(pageTabs);

            RoundedPanel statusPanel = new RoundedPanel();
            statusPanel.Location = new Point(32, 136);
            statusPanel.Size = new Size(736, 176);
            statusPanel.Radius = 14;
            statusPanel.FillColor = Palette.Surface;
            statusPanel.BorderColor = Palette.Line;
            statusPanel.BackColor = Palette.BgTop;
            Controls.Add(statusPanel);
            runPageControls.Add(statusPanel);
            runCardShadows.Add(statusPanel.Bounds);

            // 两行等高（88px）：圆点居整块文字的中线，右列（地址 + 端口）也按同一中线对齐。
            backendDot = AddDot(statusPanel, 22, 36, 11);
            Label backendTitle = AddCardTitle(statusPanel, "后端服务", 48, 22);
            backendStatus = AddInlineStatus(statusPanel, backendTitle, "未启动", Palette.Faint);
            AddCardSub(statusPanel, "本机服务 · 每 2.5 秒检测", 48, 47);
            backendDetail = AddCardDetail(statusPanel, "127.0.0.1", 516, 36);
            AlignHost(backendDetail);
            backendPortBox = AddPortBox(statusPanel, backendPort, 636, 29);

            backendDbInfo = new Label();
            backendDbInfo.AutoSize = true;
            backendDbInfo.Font = new Font("Microsoft YaHei UI", 8.5F,
                FontStyle.Regular, GraphicsUnit.Point);
            backendDbInfo.ForeColor = Palette.Muted;
            backendDbInfo.BackColor = Palette.Surface;
            backendDbInfo.Location = new Point(48, 64);
            backendDbInfo.Visible = false;
            statusPanel.Controls.Add(backendDbInfo);

            Panel backendLine = new Panel();
            backendLine.Location = new Point(22, 88);
            backendLine.Size = new Size(692, 1);
            backendLine.BackColor = Color.FromArgb(241, 238, 231);
            statusPanel.Controls.Add(backendLine);

            frontendDot = AddDot(statusPanel, 22, 124, 11);
            Label frontendTitle = AddCardTitle(statusPanel, "前端界面", 48, 110);
            frontendStatus = AddInlineStatus(statusPanel, frontendTitle, "未启动", Palette.Faint);
            AddCardSub(statusPanel,
                SinglePort ? "页面与接口由后端统一提供" : "独立前端进程 · 仅本机调试",
                48, 135);
            frontendDetail = AddCardDetail(statusPanel, "127.0.0.1", 516, 124);
            AlignHost(frontendDetail);
            frontendPortBox = AddPortBox(statusPanel, frontendPort, 636, 117);
            frontendPortWrap = frontendPortBox.Parent;

            frontendBadge = new PillLabel();
            frontendBadge.Location = new Point(620, 117);
            frontendBadge.Size = new Size(92, 30);            frontendBadge.Radius = 8;
            frontendBadge.Font = new Font("Microsoft YaHei UI", 8.5F, FontStyle.Bold, GraphicsUnit.Point);
            frontendBadge.BackColor = Palette.Surface;
            frontendBadge.TextAlign = ContentAlignment.MiddleCenter;
            frontendBadge.SetPalette(Palette.PrimarySofter, Palette.PrimaryLine,
                Palette.PrimaryDeep, Palette.Primary);
            frontendBadge.Text = backendPort + " 端口";
            frontendBadge.Visible = false;
            statusPanel.Controls.Add(frontendBadge);
            if (SinglePort)
            {
                // 先摆好单端口形态，避免首屏先闪一下 5173 再变成「同后端」。
                frontendDetail.Text = "同后端";
                frontendBadge.Visible = true;
                frontendPortWrap.Visible = false;
            }

            startBtn = MakeButton("启动并打开", true, 32, 326, 176);
            startBtn.Icon = UiIcon.Play;
            startBtn.BackColor = Palette.BgTop;
            // async void 处理器里的未捕获异常会直接崩掉整个 WinForms 进程
            // （launcher-crash.log 那次就是这么来的）。这里兜底：写 crash 日志、
            // 弹窗告知，界面保持可用——别让「点一下启动」变成「软件打不开」。
            startBtn.Click += async delegate
            {
                try { await StartAll(true); }
                catch (Exception ex)
                {
                    Program.WriteCrash(ex);
                    ShowError("启动失败：" + ex.Message + "\n（详情见 launcher-logs\\ 下的崩溃日志）");
                }
            };
            Controls.Add(startBtn);
            runPageControls.Add(startBtn);

            stopBtn = MakeButton("停止服务", false, 220, 326, 124);
            stopBtn.Icon = UiIcon.Stop;
            stopBtn.BackColor = Palette.BgTop;
            stopBtn.Enabled = false;
            stopBtn.Click += async delegate
            {
                try { await StopAll(); }
                catch (Exception ex)
                {
                    Program.WriteCrash(ex);
                    ShowError("停止服务失败：" + ex.Message);
                }
            };
            Controls.Add(stopBtn);
            runPageControls.Add(stopBtn);

            lanLabel = new Label();
            lanLabel.Text = "局域网访问";
            lanLabel.AutoSize = false;
            lanLabel.TextAlign = ContentAlignment.MiddleRight;
            lanLabel.Size = new Size(88, 20);
            lanLabel.Location = new Point(600, 338);
            lanLabel.ForeColor = Palette.Text;
            lanLabel.BackColor = Palette.BgTop;
            lanLabel.Font = new Font("Microsoft YaHei UI", 9.5F, FontStyle.Regular, GraphicsUnit.Point);
            lanLabel.Cursor = Cursors.Hand;
            lanLabel.Click += delegate { if (lanSwitch.Enabled) lanSwitch.Checked = !lanSwitch.Checked; };
            Controls.Add(lanLabel);
            runPageControls.Add(lanLabel);

            lanSwitch = new ToggleSwitch();
            lanSwitch.Location = new Point(700, 336);
            lanSwitch.Size = new Size(44, 24);
            lanSwitch.Checked = lanAccess;
            lanSwitch.CheckedChanged += LanToggled;
            Controls.Add(lanSwitch);
            runPageControls.Add(lanSwitch);

            lanHint = new Label();
            lanHint.AutoSize = false;
            lanHint.Size = new Size(560, 18);
            lanHint.Location = new Point(32, 379);
            lanHint.BackColor = Palette.BgTop;
            lanHint.ForeColor = Palette.Muted;
            lanHint.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            lanHint.TextAlign = ContentAlignment.MiddleLeft;
            Controls.Add(lanHint);
            runPageControls.Add(lanHint);

            // 「复制」紧跟在地址后面，位置由文本宽度算出来，地址长短变化也不会错位。
            // 用扁平的 Button 而不是 Label：Label 拿不到焦点，键盘用户永远复制不了
            // 内网地址（2026-10-26 评测 P2）；Button 自带 Tab 焦点、回车/空格激活
            // 与 PushButton 无障碍角色，外观用一个无边框的 flat 样式对齐原来的文字。
            lanCopy = new Button();
            lanCopy.Text = "复制";
            lanCopy.FlatStyle = FlatStyle.Flat;
            lanCopy.FlatAppearance.BorderSize = 0;
            lanCopy.FlatAppearance.MouseOverBackColor = Palette.BgTop;
            lanCopy.FlatAppearance.MouseDownBackColor = Palette.BgTop;
            lanCopy.UseVisualStyleBackColor = false;
            lanCopy.TextAlign = ContentAlignment.MiddleCenter;
            lanCopy.Size = new Size(44, 20);
            lanCopy.Padding = new Padding(0);
            lanCopy.TabStop = true;
            lanCopy.AccessibleName = "复制局域网地址";
            lanCopy.ForeColor = Palette.Primary;
            lanCopy.BackColor = Palette.BgTop;
            lanCopy.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold, GraphicsUnit.Point);
            lanCopy.Cursor = Cursors.Hand;
            lanCopy.Visible = false;
            lanCopy.Click += delegate { CopyLanUrl(); };
            lanCopy.MouseEnter += delegate { lanCopy.ForeColor = Palette.PrimaryDeep; };
            lanCopy.MouseLeave += delegate { lanCopy.ForeColor = Palette.Primary; };
            Controls.Add(lanCopy);
            runPageControls.Add(lanCopy);

            lanNote = new Label();
            lanNote.AutoSize = false;
            lanNote.TextAlign = ContentAlignment.MiddleLeft;
            lanNote.Size = new Size(280, 18);
            lanNote.ForeColor = Palette.Muted;
            lanNote.BackColor = Palette.BgTop;
            lanNote.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            lanNote.Text = "首次启动若弹防火墙提示需允许。";
            lanNote.Visible = false;
            Controls.Add(lanNote);
            runPageControls.Add(lanNote);

            UpdateLanHint();
            // 配置里已经是「局域网访问」时，开机就把防火墙放行配好。
            // 只在点开关和启动服务时做会漏掉这类用户：他们不会再去点开关，
            // 结果手机连不上却看不出原因（2026-10-26 真机就是这样）。
            if (lanAccess) EnsureFirewallRule();
            BuildDbPage();

            // ── 库结构落后提示条（2026-10-26 真机事故）──
            // 代码升级了、库没跑迁移时，应用照常启动、页面能开，只有碰新列的接口 500。
            // 现场看到的是"系统坏了"。这条提示把原因摆到界面上，并给一个一键升级入口。
            schemaWarning = new Label();
            schemaWarning.AutoSize = false;
            // 位置：状态卡到 y=312 结束、按钮从 y=326 起，中间只有 14px，放不下。
            // 所以放在按钮**下方**、日志标题**上方**，并且必须 BringToFront ——
            // 日志面板是后添加的，z-order 在上层，会把这里的文字盖掉（实测过）。
            schemaWarning.Location = new Point(32, 378);
            schemaWarning.Size = new Size(586, 22);
            schemaWarning.TextAlign = ContentAlignment.MiddleLeft;
            schemaWarning.Font = new Font("Microsoft YaHei UI", 8.5F,
                FontStyle.Regular, GraphicsUnit.Point);
            schemaWarning.ForeColor = Palette.Danger;
            schemaWarning.BackColor = Palette.BgTop;
            schemaWarning.Text = "";
            schemaWarning.Visible = false;
            Controls.Add(schemaWarning);
            runPageControls.Add(schemaWarning);

            schemaUpgradeBtn = MakeButton("升级数据库", false, 626, 374, 142);
            schemaUpgradeBtn.Height = 30;
            schemaUpgradeBtn.Font = new Font("Microsoft YaHei UI", 8.5F,
                FontStyle.Bold, GraphicsUnit.Point);
            schemaUpgradeBtn.Visible = false;
            schemaUpgradeBtn.Click += async delegate
            {
                try { await UpgradeSchema(); }
                catch (Exception ex)
                {
                    Program.WriteCrash(ex);
                    ShowError("升级失败：" + ex.Message + "\n（详情见 launcher-logs\\ 下的日志）");
                }
            };
            Controls.Add(schemaUpgradeBtn);
            runPageControls.Add(schemaUpgradeBtn);
            schemaWarning.BringToFront();
            schemaUpgradeBtn.BringToFront();

            Label logTitle = new Label();
            logTitle.Text = "运行日志";
            logTitle.ForeColor = Palette.Ink;
            logTitle.Font = new Font("Microsoft YaHei UI", 9.5F, FontStyle.Bold, GraphicsUnit.Point);
            logTitle.AutoSize = true;
            logTitle.BackColor = Palette.BgTop;
            logTitle.Location = new Point(32, 409);
            Controls.Add(logTitle);
            livePill = new PillLabel();
            livePill.Location = new Point(88, 406);
            livePill.Size = new Size(50, 18);
            livePill.Radius = 999;
            livePill.ShowDot = true;
            livePill.Font = new Font("Microsoft YaHei UI", 8F, FontStyle.Regular, GraphicsUnit.Point);
            livePill.BackColor = Palette.BgTop;
            livePill.TextAlign = ContentAlignment.MiddleCenter;
            livePill.SetPalette(Palette.SuccessSoft, Palette.SuccessSoft,
                Palette.Success, Palette.Success);
            livePill.Text = "实时";
            Controls.Add(livePill);

            logOpen = new IconLink();
            logOpen.Icon = UiIcon.Folder;
            logOpen.Text = "打开目录";
            logOpen.Location = new Point(600, 407);
            logOpen.Size = new Size(90, 18);
            logOpen.BackColor = Palette.BgTop;
            logOpen.TextColor = Palette.Primary;
            logOpen.HoverColor = Palette.PrimaryDeep;
            logOpen.Click += delegate { OpenLogs(); };
            Controls.Add(logOpen);

            // 用户往上翻看历史时冒出这行字，点一下回到末尾；否则日志每 2.5 秒一跳，根本看不住
            logJump = new Label();
            logJump.Text = "回到最新 ↓";
            logJump.AutoSize = false;
            logJump.TextAlign = ContentAlignment.MiddleRight;
            logJump.Size = new Size(72, 16);
            logJump.Location = new Point(696, 409);
            logJump.ForeColor = Palette.Muted;
            logJump.BackColor = Palette.BgTop;
            logJump.Font = new Font("Microsoft YaHei UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point);
            logJump.Cursor = Cursors.Hand;
            logJump.Visible = false;
            logJump.Click += delegate
            {
                logBox.SelectionStart = logBox.TextLength;
                logBox.ScrollToCaret();
                logJump.Visible = false;
            };
            Controls.Add(logJump);

            RoundedPanel logPanel = new RoundedPanel();
            logPanel.Location = new Point(32, 437);
            logPanel.Size = new Size(736, 190);
            logPanel.Radius = 12;
            logPanel.FillColor = Palette.Surface;
            logPanel.BorderColor = Palette.Line;
            logPanel.BackColor = Palette.BgTop;
            Controls.Add(logPanel);
            commonCardShadows.Add(logPanel.Bounds);

            logBox = new TextBox();
            logBox.Multiline = true;
            logBox.ReadOnly = true;
            logBox.ScrollBars = ScrollBars.None;
            logBox.WordWrap = false;
            logBox.BackColor = Palette.Surface;
            logBox.ForeColor = Palette.LogInk;
            logBox.BorderStyle = BorderStyle.None;
            logBox.Font = new Font("Consolas", 9F, FontStyle.Regular, GraphicsUnit.Point);
            logBox.Location = new Point(16, 14);
            logBox.Size = new Size(704, 162);
            logPanel.Controls.Add(logBox);

            footer = new Label();
            footer.Text = "项目目录：" + root;
            footer.ForeColor = Palette.Muted;
            footer.AutoSize = false;
            footer.AutoEllipsis = true;
            footer.BackColor = Palette.BgTop;
            footer.Location = new Point(32, 635);
            footer.Size = new Size(600, 16);
            Controls.Add(footer);

            logAuto = new Label();
            logAuto.Text = "自动探测 2.5s";
            logAuto.ForeColor = Palette.Muted;
            logAuto.AutoSize = false;
            logAuto.TextAlign = ContentAlignment.MiddleRight;
            logAuto.BackColor = Palette.BgTop;
            logAuto.Location = new Point(632, 635);
            logAuto.Size = new Size(136, 16);
            logAuto.Font = new Font("Microsoft YaHei UI", 8F, FontStyle.Regular, GraphicsUnit.Point);
            Controls.Add(logAuto);

            ResumeLayout(false);
        }

        private void BuildDbPage()
        {
            dbCard = new RoundedPanel();
            dbCard.Location = new Point(32, 136);
            dbCard.Size = new Size(736, 258);
            dbCard.Radius = 14;
            dbCard.FillColor = Palette.Surface;
            dbCard.BorderColor = Palette.Line;
            dbCard.BackColor = Palette.BgTop;
            dbCard.Visible = false;
            Controls.Add(dbCard);
            dbCardShadows.Add(dbCard.Bounds);

            IconChip titleIcon = new IconChip();
            titleIcon.Location = new Point(24, 18);
            titleIcon.Size = new Size(28, 28);
            titleIcon.Radius = 8;
            titleIcon.Icon = UiIcon.Database;
            titleIcon.BackColor = Palette.Surface;
            dbCard.Controls.Add(titleIcon);

            Label title = new Label();
            title.Text = "连接参数";
            title.Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Bold, GraphicsUnit.Point);
            title.ForeColor = Palette.Ink;
            title.BackColor = Palette.Surface;
            title.AutoSize = true;
            title.Location = new Point(60, 21);
            dbCard.Controls.Add(title);

            dbModeCheck = new CheckBox();
            dbModeCheck.Text = "使用内置数据库";
            dbModeCheck.AutoSize = true;
            dbModeCheck.Location = new Point(190, 19);
            dbModeCheck.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            dbModeCheck.ForeColor = Palette.Ink;
            dbModeCheck.BackColor = Palette.Surface;
            dbModeCheck.Checked = (dbMode == "portable");
            dbModeCheck.CheckedChanged += delegate
            {
                if (dbFormLoading || dbBusy) return;
                dbMode = dbModeCheck.Checked ? "portable" : "external";
                SaveConfig();
                UpdateDbModeUi();
                Log("数据库模式已切换为：" + (dbMode == "portable" ? "内置数据库" : "外部 PostgreSQL") + "（重启服务后生效）");
            };
            dbCard.Controls.Add(dbModeCheck);

            PillLabel badge = new PillLabel();
            badge.Text = "PostgreSQL";
            badge.Font = new Font("Microsoft YaHei UI", 8F, FontStyle.Regular, GraphicsUnit.Point);
            badge.Location = new Point(626, 18);
            badge.Size = new Size(86, 24);
            badge.Radius = 999;
            badge.ShowDot = true;
            badge.TextAlign = ContentAlignment.MiddleCenter;
            badge.BackColor = Palette.Surface;
            badge.SetPalette(Palette.SurfaceWarm, Palette.Line, Palette.Muted, Palette.Primary);
            dbCard.Controls.Add(badge);

            dbHostBox = AddDbInput(dbCard, "主机", dbHost, 24, 52, 240, false);
            dbPortBox = AddDbInput(dbCard, "端口", dbPort.ToString(), 280, 52, 90, false);
            dbPortBox.MaxLength = 5;
            dbPortBox.KeyPress += delegate(object s, KeyPressEventArgs e)
            {
                if (!Char.IsControl(e.KeyChar) && !Char.IsDigit(e.KeyChar))
                    e.Handled = true;
            };
            dbUserBox = AddDbInput(dbCard, "用户名", dbUser, 386, 52, 326, false);
            dbNameBox = AddDbInput(dbCard, "数据库", dbName, 24, 110, 240, false);

            dbPickBtn = MakeDbButton("选择", false, 272, 130, 62, 30);
            dbPickBtn.Font = new Font("Microsoft YaHei UI", 8.5F, FontStyle.Bold, GraphicsUnit.Point);
            dbPickBtn.Icon = UiIcon.ChevronDown;
            dbPickBtn.IconSize = 12;
            dbPickBtn.Click += async delegate
            {
                try { await PickDatabase(); }
                catch (Exception ex)
                {
                    Program.WriteCrash(ex);
                    ShowError("读取数据库列表失败：" + ex.Message);
                }
            };
            dbCard.Controls.Add(dbPickBtn);

            dbPasswordBox = AddDbInput(dbCard, "口令", dbPassword, 350, 110, 362, true);

            Label pwdHint = new Label();
            pwdHint.Text = "留空沿用系统设置";
            pwdHint.AutoSize = false;
            pwdHint.TextAlign = ContentAlignment.MiddleRight;
            pwdHint.Size = new Size(116, 18);
            pwdHint.Location = new Point(596, 109);
            pwdHint.ForeColor = Palette.Muted;
            pwdHint.BackColor = Palette.Surface;
            pwdHint.Font = new Font("Microsoft YaHei UI", 8F, FontStyle.Regular, GraphicsUnit.Point);
            dbCard.Controls.Add(pwdHint);

            dbStatusStrip = new PillLabel();
            dbStatusStrip.Location = new Point(24, 170);
            dbStatusStrip.Size = new Size(688, 28);
            dbStatusStrip.Radius = 8;
            dbStatusStrip.ShowDot = true;
            dbStatusStrip.SidePadding = 10;
            dbStatusStrip.TextAlign = ContentAlignment.MiddleLeft;
            dbStatusStrip.Font = new Font("Microsoft YaHei UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point);
            dbStatusStrip.BackColor = Palette.Surface;
            dbStatusStrip.SetPalette(Color.FromArgb(247, 245, 239),
                Color.FromArgb(236, 232, 223), Palette.Muted, Palette.Accent);
            dbCard.Controls.Add(dbStatusStrip);

            dbTestBtn = MakeDbButton("测试连接", false, 24, 210, 104, 38);
            dbTestBtn.Icon = UiIcon.Pulse;
            dbTestBtn.IconSize = 13;
            dbTestBtn.Click += async delegate
            {
                try { await TestDatabase(); }
                catch (Exception ex)
                {
                    Program.WriteCrash(ex);
                    ShowError("测试连接失败：" + ex.Message);
                }
            };
            dbCard.Controls.Add(dbTestBtn);

            dbSaveBtn = MakeDbButton("保存并应用", true, 138, 210, 116, 38);
            dbSaveBtn.Icon = UiIcon.Save;
            dbSaveBtn.IconSize = 13;
            dbSaveBtn.Click += async delegate
            {
                try { await SaveDatabaseSettings(); }
                catch (Exception ex)
                {
                    Program.WriteCrash(ex);
                    ShowError("保存数据库设置失败：" + ex.Message);
                }
            };
            dbCard.Controls.Add(dbSaveBtn);

            dbNewBtn = MakeDbButton("新建并初始化", false, 264, 210, 144, 38);
            dbNewBtn.Icon = UiIcon.Plus;
            dbNewBtn.IconSize = 13;
            dbNewBtn.Click += async delegate
            {
                try { await CreateDatabase(); }
                catch (Exception ex)
                {
                    Program.WriteCrash(ex);
                    ShowError("新建数据库失败：" + ex.Message);
                }
            };
            dbCard.Controls.Add(dbNewBtn);

            dbInitBtn = MakeDbButton("初始化当前库", false, 418, 210, 132, 38);
            dbInitBtn.Icon = UiIcon.Refresh;
            dbInitBtn.IconSize = 13;
            dbInitBtn.Click += async delegate
            {
                try { await InitializeDatabase(); }
                catch (Exception ex)
                {
                    Program.WriteCrash(ex);
                    ShowError("初始化数据库失败：" + ex.Message);
                }
            };
            dbCard.Controls.Add(dbInitBtn);

            EventHandler changed = delegate { DbFormChanged(); };
            dbHostBox.TextChanged += changed;
            dbPortBox.TextChanged += changed;
            dbNameBox.TextChanged += changed;
            dbUserBox.TextChanged += changed;
            dbPasswordBox.TextChanged += changed;

            if (dbPasswordError)
                SetDbStatus("已保存的口令无法解密，请重新输入口令后保存。", Palette.Danger);
            else
                UpdateDbStatusLine();
            UpdateDbModeUi();
        }

        private void UpdateDbModeUi()
        {
            bool portable = (dbMode == "portable");
            dbFormLoading = true;
            try
            {
                if (dbModeCheck != null) dbModeCheck.Checked = portable;
                if (dbHostBox != null) dbHostBox.Enabled = !portable;
                if (dbPortBox != null) dbPortBox.Enabled = !portable;
                if (dbUserBox != null) dbUserBox.Enabled = !portable;
                if (dbNameBox != null) dbNameBox.Enabled = !portable;
                if (dbPasswordBox != null) dbPasswordBox.Enabled = !portable;
                if (dbPickBtn != null) dbPickBtn.Visible = !portable;
                if (dbTestBtn != null) dbTestBtn.Text = portable ? "检测内置数据库" : "测试连接";
                if (dbNewBtn != null) dbNewBtn.Text = portable ? "初始化内置数据库" : "新建并初始化";
                if (dbInitBtn != null) dbInitBtn.Text = portable ? "打开数据目录" : "初始化当前库";
                if (portable)
                {
                    dbHost = "127.0.0.1";
                    dbPort = pgPort;
                    dbName = "jianji";
                    dbUser = "postgres";
                    if (dbHostBox != null) dbHostBox.Text = dbHost;
                    if (dbPortBox != null) dbPortBox.Text = dbPort.ToString();
                    if (dbNameBox != null) dbNameBox.Text = dbName;
                    if (dbUserBox != null) dbUserBox.Text = dbUser;
                    if (dbStatusStrip != null)
                        SetDbStatus("内置数据库：" + RelativeToRoot(pgDataDir)
                            + "，端口 " + pgPort + "。", Palette.Accent);
                }
                else if (dbStatusStrip != null)
                {
                    SetDbStatus("外部 PostgreSQL：" + DbTargetLabel() + "。",
                        Palette.Muted);
                }
            }
            finally { dbFormLoading = false; }
        }

        private TextBox AddDbInput(Panel parent, string label, string value,
            int x, int y, int width, bool password)
        {
            Label text = new Label();
            text.Text = label;
            text.AutoSize = true;
            text.BackColor = Palette.Surface;
            text.ForeColor = Palette.Muted;
            text.Font = new Font("Microsoft YaHei UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point);
            text.Location = new Point(x, y);
            parent.Controls.Add(text);

            RoundedPanel wrap = new RoundedPanel();
            wrap.Location = new Point(x, y + 20);
            wrap.Size = new Size(width, 30);
            wrap.Radius = 8;
            wrap.FillColor = Palette.SurfaceWarm;
            wrap.BorderColor = Palette.LineStrong;
            wrap.BackColor = Palette.Surface;
            parent.Controls.Add(wrap);

            TextBox box = new TextBox();
            box.Text = value;
            box.Location = new Point(10, 4);
            box.Size = new Size(width - 20, 22);
            box.BorderStyle = BorderStyle.None;
            box.BackColor = Palette.SurfaceWarm;
            box.ForeColor = Palette.Text;
            box.Font = new Font("Microsoft YaHei UI", 9.5F, FontStyle.Regular, GraphicsUnit.Point);
            if (password) box.UseSystemPasswordChar = true;
            box.Enter += delegate
            {
                wrap.BorderColor = Palette.Primary;
                wrap.FillColor = Color.White;
                box.BackColor = Color.White;
                wrap.Invalidate();
            };
            box.Leave += delegate
            {
                wrap.BorderColor = Palette.LineStrong;
                wrap.FillColor = Palette.SurfaceWarm;
                box.BackColor = Palette.SurfaceWarm;
                wrap.Invalidate();
            };
            wrap.Controls.Add(box);
            return box;
        }

        private static RoundedButton MakeDbButton(string text, bool primary,
            int x, int y, int w, int h)
        {
            RoundedButton b = new RoundedButton();
            b.Primary = primary;
            b.Text = text;
            b.Location = new Point(x, y);
            b.Size = new Size(w, h);
            b.Radius = 8;
            b.BackColor = Palette.Surface;
            b.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold, GraphicsUnit.Point);
            return b;
        }

        private void SwitchPage(int index)
        {
            bool run = index == 0;
            runPageActive = run;
            foreach (Control control in runPageControls)
                control.Visible = run;
            if (dbCard != null) dbCard.Visible = !run;
            if (pageIconChip != null)
            {
                pageIconChip.Icon = run ? UiIcon.Server : UiIcon.Database;
                pageIconChip.Invalidate();
            }
            heading.Text = run ? "运行控制台" : "数据库连接";
            subheading.Text = run ? "服务状态与启动" : "后端使用的 PostgreSQL 库";
            if (pageTabs.SelectedIndex != index) pageTabs.SelectedIndex = index;
            UpdateLanHint();
            if (!run) ApplyDbStatus();
            Invalidate();
        }

        private void UpdateDbStatusLine()
        {
            if (!String.IsNullOrEmpty(dbStatusText))
            {
                ApplyDbStatus();
                return;
            }
            string prefix = dbConfigured ? "已保存：" : "启动时沿用系统配置：";
            SetDbStatus(prefix + DbTargetLabel(), Palette.Faint);
        }

        private void SetDbStatus(string text, Color color)
        {
            dbStatusText = text ?? "";
            dbStatusColor = color;
            ApplyDbStatus();
        }

        private void ApplyDbStatus()
        {
            if (dbStatusStrip == null || dbStatusStrip.IsDisposed) return;
            dbStatusStrip.Text = dbStatusText;
            if (dbStatusColor == Palette.Success)
            {
                dbStatusStrip.SetPalette(Palette.SuccessSoft, Color.FromArgb(215, 233, 223),
                    Color.FromArgb(31, 122, 84), Palette.Success);
            }
            else if (dbStatusColor == Palette.Danger)
            {
                dbStatusStrip.SetPalette(Palette.DangerSoft, Color.FromArgb(235, 207, 200),
                    Palette.Danger, Palette.Danger);
            }
            else if (dbStatusColor == Palette.Accent || dbStatusColor == Palette.AccentDeep)
            {
                dbStatusStrip.SetPalette(Palette.AccentSoft, Color.FromArgb(236, 220, 191),
                    Palette.AccentDeep, Palette.Accent);
            }
            else
            {
                dbStatusStrip.SetPalette(Color.FromArgb(247, 245, 239),
                    Color.FromArgb(236, 232, 223), Palette.Muted, Palette.Accent);
            }
            dbStatusStrip.Invalidate();
        }

        private void DbFormChanged()
        {
            if (dbFormLoading || dbBusy) return;
            SetDbStatus("配置有修改，尚未测试。", Palette.AccentDeep);
        }

        private bool ReadDbForm(out string host, out int port, out string name,
            out string user, out string password)
        {
            host = dbHostBox.Text.Trim();
            name = dbNameBox.Text.Trim();
            user = dbUserBox.Text.Trim();
            password = dbPasswordBox.Text;
            port = dbPort;

            if (host.Length == 0)
            {
                ShowError("数据库主机不能为空。");
                return false;
            }
            if (host.IndexOfAny(new char[] { ' ', '/', '@', '?', '#' }) >= 0)
            {
                ShowError("数据库主机格式不正确。");
                return false;
            }
            if (!Int32.TryParse(dbPortBox.Text.Trim(), out port) ||
                port < 1 || port > 65535)
            {
                ShowError("数据库端口必须是 1–65535 的数字。");
                return false;
            }
            if (name.Length == 0)
            {
                ShowError("数据库名不能为空。");
                return false;
            }
            if (name.IndexOfAny(new char[] { '/', '?', '#', ' ', '\\' }) >= 0 ||
                Encoding.UTF8.GetByteCount(name) > 63)
            {
                ShowError("数据库名不能包含空格或 / ? # \\，且最长 63 字节。");
                return false;
            }
            if (user.Length == 0)
            {
                ShowError("数据库用户名不能为空。");
                return false;
            }
            if (user.IndexOfAny(new char[] { '@', ':', '/', ' ', '\\' }) >= 0)
            {
                ShowError("数据库用户名格式不正确。");
                return false;
            }
            return true;
        }

        private static bool IsSafeNewDbName(string name)
        {
            return Regex.IsMatch(name, @"^[A-Za-z_][A-Za-z0-9_\-]{0,62}$") &&
                !name.Equals("postgres", StringComparison.OrdinalIgnoreCase) &&
                !name.StartsWith("template", StringComparison.OrdinalIgnoreCase);
        }

        // ————————————————— 数据库页操作 —————————————————

        private async Task TestDatabase()
        {
            if (dbBusy) return;
            if (dbMode == "portable")
            {
                SetDbBusy(true, "正在检测内置数据库…");
                bool running;
                try { running = await Task.Run(() => PortableDbRunning()); }
                finally { SetDbBusy(false, null); }
                SetDbStatus(running
                        ? "内置数据库运行中。"
                        : "内置数据库未运行，启动服务时将自动启动。",
                    running ? Palette.Success : Palette.Muted);
                return;
            }
            string host, name, user, password;
            int port;
            if (!ReadDbForm(out host, out port, out name, out user, out password)) return;

            SetDbBusy(true, "正在测试连接…");
            try
            {
                DbProbe probe = await Task.Run(
                    () => RunDbProbe(host, port, name, user, password));
                if (!probe.Ok)
                {
                    SetDbStatus("连接失败，详情见运行日志。",
                        Palette.Danger);
                    return;
                }
                if (probe.DbExists)
                    SetDbStatus("连接成功 · " + probe.Version + " · 库已存在",
                        Palette.Success);
                else
                    SetDbStatus("服务器可连接 · " + probe.Version +
                        " · 库不存在，可点「新建并初始化」。",
                        Palette.AccentDeep);
            }
            finally
            {
                SetDbBusy(false, null);
            }
        }

        private async Task PickDatabase()
        {
            if (dbBusy) return;
            string host, name, user, password;
            int port;
            if (!ReadDbForm(out host, out port, out name, out user, out password)) return;

            SetDbBusy(true, "正在读取数据库列表…");
            DbListResult list;
            try
            {
                list = await Task.Run(() => RunDbList(host, port, user, password));
            }
            finally
            {
                SetDbBusy(false, null);
            }
            if (!list.Ok)
            {
                SetDbStatus("读取数据库列表失败，详情见运行日志。",
                    Palette.Danger);
                return;
            }
            SetDbStatus("已读取 " + list.Databases.Count + " 个数据库，请选择。",
                Palette.Faint);
            using (DbPickerForm picker = new DbPickerForm(list.Databases, name))
            {
                if (picker.ShowDialog(this) == DialogResult.OK &&
                    !String.IsNullOrEmpty(picker.SelectedDatabase))
                {
                    dbFormLoading = true;
                    dbNameBox.Text = picker.SelectedDatabase;
                    dbFormLoading = false;
                    SetDbStatus("已选择 " + picker.SelectedDatabase + "，测试后保存。",
                        Palette.Ink);
                }
            }
        }

        private async Task SaveDatabaseSettings()
        {
            if (dbBusy) return;
            if (dbMode == "portable")
            {
                SaveConfig();
                SetDbStatus("内置数据库设置已保存：" + RelativeToRoot(pgDataDir)
                    + "（端口 " + pgPort + "）。", Palette.Success);
                Log("内置数据库设置已保存。");
                await PromptRestartIfRunning("内置数据库设置已保存");
                return;
            }
            string host, name, user, password;
            int port;
            if (!ReadDbForm(out host, out port, out name, out user, out password)) return;

            dbHost = host;
            dbPort = port;
            dbName = name;
            dbUser = user;
            dbPassword = password;
            dbConfigured = true;
            dbPasswordError = false;
            SaveConfig();
            SetDbStatus("已保存：" + DbTargetLabel(), Palette.Success);
            Log("数据库设置已保存：" + DbTargetLabel());
            await PromptRestartIfRunning("数据库设置已保存");
        }

        private async Task CreateDatabase()
        {
            if (dbBusy) return;
            if (dbMode == "portable")
            {
                bool portableRunning = await BackendRunning();
                string portableWarning = portableRunning
                    ? "\n\n后端正在运行；初始化会修改表结构，建议先停止服务。" : "";
                DialogResult portableAnswer = MessageBox.Show(this,
                    "将启动程序内置数据库，并建表、导入 22 个中图法一级大类。\n"
                    + "已有数据只做升级/补表，不会清空；现有非空库会先自动备份。"
                    + portableWarning + "\n\n继续？",
                    "初始化内置数据库", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (portableAnswer != DialogResult.Yes) return;
                SetDbBusy(true, "正在初始化内置数据库…");
                bool ok;
                try { ok = await Task.Run(() => EnsurePortableDatabase()); }
                finally { SetDbBusy(false, null); }
                if (!ok)
                {
                    SetDbStatus("内置数据库初始化失败，详情见运行日志。",
                        Palette.Danger);
                    return;
                }
                SetDbStatus("内置数据库已就绪：" + RelativeToRoot(pgDataDir),
                    Palette.Success);
                await PromptRestartIfRunning("内置数据库已初始化");
                return;
            }
            string host, name, user, password;
            int port;
            if (!ReadDbForm(out host, out port, out name, out user, out password)) return;
            if (!IsSafeNewDbName(name))
            {
                ShowError("新库名只能包含字母、数字、下划线和短横线，且不能以数字开头；\n" +
                    "postgres 与 template 开头的系统库不可用。");
                return;
            }

            bool running = await BackendRunning();
            string warning = running
                ? "\n\n后端正在运行；初始化会修改表结构，建议先停止服务。" : "";
            DialogResult answer = MessageBox.Show(this,
                "将创建（或复用）数据库 " + name + "，建表并导入中图法类表。\n" +
                "已存在的库只做升级/补表，不会清空已有数据；现有非空库会先自动备份到 backups/。" +
                warning + "\n\n继续？",
                "新建并初始化数据库", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (answer != DialogResult.Yes) return;

            SetDbBusy(true, "正在新建并初始化 " + name + "…");
            Log("开始新建并初始化数据库：" + name);
            RunResult result;
            try
            {
                result = await Task.Run(
                    () => RunInitDb(host, port, name, user, password));
            }
            finally
            {
                SetDbBusy(false, null);
            }
            if (!result.Ok)
            {
                SetDbStatus("新建/初始化失败，详情见运行日志。",
                    Palette.Danger);
                return;
            }

            dbHost = host;
            dbPort = port;
            dbName = name;
            dbUser = user;
            dbPassword = password;
            dbConfigured = true;
            dbPasswordError = false;
            SaveConfig();
            SetDbStatus("数据库已就绪：" + DbTargetLabel(), Palette.Success);
            Log("数据库初始化完成：" + DbTargetLabel());
            await PromptRestartIfRunning("数据库已初始化");
        }

        private async Task InitializeDatabase()
        {
            if (dbBusy) return;
            if (dbMode == "portable")
            {
                try
                {
                    Directory.CreateDirectory(pgDataDir);
                    Process.Start("explorer.exe", pgDataDir);
                }
                catch (Exception ex)
                {
                    ShowError("打开数据目录失败：" + ex.Message);
                }
                return;
            }
            string host, name, user, password;
            int port;
            if (!ReadDbForm(out host, out port, out name, out user, out password)) return;

            bool running = await BackendRunning();
            string warning = running
                ? "\n\n后端正在运行；初始化会修改表结构，建议先停止服务。" : "";
            DialogResult answer = MessageBox.Show(this,
                "将检查/升级数据库 " + name + " 的表结构，并补齐中图法类表。\n" +
                "不会清空已有数据；现有非空库会先自动备份到 backups/。" +
                warning + "\n\n继续？",
                "初始化当前库", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (answer != DialogResult.Yes) return;

            SetDbBusy(true, "正在初始化 " + name + "…");
            Log("开始初始化数据库：" + name);
            RunResult result;
            try
            {
                result = await Task.Run(
                    () => RunInitDb(host, port, name, user, password));
            }
            finally
            {
                SetDbBusy(false, null);
            }
            if (!result.Ok)
            {
                SetDbStatus("初始化失败，详情见运行日志。",
                    Palette.Danger);
                return;
            }

            dbHost = host;
            dbPort = port;
            dbName = name;
            dbUser = user;
            dbPassword = password;
            dbConfigured = true;
            dbPasswordError = false;
            SaveConfig();
            SetDbStatus("数据库已就绪：" + DbTargetLabel(), Palette.Success);
            Log("数据库初始化完成：" + DbTargetLabel());
            await PromptRestartIfRunning("数据库已初始化");
        }

        private async Task<bool> BackendRunning()
        {
            bool dbOk = false;
            bool running = false;
            await Task.Run(delegate { running = CheckBackend(out dbOk); });
            return running;
        }

        private async Task PromptRestartIfRunning(string reason)
        {
            bool running = await BackendRunning();
            if (!running)
            {
                SetDbStatus("已保存：" + DbTargetLabel() + "（下次启动生效）",
                    Palette.Success);
                Log("后端未运行，数据库设置将在下次启动时生效。");
                return;
            }
            DialogResult answer = MessageBox.Show(this,
                reason + "。\n\n是否立即重启后端以连接 " + dbName + "？\n" +
                "重启会短暂中断正在使用的页面。",
                "数据库设置", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (answer != DialogResult.Yes)
            {
                SetDbStatus("已保存：" + DbTargetLabel() + "（重启后端后生效）",
                    Palette.AccentDeep);
                Log("数据库设置已保存；后端仍在用旧连接，重启后生效。");
                return;
            }
            SetDbBusy(true, "正在重启后端…");
            try
            {
                await RestartBackendAsync();
            }
            finally
            {
                SetDbBusy(false, null);
            }
        }

        private async Task RestartBackendAsync()
        {
            if (starting || stopping) return;
            starting = true;
            everStarted = true;
            Ui(delegate
            {
                startBtn.Enabled = false;
                stopBtn.Enabled = false;
                SetStatus(backendStatus, backendDot, "重启中…", Palette.Accent);
                SetStatus(frontendStatus, frontendDot, "重启中…", Palette.Accent);
            });
            Log("正在重启后端以应用数据库设置…");
            try
            {
                await Task.Run(delegate { StopBackendOnly(); });
                bool ok = await Task.Run(() => StartBackend());
                starting = false;
                bool dbOk = false;
                bool backend = CheckBackend(out dbOk);
                ApplyHealth(backend, dbOk, CheckFrontend());
                if (backend && dbOk)
                    SetDbStatus("后端已连接：" + DbTargetLabel(), Palette.Success);
                else if (backend)
                    SetDbStatus("后端已启动，但数据库未连接；请查看日志或重新测试连接。",
                        Palette.Accent);
                else
                    SetDbStatus("后端重启失败，详情见运行日志。",
                        Palette.Danger);
                if (!ok) ShowError("后端重启失败，请查看运行日志。");
            }
            catch (Exception ex)
            {
                starting = false;
                Program.WriteCrash(ex);
                ShowError("重启后端失败：" + ex.Message);
            }
            finally
            {
                starting = false;
            }
        }

        private void StopBackendOnly()
        {
            bool stopped = KillTracked(ref backendProc, "后端");
            if (!stopped && FindListeningPid(backendPort) > 0)
                KillByPort(backendPort, "后端");
        }

        private void SetDbBusy(bool busy, string status)
        {
            dbBusy = busy;
            bool enabled = !busy;
            bool external = enabled && dbMode == "external";
            if (dbModeCheck != null) dbModeCheck.Enabled = enabled;
            if (dbHostBox != null) dbHostBox.Enabled = external;
            if (dbPortBox != null) dbPortBox.Enabled = external;
            if (dbNameBox != null) dbNameBox.Enabled = external;
            if (dbUserBox != null) dbUserBox.Enabled = external;
            if (dbPasswordBox != null) dbPasswordBox.Enabled = external;
            if (dbTestBtn != null) dbTestBtn.Enabled = enabled;
            if (dbSaveBtn != null) dbSaveBtn.Enabled = enabled;
            if (dbNewBtn != null) dbNewBtn.Enabled = enabled;
            if (dbInitBtn != null) dbInitBtn.Enabled = enabled;
            if (dbPickBtn != null) dbPickBtn.Enabled = external;
            if (!String.IsNullOrEmpty(status))
                SetDbStatus(status, Palette.Accent);
        }

        private ProcessStartInfo BuildDbHelperPsi(PythonCommand python, string action,
            string host, int port, string name, string user, string password)
        {
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = python.FileName;
            psi.Arguments = python.Prefix + "scripts/db_manage.py " + action;
            psi.WorkingDirectory = Path.Combine(root, "backend");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            SetChildEnv(psi, "PYTHONIOENCODING", "utf-8");
            SetChildEnv(psi, "PYTHONPATH", Path.Combine(root, "backend"));
            SetChildEnv(psi, "PATH",
                Path.Combine(root, "runtime", "pgsql", "bin") + ";"
                + Environment.GetEnvironmentVariable("PATH"));
            SetChildEnv(psi, "JIANJI_DB_HOST", host);
            SetChildEnv(psi, "JIANJI_DB_PORT", port.ToString());
            SetChildEnv(psi, "JIANJI_DB_NAME", name);
            SetChildEnv(psi, "JIANJI_DB_USER", user);
            if (!String.IsNullOrEmpty(password))
                SetChildEnv(psi, "JIANJI_DB_PASSWORD", password);
            return psi;
        }

        private DbProbe RunDbProbe(string host, int port, string name,
            string user, string password)
        {
            DbProbe probe = new DbProbe();
            PythonCommand python = FindPython();
            if (python == null)
            {
                probe.Message = "找不到可用的 Python（需要后端依赖）。";
                Log("数据库测试失败：" + probe.Message);
                return probe;
            }
            ProcessStartInfo psi = BuildDbHelperPsi(python, "test", host, port, name,
                user, password);
            PythonRunResult run = RunPythonProcess(psi, 25000, false);
            string message = RunMessage(run);
            if (!String.IsNullOrEmpty(message))
            {
                foreach (string line in message.Split('\n'))
                {
                    if (line.Trim().Length > 0) Log("[db] " + line.Trim());
                }
            }
            string json = LastJsonLine(run.StdOut.ToString());
            if (json.Length == 0)
            {
                probe.Message = message.Length > 0 ? message : "测试脚本没有返回结果。";
                return probe;
            }
            try
            {
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                Dictionary<string, object> map =
                    serializer.Deserialize<Dictionary<string, object>>(json);
                probe.Ok = JsonBool(map, "ok");
                probe.DbExists = JsonBool(map, "db_exists");
                probe.Version = JsonText(map, "version");
                probe.Message = JsonText(map, "message");
            }
            catch (Exception ex)
            {
                probe.Message = "无法解析测试结果：" + ex.Message + " " + json;
            }
            if (probe.Message.Length > 0) Log("数据库测试：" + probe.Message);
            return probe;
        }

        private DbListResult RunDbList(string host, int port, string user, string password)
        {
            DbListResult result = new DbListResult();
            PythonCommand python = FindPython();
            if (python == null)
            {
                result.Message = "找不到可用的 Python（需要后端依赖）。";
                Log("读取数据库列表失败：" + result.Message);
                return result;
            }
            ProcessStartInfo psi = BuildDbHelperPsi(python, "list", host, port,
                "postgres", user, password);
            PythonRunResult run = RunPythonProcess(psi, 25000, false);
            string message = RunMessage(run);
            if (!String.IsNullOrEmpty(message))
            {
                foreach (string line in message.Split('\n'))
                {
                    if (line.Trim().Length > 0) Log("[db] " + line.Trim());
                }
            }
            string json = LastJsonLine(run.StdOut.ToString());
            if (json.Length == 0)
            {
                result.Message = message.Length > 0 ? message : "列表脚本没有返回结果。";
                return result;
            }
            try
            {
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                Dictionary<string, object> map =
                    serializer.Deserialize<Dictionary<string, object>>(json);
                result.Ok = JsonBool(map, "ok");
                result.Message = JsonText(map, "message");
                object raw;
                if (map.TryGetValue("databases", out raw))
                {
                    System.Collections.IEnumerable items = raw as System.Collections.IEnumerable;
                    if (items != null)
                    {
                        foreach (object item in items)
                        {
                            string db = Convert.ToString(item);
                            if (!String.IsNullOrEmpty(db)) result.Databases.Add(db);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                result.Message = "无法解析数据库列表：" + ex.Message;
            }
            if (result.Message.Length > 0) Log("数据库列表：" + result.Message);
            return result;
        }

        private RunResult RunInitDb(string host, int port, string name,
            string user, string password)
        {
            PythonCommand python = PythonForMode();
            if (python == null)
            {
                string error = "找不到可用的 Python（需要后端依赖）。";
                Log(error);
                return new RunResult(false, error);
            }
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = python.FileName;
            psi.Arguments = python.Prefix + "scripts/init_db.py";
            psi.WorkingDirectory = Path.Combine(root, "backend");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            SetChildEnv(psi, "PYTHONIOENCODING", "utf-8");
            SetChildEnv(psi, "PYTHONPATH", Path.Combine(root, "backend"));
            SetChildEnv(psi, "PATH",
                Path.Combine(root, "runtime", "pgsql", "bin") + ";"
                + Environment.GetEnvironmentVariable("PATH"));
            string effectivePassword = String.IsNullOrEmpty(password)
                ? EffectiveDbPassword() : password;
            SetChildEnv(psi, "JIANJI_DATABASE_URL",
                BuildDatabaseUrl(host, port, name, user, effectivePassword));
            PythonRunResult run = RunPythonProcess(psi, 300000, true);
            bool ok = run.Started && !run.TimedOut && run.ExitCode == 0;
            string message = ok ? "初始化完成。"
                : run.TimedOut ? "初始化超时。"
                : run.Started ? "初始化失败（退出码 " + run.ExitCode + "）。"
                : "初始化未能启动：" + run.Error;
            Log(message);
            return new RunResult(ok, message);
        }

        private bool PortableDbRunning()
        {
            PythonCommand python = PythonForMode();
            if (python == null) return false;
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = python.FileName;
            psi.Arguments = python.Prefix + "scripts/portable_db.py status"
                + " --data-dir \"" + pgDataDir + "\""
                + " --port " + pgPort
                + " --log \"" + Path.Combine(root, "data", "postgresql.log") + "\"";
            psi.WorkingDirectory = Path.Combine(root, "backend");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            SetChildEnv(psi, "PYTHONIOENCODING", "utf-8");
            SetChildEnv(psi, "PYTHONPATH", Path.Combine(root, "backend"));
            SetChildEnv(psi, "PATH",
                Path.Combine(root, "runtime", "pgsql", "bin") + ";"
                + Environment.GetEnvironmentVariable("PATH"));
            PythonRunResult run = RunPythonProcess(psi, 30000, false);
            if (!run.Started || run.TimedOut || run.ExitCode != 0) return false;
            string json = LastJsonLine(run.StdOut.ToString());
            if (json.Length == 0) return false;
            try
            {
                Dictionary<string, object> map =
                    new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                return map != null && map.ContainsKey("running")
                    && Convert.ToBoolean(map["running"]);
            }
            catch { return false; }
        }

        private bool RunPortableDb(string action, bool streamToLog)
        {
            PythonCommand python = PythonForMode();
            if (python == null)
            {
                Log("找不到可用的内置 Python，无法执行内置数据库命令。");
                return false;
            }
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = python.FileName;
            psi.Arguments = python.Prefix + "scripts/portable_db.py " + action
                + " --data-dir \"" + pgDataDir + "\""
                + " --port " + pgPort
                + " --log \"" + Path.Combine(root, "data", "postgresql.log") + "\"";
            psi.WorkingDirectory = Path.Combine(root, "backend");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            SetChildEnv(psi, "PYTHONIOENCODING", "utf-8");
            SetChildEnv(psi, "PYTHONPATH", Path.Combine(root, "backend"));
            SetChildEnv(psi, "PATH",
                Path.Combine(root, "runtime", "pgsql", "bin") + ";"
                + Environment.GetEnvironmentVariable("PATH"));
            if (!String.IsNullOrEmpty(pgPassword))
                SetChildEnv(psi, "JIANJI_PG_PASSWORD", pgPassword);
            PythonRunResult run = RunPythonProcess(psi, 180000, streamToLog);
            bool ok = run.Started && !run.TimedOut && run.ExitCode == 0;
            if (!ok)
            {
                string detail = run.Started
                    ? "退出码 " + run.ExitCode
                    : "无法启动 " + run.Error;
                Log("内置数据库命令 " + action + " 失败：" + detail);
            }
            return ok;
        }

        private bool EnsurePortableDatabase()
        {
            try
            {
                string dataRoot = Path.Combine(root, "data");
                Directory.CreateDirectory(dataRoot);
                string probe = Path.Combine(dataRoot, ".write-test");
                File.WriteAllText(probe, "");
                File.Delete(probe);
            }
            catch (Exception ex)
            {
                ShowError("程序目录不可写，无法创建内置数据库。\n\n"
                    + "请把整个程序解压到普通可写目录（不要放 Program Files）。\n\n"
                    + ex.Message);
                return false;
            }
            bool initialized = File.Exists(Path.Combine(pgDataDir, "PG_VERSION"));
            if (String.IsNullOrEmpty(pgPassword) && initialized)
            {
                ShowError("内置数据库已存在，但配置文件中没有可用口令。\n\n"
                    + "请恢复配置文件，或备份数据后重新初始化。");
                return false;
            }
            if (!PortableDbRunning() && !IsPortFree(pgPort))
            {
                pgPort = PickFreePort(55432);
                SaveConfig();
                Log("内置数据库端口被占用，改用 " + pgPort);
            }
            if (String.IsNullOrEmpty(pgPassword))
            {
                pgPassword = GenerateDbPassword();
                SaveConfig();
            }
            if (!RunPortableDb("ensure", true))
            {
                ShowError("内置数据库启动失败。\n\n请查看下方日志和 data\\postgresql.log。");
                return false;
            }
            string marker = Path.Combine(root, "data", ".portable_initialized");
            if (!File.Exists(marker))
            {
                RunResult init = RunInitDb("127.0.0.1", pgPort, "jianji",
                    "postgres", pgPassword);
                if (!init.Ok)
                {
                    ShowError("内置数据库已启动，但初始化失败。\n\n请查看下方日志。");
                    return false;
                }
                try { File.WriteAllText(marker, pgVersion, Encoding.UTF8); }
                catch { }
            }
            return true;
        }

        private static string GenerateDbPassword()
        {
            byte[] bytes = new byte[32];
            using (RandomNumberGenerator rng = RandomNumberGenerator.Create())
                rng.GetBytes(bytes);
            return Convert.ToBase64String(bytes).Replace("+", "-")
                .Replace("/", "_").Replace("=", "");
        }

        private PythonRunResult RunPythonProcess(ProcessStartInfo psi, int timeoutMs,
            bool streamToLog)
        {
            PythonRunResult result = new PythonRunResult();
            try
            {
                using (Process p = new Process())
                {
                    p.StartInfo = psi;
                    p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
                    {
                        if (e.Data == null) return;
                        string line = CleanOutput(e.Data);
                        if (line.Length == 0) return;
                        if (streamToLog) Log("[db] " + line);
                        else result.StdOut.AppendLine(line);
                    };
                    p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
                    {
                        if (e.Data == null) return;
                        string line = CleanOutput(e.Data);
                        if (line.Length == 0) return;
                        if (streamToLog) Log("[db] " + line);
                        else result.StdErr.AppendLine(line);
                    };
                    p.Start();
                    result.Started = true;
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();
                    if (!p.WaitForExit(timeoutMs))
                    {
                        result.TimedOut = true;
                        try { p.Kill(); }
                        catch { }
                        p.WaitForExit();
                    }
                    else
                    {
                        p.WaitForExit();
                        result.ExitCode = p.ExitCode;
                    }
                }
            }
            catch (Exception ex)
            {
                result.Error = ex.Message;
            }
            return result;
        }

        private static string RunMessage(PythonRunResult run)
        {
            if (!run.Started) return run.Error;
            if (run.TimedOut) return "脚本执行超时。";
            string stderr = run.StdErr.ToString().Trim();
            if (stderr.Length > 0) return stderr;
            if (run.ExitCode != 0 && run.StdOut.Length == 0)
                return "脚本退出码 " + run.ExitCode + "。";
            return "";
        }

        private static string LastJsonLine(string text)
        {
            string[] lines = (text ?? "").Replace("\r", "").Split('\n');
            for (int i = lines.Length - 1; i >= 0; i--)
            {
                string line = lines[i].Trim();
                if (line.StartsWith("{") && line.EndsWith("}")) return line;
            }
            for (int i = lines.Length - 1; i >= 0; i--)
            {
                string line = lines[i].Trim();
                if (line.Length > 0) return line;
            }
            return "";
        }

        private static bool JsonBool(Dictionary<string, object> map, string key)
        {
            object raw;
            if (!map.TryGetValue(key, out raw)) return false;
            try { return Convert.ToBoolean(raw); }
            catch { return Convert.ToString(raw) == "true"; }
        }

        private static string JsonText(Dictionary<string, object> map, string key)
        {
            object raw;
            if (!map.TryGetValue(key, out raw)) return "";
            return Convert.ToString(raw) ?? "";
        }

        private sealed class DbProbe
        {
            public bool Ok;
            public bool DbExists;
            public string Version = "";
            public string Message = "";
        }

        private sealed class DbListResult
        {
            public bool Ok;
            public string Message = "";
            public readonly List<string> Databases = new List<string>();
        }

        private sealed class RunResult
        {
            public readonly bool Ok;
            public readonly string Message;

            public RunResult(bool ok, string message)
            {
                Ok = ok;
                Message = message;
            }
        }

        private sealed class PythonRunResult
        {
            public bool Started;
            public bool TimedOut;
            public int ExitCode = -1;
            public string Error = "";
            public readonly StringBuilder StdOut = new StringBuilder();
            public readonly StringBuilder StdErr = new StringBuilder();
        }

        private void BuildTitleBar(string iconPath)
        {
            Panel bar = new Panel();
            bar.Dock = DockStyle.Top;
            bar.Height = 52;
            bar.BackColor = Palette.TitleBar;
            Controls.Add(bar);

            BrandMark mark = new BrandMark();
            mark.Location = new Point(20, 14);
            mark.Size = new Size(24, 24);
            mark.BackColor = bar.BackColor;
            bar.Controls.Add(mark);

            Label name = new Label();
            name.Text = "简籍";
            name.Font = new Font("Microsoft YaHei UI", 10F, FontStyle.Bold, GraphicsUnit.Point);
            name.ForeColor = Palette.Ink;
            name.AutoSize = true;
            name.BackColor = bar.BackColor;
            name.Location = new Point(52, 16);
            bar.Controls.Add(name);

            Label section = new Label();
            section.Text = "控制台";
            section.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            section.ForeColor = Palette.Muted;
            section.AutoSize = true;
            section.BackColor = bar.BackColor;
            section.Location = new Point(96, 17);
            bar.Controls.Add(section);

            string version = Application.ProductVersion;
            if (version.EndsWith(".0") && version.Split('.').Length == 4)
                version = version.Substring(0, version.Length - 2);
            PillLabel versionPill = new PillLabel();
            versionPill.Text = "v" + version;
            versionPill.Location = new Point(160, 16);
            versionPill.Size = new Size(56, 20);
            versionPill.Radius = 999;
            versionPill.Font = new Font("Segoe UI", 8F, FontStyle.Regular, GraphicsUnit.Point);
            versionPill.BackColor = bar.BackColor;
            versionPill.TextAlign = ContentAlignment.MiddleCenter;
            versionPill.SetPalette(Color.FromArgb(242, 240, 233), Palette.Line,
                Palette.Muted, Palette.Primary);
            bar.Controls.Add(versionPill);

            Label minimize = MakeWindowButton("—", false, 728, 11);
            minimize.Click += delegate { WindowState = FormWindowState.Minimized; };
            bar.Controls.Add(minimize);

            Label close = MakeWindowButton("×", true, 764, 11);
            close.Click += delegate { Close(); };
            bar.Controls.Add(close);

            AttachDrag(bar);
            AttachDrag(mark);
            AttachDrag(name);
            AttachDrag(section);
            AttachDrag(versionPill);

            Panel accent = new Panel();
            accent.Dock = DockStyle.Top;
            accent.Height = 2;
            accent.BackColor = bar.BackColor;
            accent.Paint += delegate(object s, PaintEventArgs e)
            {
                using (LinearGradientBrush brush = new LinearGradientBrush(
                    accent.ClientRectangle, Palette.Primary, Palette.Accent, 0F))
                {
                    ColorBlend blend = new ColorBlend();
                    blend.Colors = new Color[] { Palette.Primary, Palette.Primary600, Palette.Accent };
                    blend.Positions = new float[] { 0F, 0.56F, 1F };
                    brush.InterpolationColors = blend;
                    e.Graphics.FillRectangle(brush, accent.ClientRectangle);
                }
            };
            bar.Controls.Add(accent);
            accent.BringToFront();

            Panel line = new Panel();
            line.Dock = DockStyle.Bottom;
            line.Height = 1;
            line.BackColor = Palette.Line;
            bar.Controls.Add(line);
        }

        private Label MakeWindowButton(string text, bool close, int x, int y)
        {
            Label b = new Label();
            b.Text = text;
            b.Location = new Point(x, y);
            b.Size = new Size(28, 30);
            b.TextAlign = ContentAlignment.MiddleCenter;
            b.Font = new Font("Segoe UI", 11F, FontStyle.Regular, GraphicsUnit.Point);
            b.ForeColor = Palette.Muted;
            b.BackColor = Palette.TitleBar;
            b.Cursor = Cursors.Hand;
            b.MouseEnter += delegate
            {
                b.BackColor = close ? Palette.Danger : Palette.SurfaceSoft;
                b.ForeColor = close ? Color.White : Palette.Ink;
            };
            b.MouseLeave += delegate
            {
                b.BackColor = Palette.TitleBar;
                b.ForeColor = Palette.Muted;
            };
            return b;
        }

        private void AttachDrag(Control control)
        {
            control.MouseDown += DragWindow;
        }

        private void DragWindow(object sender, MouseEventArgs e)
        {
            if (e.Button != MouseButtons.Left) return;
            ReleaseCapture();
            SendMessage(Handle, 0xA1, (IntPtr)0x2, IntPtr.Zero);
        }

        [DllImport("user32.dll")]
        private static extern bool ReleaseCapture();

        [DllImport("user32.dll")]
        private static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            try
            {
                int preference = 2;
                DwmSetWindowAttribute(Handle, 33, ref preference, sizeof(int));
            }
            catch { }
        }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams cp = base.CreateParams;
                cp.ClassStyle |= 0x20000;
                return cp;
            }
        }

        protected override void OnPaintBackground(PaintEventArgs e)
        {
            if (Width <= 0 || Height <= 0) return;
            // 暖纸底用纯色：WinForms 的 Label/自绘控件不透明，渐变或径向光晕会在
            // 标题、页签这些控件周围露出方形色块，反而显脏。
            e.Graphics.Clear(Palette.BgTop);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            foreach (Rectangle rect in commonCardShadows) DrawSoftShadow(e.Graphics, rect);
            foreach (Rectangle rect in runPageActive ? runCardShadows : dbCardShadows)
                DrawSoftShadow(e.Graphics, rect);
        }

        private static void DrawSoftShadow(Graphics g, Rectangle rect)
        {
            for (int i = 4; i >= 1; i--)
            {
                Rectangle shadow = rect;
                shadow.Inflate(i, i);
                shadow.Offset(0, 2);
                using (GraphicsPath path = RoundedPanel.RoundRect(shadow, 16))
                using (SolidBrush brush = new SolidBrush(Color.FromArgb(5, Palette.Shadow)))
                    g.FillPath(brush, path);
            }
        }

        private void AlignHost(Label label)
        {
            label.AutoSize = false;
            label.Size = new Size(100, 20);
            label.TextAlign = ContentAlignment.MiddleRight;
            label.Location = new Point(516, label.Location.Y);
        }

        private static Panel AddDot(Panel card, int x, int y, int size)
        {
            Panel dot = new Panel();
            dot.Location = new Point(x, y);
            dot.Size = new Size(size, size);
            RoundedPanel rounded = card as RoundedPanel;
            dot.BackColor = rounded != null ? rounded.FillColor : Color.White;
            dot.Tag = Palette.Faint;
            dot.Paint += delegate(object s, PaintEventArgs e)
            {
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                Color color = dot.Tag is Color ? (Color)dot.Tag : Color.Gray;
                int d = Math.Min(dot.Width, dot.Height);
                int halo = Math.Max(1, d - 4);
                using (SolidBrush glow = new SolidBrush(Color.FromArgb(34, color)))
                    e.Graphics.FillEllipse(glow, 0, 0, d, d);
                using (SolidBrush solid = new SolidBrush(color))
                    e.Graphics.FillEllipse(solid, (d - halo) / 2, (d - halo) / 2, halo, halo);
            };
            card.Controls.Add(dot);
            return dot;
        }

        private static Label AddCardTitle(Panel card, string text, int x, int y)
        {
            Label l = new Label();
            l.Text = text;
            l.ForeColor = Palette.Ink;
            l.BackColor = Palette.Surface;
            l.Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Bold, GraphicsUnit.Point);
            l.AutoSize = true;
            l.Location = new Point(x, y);
            card.Controls.Add(l);
            return l;
        }

        private static Label AddInlineStatus(Panel card, Label title, string text, Color color)
        {
            Label l = new Label();
            l.Text = text;
            l.ForeColor = color;
            l.BackColor = Palette.Surface;
            l.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold, GraphicsUnit.Point);
            l.AutoSize = true;
            int width = TextRenderer.MeasureText(title.Text, title.Font,
                new Size(Int32.MaxValue, Int32.MaxValue), TextFormatFlags.NoPadding).Width;
            l.Location = new Point(title.Location.X + width + 8, title.Location.Y + 5);
            card.Controls.Add(l);
            return l;
        }

        private static Label AddCardSub(Panel card, string text, int x, int y)
        {
            Label l = new Label();
            l.Text = text;
            l.ForeColor = Palette.Muted;
            l.BackColor = Palette.Surface;
            l.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            l.AutoSize = true;
            l.Location = new Point(x, y);
            card.Controls.Add(l);
            return l;
        }

        private static Label AddCardDetail(Panel card, string text, int x, int y)
        {
            Label l = new Label();
            l.Text = text;
            l.ForeColor = Palette.Muted;
            l.BackColor = Palette.Surface;
            l.Font = new Font("Consolas", 9F, FontStyle.Regular, GraphicsUnit.Point);
            l.AutoSize = true;
            l.Location = new Point(x, y);
            card.Controls.Add(l);
            return l;
        }

        private static RoundedButton MakeButton(string text, bool primary, int x, int y, int w)
        {
            RoundedButton b = new RoundedButton();
            b.Primary = primary;
            b.Text = text;
            b.Location = new Point(x, y);
            b.Size = new Size(w, 44);
            b.Radius = 9;
            b.Font = new Font("Microsoft YaHei UI", 9.5F, FontStyle.Bold, GraphicsUnit.Point);
            return b;
        }

        /// <summary>
        /// 日志超过 5 MB 就轮转，只保留上一份（`launcher.log.1`）。
        ///
        /// 不做「按天留 7 天」：现场几乎不看历史，而多份文件会让「日志到底在哪一份」
        /// 重新变成一个问题。保留一份足够回答「上次启动发生了什么事」。
        /// </summary>
        private void RotateLogIfNeeded()
        {
            try
            {
                FileInfo info = new FileInfo(launcherLog);
                if (!info.Exists || info.Length < LogRotateBytes) return;
                string backup = launcherLog + ".1";
                if (File.Exists(backup)) File.Delete(backup);
                File.Move(launcherLog, backup);
                File.AppendAllText(launcherLog,
                    DateTime.Now.ToString("HH:mm:ss")
                    + "  （日志已超过 5 MB，轮转完成：上一份见 launcher.log.1）"
                    + Environment.NewLine, new UTF8Encoding(false));
                logWrites = 0;
            }
            catch { }       // 轮转失败不能影响写日志（磁盘满/被杀软占用都可能）
        }

        /// <summary>
        /// 追加一行日志。关键点：**用户往上翻的时候就别把他弹回底部**。
        /// 之前每行都 `ScrollToCaret()`，而健康检查每 2.5 秒产生一行访问日志，
        /// 于是滚上去立刻被拽回来，历史根本看不住。
        /// </summary>
        private void Log(string message)
        {
            string line = DateTime.Now.ToString("HH:mm:ss") + "  " + message;
            if (line.Length > logMaxChars) logMaxChars = line.Length;
            lock (logLock)
            {
                try
                {
                    // 每 200 行查一次大小（每行都 FileInfo 太贵，200 行也就几十 KB）
                    logWrites++;
                    if (logWrites % 200 == 0) RotateLogIfNeeded();
                    File.AppendAllText(launcherLog, line + Environment.NewLine, new UTF8Encoding(false));
                }
                catch { }
            }
            Ui(delegate
            {
                if (logBox == null || logBox.IsDisposed) return;
                bool stick = LogStuckToBottom();
                int firstLine = 0;
                if (!stick)
                    firstLine = SendMessage(logBox.Handle, EmGetFirstVisibleLine, IntPtr.Zero, IntPtr.Zero).ToInt32();

                logBox.AppendText(line + Environment.NewLine);
                if (stick && logBox.TextLength > 120000)
                    logBox.Text = logBox.Text.Substring(logBox.TextLength - 80000);
                UpdateLogScrollBars();

                if (stick)
                {
                    logBox.SelectionStart = logBox.TextLength;
                    logBox.ScrollToCaret();
                }
                else
                {
                    // AppendText 会把视图带走，按行数把它挪回原来的位置
                    int nowLine = SendMessage(logBox.Handle, EmGetFirstVisibleLine, IntPtr.Zero, IntPtr.Zero).ToInt32();
                    if (nowLine != firstLine)
                        SendMessage(logBox.Handle, EmLineScroll, IntPtr.Zero, (IntPtr)(firstLine - nowLine));
                }
                UpdateLogJump(stick);
            });
        }

        /// <summary>
        /// 只有内容超出可视行数时才显示竖向滚动条，空闲状态保持一整块干净的纸面。
        /// 切换 ScrollBars 会把视图带回顶部，所以这里先记住首行、切换后还原。
        /// </summary>
        private void UpdateLogScrollBars()
        {
            if (logBox == null || logBox.IsDisposed || !logBox.IsHandleCreated) return;
            int total = logBox.GetLineFromCharIndex(logBox.TextLength) + 1;
            int lineHeight = Math.Max(12, logBox.Font.Height + 3);
            int visible = Math.Max(1, logBox.ClientSize.Height / lineHeight);
            bool tall = total > visible + 1;
            // 长行（异常堆栈、一整段 JSON）以前被静默裁掉且没有横条，等于看不全。
            // WordWrap=false 是为了日志保持「一行一件事」，所以补横条而不是改成自动换行。
            bool wide = logMaxChars > 0 && logMaxChars * LogCharWidth() > logBox.ClientSize.Width - 4;
            ScrollBars wanted = tall
                ? (wide ? ScrollBars.Both : ScrollBars.Vertical)
                : (wide ? ScrollBars.Horizontal : ScrollBars.None);
            if (logBox.ScrollBars == wanted) return;
            int first = SendMessage(logBox.Handle, EmGetFirstVisibleLine,
                IntPtr.Zero, IntPtr.Zero).ToInt32();
            logBox.ScrollBars = wanted;
            int now = SendMessage(logBox.Handle, EmGetFirstVisibleLine,
                IntPtr.Zero, IntPtr.Zero).ToInt32();
            if (now != first)
                SendMessage(logBox.Handle, EmLineScroll, IntPtr.Zero, (IntPtr)(first - now));
        }

        /// <summary>等宽字体下单个字符的宽度（量一次就够，日志框字体在运行时不变）。</summary>
        private int LogCharWidth()
        {
            if (logCharWidth > 0) return logCharWidth;
            try
            {
                logCharWidth = Math.Max(1,
                    TextRenderer.MeasureText("0000000000", logBox.Font).Width / 10);
            }
            catch { logCharWidth = 7; }
            return logCharWidth;
        }

        /// <summary>日志是否停在末尾（用户在翻历史时为 false）。</summary>
        private bool LogStuckToBottom()
        {
            if (logBox == null || logBox.IsDisposed || logBox.TextLength == 0) return true;
            Point probe = new Point(2, Math.Max(2, logBox.ClientSize.Height - 4));
            return logBox.GetCharIndexFromPosition(probe) >= logBox.TextLength - 2;
        }

        private void UpdateLogJump(bool stick)
        {
            if (logJump == null || logJump.IsDisposed) return;
            if (stick) logJump.Visible = false;
            else if (!logJump.Visible) logJump.Visible = true;
        }

        private void Ui(Action action)
        {
            if (IsDisposed || Disposing) return;
            try
            {
                if (InvokeRequired) BeginInvoke(action);
                else action();
            }
            catch (InvalidOperationException) { }
        }

        private void SetStatus(Label label, Panel dot, string text, Color color)
        {
            Ui(delegate
            {
                if (label == null || label.IsDisposed) return;
                label.Text = text;
                label.ForeColor = color;
                if (dot != null && !dot.IsDisposed)
                {
                    dot.Tag = color;
                    dot.Invalidate();
                }
            });
        }

        private void PollTick(object sender, EventArgs e)
        {
            if (starting || polling) return;
            polling = true;
            Task.Run(delegate
            {
                bool dbOk;
                bool backend = CheckBackend(out dbOk);
                bool frontend = CheckFrontend();
                Ui(delegate
                {
                    ApplyHealth(backend, dbOk, frontend);
                    polling = false;
                });
            });
        }

        private void ApplyHealth(bool backend, bool dbOk, bool frontend)
        {
            if (starting) return;
            if (backendDbInfo != null)
            {
                backendDbInfo.Text = backend && dbOk && actualDbLabel.Length > 0
                    ? "实际库：" + actualDbLabel : "";
                backendDbInfo.Visible = backend && dbOk && actualDbLabel.Length > 0;
            }
            RefreshSchemaWarning(backend, dbOk);
            // 绑到 0.0.0.0 时如果还显示 127.0.0.1，会让人误以为外部访问没生效（2026-10-23 修）
            string host = BindHost;

            if (backend)
            {
                if (dbOk)
                {
                    SetStatus(backendStatus, backendDot, "运行中", Palette.Success);
                    backendDetail.Text = host;
                }
                else
                {
                    SetStatus(backendStatus, backendDot, "数据库未连接", Palette.Accent);
                    backendDetail.Text = host;
                }
            }
            else
            {
                SetStatus(backendStatus, backendDot, everStarted ? "已停止" : "未启动",
                    Palette.Muted);
                backendDetail.Text = host;
            }

            if (SinglePort)
            {
                SetStatus(frontendStatus, frontendDot,
                    backend ? "由后端托管" : (everStarted ? "已停止" : "未启动"),
                    backend ? Palette.Success : Palette.Muted);
                frontendDetail.Text = "同后端";
                frontendBadge.Text = backendPort + " 端口";
                frontendBadge.Visible = true;
                if (frontendPortWrap != null) frontendPortWrap.Visible = false;
            }
            else
            {
                if (frontendBadge != null) frontendBadge.Visible = false;
                if (frontendPortWrap != null) frontendPortWrap.Visible = true;
                if (frontend)
                {
                    SetStatus(frontendStatus, frontendDot, "运行中", Palette.Success);
                    frontendDetail.Text = host;
                }
                else
                {
                    SetStatus(frontendStatus, frontendDot, everStarted ? "已停止" : "未启动",
                        Palette.Muted);
                    frontendDetail.Text = host;
                }
            }

            bool anyRunning = backend || frontend;
            // 关键：只要服务在跑就必须能点「停止服务」。
            // 旧逻辑只看「是不是本启动器拉起的进程」，于是上次关掉启动器（服务留着继续跑）
            // 再打开时，状态显示「运行中」但按钮是灰的 —— 用户只能去任务管理器杀。
            stopBtn.Enabled = anyRunning && !starting && !stopping;
            startBtn.Enabled = !starting && !stopping;
            startBtn.Text = (backend && frontend) ? "打开界面" : "启动并打开";
            backendPortBox.Enabled = !anyRunning && !starting;
            frontendPortBox.Enabled = !anyRunning && !starting && !SinglePort;
            lanSwitch.Enabled = !starting;
            lanLabel.ForeColor = lanSwitch.Enabled
                ? Palette.Ink : Palette.Faint;
        }

        /// <summary>
        /// 刷新「库结构落后」提示条。
        ///
        /// 只在**后端在跑 + 库连得上 + 结构落后**时显示。三个条件缺一不可：
        /// 服务没起来时说"库结构落后"会误导（可能只是没启动）。
        /// </summary>
        private void RefreshSchemaWarning(bool backend, bool dbOk)
        {
            if (schemaWarning == null || schemaWarning.IsDisposed) return;
            bool show = backend && dbOk && !schemaOk;
            // 只在状态**变化**时记一行：这个方法是 2.5 秒一次的轮询回调，
            // 每次都写会把日志刷满（实测 8 秒就 18 行）。
            if (show != schemaWarned)
            {
                schemaWarned = show;
                Log(show
                    ? "检测到数据库结构落后于代码（需 " + schemaHead + "），可点「升级数据库」"
                    : "数据库结构已是最新。");
            }
            schemaWarning.Visible = show;
            if (schemaUpgradeBtn != null) schemaUpgradeBtn.Visible = show;
            if (!show) return;
            string head = schemaHead.Length > 6 ? schemaHead.Substring(0, 6) : schemaHead;
            schemaWarning.Text = "⚠ 数据库结构落后于代码（需 " + head
                + "）：部分页面会报错，点右侧升级";
        }

        /// <summary>
        /// 一键升级数据库结构（跑 `alembic upgrade head`）。
        ///
        /// 为什么要先停服务：迁移里有 `CREATE INDEX CONCURRENTLY` 与数据清洗，
        /// 服务在跑时并发写入会让建索引一直等（甚至失败）。所以流程是
        /// 「确认 → 停服务 → 备份 → 升级 → 提示重新启动」，与《升级与回滚手册》一致。
        /// </summary>
        /// <summary>
        /// 一键升级数据库结构。
        ///
        /// ## 为什么由**后端**执行，不是启动器自己跑 alembic
        ///
        /// 启动器只知道端口，不知道后端实际连的是哪个库——`/api/health` 刻意不返回
        /// 用户名与口令。于是启动器去跑 `alembic upgrade head` 时，只能靠
        /// `launcher-config.json` 里保存的 `db_*`；**没保存过就退回默认库 jianji**。
        ///
        /// 如果服务连的是 A 库、启动器升级的却是 B 库，界面会显示成功但实际库没有变化。
        /// **升错库比不升级更危险。**
        ///
        /// 现在改成调 `POST /api/maintenance/upgrade-schema`：后端手里就是运行中的那份
        /// 连接，alembic 用的必然是同一个库。代价是**要求服务正在运行**（这也合理：
        /// 只有服务在跑才知道该升哪个库）。
        /// </summary>
        private async Task UpgradeSchema()
        {
            bool dbOk;
            if (!CheckBackend(out dbOk) || !dbOk)
            {
                ShowError("升级需要后端服务正在运行（启动器要问它连的是哪个库）。\n\n"
                    + "请先点「启动并打开」，等服务起来后再点「升级数据库」。");
                return;
            }

            DialogResult answer = MessageBox.Show(this,
                "将把当前数据库结构升级到最新版本。\n\n"
                + "• 升级前请确认已有备份（《升级与回滚手册》第 1 节）\n"
                + "• 大表建索引可能让服务短暂变慢\n"
                + "• 升级过程不要关闭本窗口\n\n继续吗？",
                "升级数据库结构",
                MessageBoxButtons.YesNo, MessageBoxIcon.Warning,
                MessageBoxDefaultButton.Button2);
            if (answer != DialogResult.Yes) return;

            schemaUpgradeBtn.Enabled = false;
            try
            {
                Log("开始升级数据库结构（由后端执行，目标是它实际连接的那个库）…");
                string body = await Task.Run(delegate
                {
                    return PostMaintenance("upgrade-schema");
                });
                foreach (string line in body.Split(new string[] { "\r\n", "\n" },
                                                   StringSplitOptions.RemoveEmptyEntries))
                    Log("  " + line);

                bool ok;
                ApplyHealth(CheckBackend(out ok), ok, CheckFrontend());
                if (schemaOk)
                {
                    Log("数据库结构已是最新。");
                    MessageBox.Show(this, "升级完成，数据库结构已是最新。",
                        "升级数据库结构", MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
                else
                {
                    ShowError("后端返回了成功，但结构仍显示落后。请查看下方日志，"
                        + "或到命令行执行数据库升级命令。");
                }
            }
            catch (Exception ex)
            {
                ShowError("升级失败：" + ex.Message + "\n\n详情见下方日志。");
            }
            finally
            {
                schemaUpgradeBtn.Enabled = true;
            }
        }

        /// <summary>
        /// 调维护接口（同步版，供 Task.Run 用）。
        ///
        /// 管理员接口需要登录令牌，而启动器不持有会话——所以这条接口额外接受
        /// **本机回环 + 启动器标记头**：它只能从这台机器的 127.0.0.1 调用，
        /// 且不返回任何数据，只触发一次迁移。
        /// </summary>
        private string PostMaintenance(string action)
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(
                BackendUrl + "/api/maintenance/" + action);
            req.Method = "POST";
            req.Proxy = null;
            req.KeepAlive = false;
            req.ContentType = "application/json";
            req.Timeout = 600000;
            req.ReadWriteTimeout = 600000;
            req.Headers.Add("X-Launcher-Local", "1");
            byte[] payload = Encoding.UTF8.GetBytes("{}");
            req.ContentLength = payload.Length;
            using (Stream s = req.GetRequestStream()) s.Write(payload, 0, payload.Length);

            try
            {
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (StreamReader reader = new StreamReader(resp.GetResponseStream()))
                    return reader.ReadToEnd();
            }
            catch (WebException ex)
            {
                // 4xx/5xx 的响应体里才有真正的失败原因（FastAPI 的 detail）
                if (ex.Response != null)
                {
                    using (StreamReader reader = new StreamReader(ex.Response.GetResponseStream()))
                    {
                        string detail = reader.ReadToEnd();
                        throw new InvalidOperationException(
                            ExtractDetail(detail) ?? detail);
                    }
                }
                throw;
            }
        }

        /// <summary>从 FastAPI 的错误响应里取出 detail 字段（取不到就返回 null）。</summary>
        private static string ExtractDetail(string json)
        {
            if (String.IsNullOrEmpty(json)) return null;
            try
            {
                Dictionary<string, object> cfg =
                    new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                return cfg != null && cfg.ContainsKey("detail")
                    ? Convert.ToString(cfg["detail"]) : null;
            }
            catch
            {
                return null;
            }
        }

        private async Task StartAll(bool openBrowser)
        {
            if (starting) return;
            int bp;
            int fp;
            if (!TryReadPorts(out bp, out fp)) return;
            backendPort = bp;
            frontendPort = fp;
            SaveConfig();
            starting = true;
            everStarted = true;
            backendPortBox.Enabled = false;
            frontendPortBox.Enabled = false;
            startBtn.Enabled = false;
            startBtn.Text = "启动中…";
            stopBtn.Enabled = false;
            SetStatus(backendStatus, backendDot, "启动中…", Palette.Accent);
            SetStatus(frontendStatus, frontendDot, "启动中…", Palette.Accent);
            backendDetail.Text = "正在启动…";
            frontendDetail.Text = "正在启动…";
            Log("开始启动服务…");
            if (lanAccess)
            {
                Log("注意：局域网模式下同网段的电脑都能打开登录页。");
                Log("      请先在本机完成「创建管理员」，再开放局域网——管理员建好之前，");
                Log("      后端会拒绝除探活外的接口（管理员创建前，其他设备无法使用系统）。");
            }

            bool ok = await Task.Run(() => StartBackend());
            if (ok)
                ok = await Task.Run(() => StartFrontend());

            starting = false;
            startBtn.Text = "启动并打开";
            bool dbOk;
            bool backend = CheckBackend(out dbOk);
            bool frontend = CheckFrontend();
            ApplyHealth(backend, dbOk, frontend);

            if (ok && openBrowser)
                OpenBrowser();
            else if (!ok)
                ShowError("服务没有完全启动，请查看下方日志。");
        }

        private bool StartBackend()
        {
            if (dbMode == "portable")
            {
                if (!EnsurePortableDatabase()) return false;
                dbConfigured = true;
                dbHost = "127.0.0.1";
                dbPort = pgPort;
                dbName = "jianji";
                dbUser = "postgres";
                dbPassword = pgPassword;
            }

            bool dbOk;
            if (CheckBackend(out dbOk))
            {
                Log("后端已在运行，跳过启动。");
                return true;
            }

            PythonCommand python = PythonForMode();
            if (python == null && dbMode == "portable")
            {
                ShowError("安装包不完整：找不到可用的内置 Python。\n\n请重新下载 portable 包。");
                return false;
            }
            if (python == null)
            {
                Log("找不到可用的 Python（需要能 import fastapi / uvicorn）。");
                Log("请先安装后端依赖：backend\\requirements.lock.txt");
                // 以前只写日志：界面上只有一个「服务没有完全启动」，用户不知道是缺 Python
                // 还是端口被占、还是代码报错，只能把日志整段发给别人看。
                ShowError("找不到可用的 Python，后端无法启动。\n\n按顺序排查：\n"
                    + "1) 装过后端依赖吗（backend\\requirements.lock.txt）；\n"
                    + "2) 删过 backend\\.venv 吗——删了要重建虚拟环境并重装依赖；\n"
                    + "3) 系统里是否装了 Python 3.12 及以上，且能 import fastapi / uvicorn。\n\n"
                    + "排查过程见下方日志。");
                return false;
            }

            // 端口预检：端口被别人占着时，uvicorn 的 [Errno 10048] 只会埋在日志里，
            // 弹窗却是一句「服务没有完全启动」——现场分不清是端口问题还是程序坏了。
            if (!IsPortFree(backendPort))
            {
                string owner = PortOwnerLabel(backendPort);
                Log("端口 " + backendPort + " 已被占用" + (owner.Length > 0 ? "：" + owner : "。"));
                ShowError("端口 " + backendPort + " 已被占用，后端无法启动。\n\n"
                    + (owner.Length > 0 ? "占用进程：" + owner + "\n\n" : "")
                    + "处置：\n"
                    + "1) 是上一次没退干净的 python / uvicorn → 点「停止服务」，或按上面的 PID 结束它；\n"
                    + "2) 是别的程序占着 → 在上方换一个端口再启动。");
                return false;
            }

            Log("后端 Python：" + python.Display);
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = python.FileName;
            psi.Arguments = python.Prefix + "-m uvicorn app.main:app --host " + BindHost + " --port " + backendPort;
            psi.WorkingDirectory = Path.Combine(root, "backend");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            SetChildEnv(psi, "PYTHONIOENCODING", "utf-8");

            // 数据库连接：只有用户在「数据库」页保存过才注入。没保存时保持
            // 原行为（子进程继承系统 JIANJI_DATABASE_URL），升级启动器
            // 不会把已有部署悄悄切到默认库。
            if (dbConfigured)
            {
                string password = EffectiveDbPassword();
                SetChildEnv(psi, "JIANJI_DATABASE_URL",
                    BuildDatabaseUrl(dbHost, dbPort, dbName, dbUser, password));
                Log("数据库连接：" + DbTargetLabel() +
                    (password.Length == 0 ? "（口令沿用 pgpass/系统设置）" : ""));
            }
            else
            {
                Log("数据库连接：沿用系统环境/默认库（未在启动器保存数据库设置）。");
            }

            // 显式告诉后端「这次是对外监听」。后端不能靠请求来源 IP 判断——前端
            // 是代理，后端看到的来源恒为 127.0.0.1。没有这个变量，后端就分不清
            // 自己绑的是回环还是网卡，也就无法在「还没有管理员」时收口。
            // （后端也会从 --host 兜底推断，这里显式给一份，不依赖解析命令行。）
            if (lanAccess)
            {
                SetChildEnv(psi, "JIANJI_LAN_MODE", "1");
                // 启动时也补一次防火墙放行：配置里已经是 lan_access=true 的用户不会
                // 再点开关，只在 LanToggled 里做就会漏掉他们（真机踩过）。
                EnsureFirewallRule();
            }

            // 局域网模式：把本机内网来源显式加进 CORS 白名单。
            // 后端一旦读到这个变量就**不再**用本机回环正则，所以 localhost 三个变体要一起给，
            // 否则本机浏览器反而会被拦。
            if (lanAccess)
            {
                string lanOrigin = LanUrl;
                if (lanOrigin != null)
                {
                    string existing = GetChildEnv(psi, "JIANJI_CORS_ORIGINS");
                    string merged = existing == null ? "" : existing.Trim();
                    string[] add = new string[]
                    {
                        lanOrigin,
                        "http://localhost:" + frontendPort,
                        "http://127.0.0.1:" + frontendPort,
                    };
                    foreach (string origin in add)
                    {
                        if (merged.IndexOf(origin, StringComparison.OrdinalIgnoreCase) >= 0) continue;
                        merged = merged.Length == 0 ? origin : merged + "," + origin;
                    }
                    SetChildEnv(psi, "JIANJI_CORS_ORIGINS", merged);
                    Log("已放行局域网来源：" + lanOrigin);
                }
                else
                {
                    Log("未取到本机内网地址，局域网可能无法访问。");
                }
            }

            try
            {
                backendProc = StartProcess(psi, "backend");
            }
            catch (Exception ex)
            {
                Log("后端启动失败：" + ex.Message);
                return false;
            }

            Log("后端启动中…");
            for (int i = 0; i < 80; i++)
            {
                Thread.Sleep(500);
                if (CheckBackend(out dbOk))
                {
                    Log(dbOk ? "后端已就绪。" : "后端已启动，但数据库未连接。");
                    return true;
                }
                if (backendProc == null || backendProc.HasExited)
                {
                    Log("后端进程已退出。");
                    return false;
                }
            }
            Log("后端启动超时。");
            return false;
        }

        private bool StartFrontend()
        {
            if (SinglePort)
            {
                Log("前端由后端托管（frontend\\dist），不启动独立前端进程。");
                return true;
            }
            if (CheckFrontend())
            {
                Log("前端已在运行，跳过启动。");
                return true;
            }

            string node = FindNode();
            if (node == null)
            {
                Log("找不到 Node.js（需要 20+）。");
                return false;
            }

            string vite = Path.Combine(root, "frontend", "node_modules", "vite", "bin", "vite.js");
            if (!File.Exists(vite))
            {
                Log("前端依赖未安装，正在执行 npm install…");
                if (!RunNpmInstall(node))
                {
                    Log("前端依赖安装失败。");
                    return false;
                }
            }

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = node;

            // 有构建产物就用 preview（静态文件 + 压缩 + 长效缓存），否则退回 dev。
            //
            // 为什么不一上来就用 dev：开发服务器不压缩、常驻文件监听、把 /src/**
            // 源码原样暴露，而且它自己没有任何鉴权却把请求原样转发给后端——对外
            // 服务时安全边界就落在一个为热更新设计的进程上。
            string distIndex = Path.Combine(root, "frontend", "dist", "index.html");
            bool hasBuild = File.Exists(distIndex);
            string mode = hasBuild ? "preview" : "dev";
            psi.Arguments = "\"" + vite + "\" " + mode + " --host " + BindHost
                + " --port " + frontendPort + " --strictPort";
            psi.WorkingDirectory = Path.Combine(root, "frontend");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            SetChildEnv(psi, "VITE_API_TARGET", BackendUrl);

            if (hasBuild)
            {
                Log("前端以构建产物运行（vite preview）。");
            }
            else
            {
                Log("未找到 frontend\\dist，前端以开发服务器运行。");
                Log("  仅适合本机调试；正式部署请先执行：cd frontend && npm run build");
                if (lanAccess)
                {
                    Log("  ⚠ 局域网模式下不建议用开发服务器：它会把前端源码一并暴露给同网段。");
                }
            }

            try
            {
                frontendProc = StartProcess(psi, "frontend");
            }
            catch (Exception ex)
            {
                Log("前端启动失败：" + ex.Message);
                return false;
            }

            Log("前端启动中…");
            for (int i = 0; i < 120; i++)
            {
                Thread.Sleep(500);
                if (CheckFrontend())
                {
                    Log("前端已就绪。");
                    return true;
                }
                if (frontendProc == null || frontendProc.HasExited)
                {
                    Log("前端进程已退出。");
                    return false;
                }
            }
            Log("前端启动超时。");
            return false;
        }

        private Process StartProcess(ProcessStartInfo psi, string tag)
        {
            Process p = new Process();
            p.StartInfo = psi;
            p.EnableRaisingEvents = true;
            p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
            {
                string line = CleanOutput(e.Data);
                if (line.Length > 0 && !HealthNoise.IsMatch(line)) Log("[" + tag + "] " + line);
            };
            p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
            {
                string line = CleanOutput(e.Data);
                if (line.Length > 0 && !HealthNoise.IsMatch(line)) Log("[" + tag + "] " + line);
            };
            p.Exited += delegate
            {
                Log("[" + tag + "] 进程已退出");
                Ui(delegate
                {
                    bool dbOk;
                    ApplyHealth(CheckBackend(out dbOk), dbOk, CheckFrontend());
                });
            };
            p.Start();
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            return p;
        }

        private static string CleanOutput(string value)
        {
            if (String.IsNullOrEmpty(value)) return "";
            return Ansi.Replace(value, "").TrimEnd('\r', '\n');
        }

        private bool RunNpmInstall(string node)
        {
            string npm = FindNpm(node);
            if (npm == null)
            {
                Log("找不到 npm。");
                return false;
            }
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = "cmd.exe";
            psi.Arguments = "/c \"" + npm + "\" install";
            psi.WorkingDirectory = Path.Combine(root, "frontend");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            Process p = StartProcess(psi, "npm");
            p.WaitForExit();
            bool ok = p.ExitCode == 0;
            p.Dispose();
            return ok;
        }

        /// <summary>
        /// 停止服务：先停本启动器拉起的进程，再按端口兜底停掉「不是本启动器启动、
        /// 但确实占着这两个端口的服务」。后者是常见情形 —— 上次关启动器时选了
        /// 「保持运行并退出」，再打开时界面显示运行中，进程却不归本次实例管。
        /// </summary>
        private async Task StopAll()
        {
            if (starting || stopping) return;
            // 是「同步结束进程」这件事本身会阻塞（taskkill 期间等退出），放到后台跑，
            // 否则点一下按钮窗口会卡两三秒，看着像没反应。
            stopping = true;
            Ui(delegate
            {
                stopBtn.Text = "停止中…";
                stopBtn.Enabled = false;
                startBtn.Enabled = false;
                SetStatus(backendStatus, backendDot, "停止中…", Palette.Accent);
                SetStatus(frontendStatus, frontendDot, "停止中…", Palette.Accent);
            });

            try
            {
                await Task.Run(delegate { StopAllCore(); });
            }
            finally
            {
                stopping = false;
                Ui(delegate
                {
                    stopBtn.Text = "停止服务";
                    bool ok;
                    ApplyHealth(CheckBackend(out ok), ok, CheckFrontend());
                });
            }
        }

        /// <summary>同步版，供窗口关闭时调用（那时不能等异步任务）。</summary>
        private void StopAllCore()
        {
            bool stopped = false;
            stopped |= KillTracked(ref backendProc, "后端");
            stopped |= KillTracked(ref frontendProc, "前端");

            // 本实例没拉起过进程时，按端口兜底。判据用「端口是否在监听」而不是健康检查：
            // 服务卡死、数据库断了也会让 /api/health 失败，但那时更需要能把它停掉。
            if (!stopped)
            {
                if (FindListeningPid(backendPort) > 0) stopped |= KillByPort(backendPort, "后端");
                if (FindListeningPid(frontendPort) > 0) stopped |= KillByPort(frontendPort, "前端");
            }

            if (!stopped)
                Log("没有正在运行的服务需要停止。");

            if (dbMode == "portable")
            {
                if (!RunPortableDb("stop", true))
                    Log("⚠ 内置数据库停止失败，请查看 data\\postgresql.log。");
                else
                    Log("内置数据库已停止。");
            }
        }

        // —— 子进程环境注入（2026-10-25 审查 P0-1）——
        //
        // 父进程环境块同时含 "Path" 与 "path"（大小写重复，多半是某个安装器留下
        // 的）时，.NET Framework 的 ProcessStartInfo.EnvironmentVariables 惰性字典
        // 在**第一次访问**就抛 ArgumentException（"已添加项…"），见 launcher-crash.log
        // ——崩溃点是 get_EnvironmentVariables() 本身，读写都会炸。启动器是非技术
        // 用户的唯一入口，这里必须兜底：注入失败只降级（子进程继承当前环境，缺
        // JIANJI_* 时后端有 --host 兜底推断，LAN 开关不生效而已）并写日志，
        // 绝不让启动器崩掉。

        private void SetChildEnv(ProcessStartInfo psi, string name, string value)
        {
            try
            {
                psi.EnvironmentVariables[name] = value;
            }
            catch (Exception ex)
            {
                Log("⚠ 无法注入子进程环境变量 " + name + "：" + ex.Message +
                    "。本次启动将缺少该变量（局域网开关等可能不生效），服务仍会尝试启动。");
            }
        }

        private string GetChildEnv(ProcessStartInfo psi, string name)
        {
            try
            {
                return psi.EnvironmentVariables[name];
            }
            catch (Exception ex)
            {
                Log("⚠ 无法读取子进程环境变量 " + name + "：" + ex.Message);
                return Environment.GetEnvironmentVariable(name);
            }
        }

        // —— 按端口兜底停服务的进程名白名单（2026-10-25 审查 P1-4）——
        //
        // 端口来自启动器配置，用户可能填成 3389/445 等系统端口；「停止服务」
        // 又是 taskkill /T /F 强杀整棵进程树。杀之前核对进程名：只杀 python /
        // node 这两类我们的服务可能用的进程，别的只记日志不动手。

        private static readonly string[] KillableProcessNames =
            { "python", "pythonw", "py", "node" };

        private static bool IsKillableServiceProcess(string processName)
        {
            foreach (string allowed in KillableProcessNames)
            {
                if (String.Equals(processName, allowed, StringComparison.OrdinalIgnoreCase))
                    return true;
            }
            return false;
        }

        /// <summary>
        /// 结束占用指定端口的监听进程。端口来自启动器自己的配置，且日志会记下 PID 与进程名，
        /// 便于事后核对停掉的是哪个进程。进程名不在白名单（python/node）内的不杀——
        /// 端口被别的程序占用时，强杀会误伤无关进程（如远程桌面、文件共享）。
        /// </summary>
        private bool KillByPort(int port, string name)
        {
            int pid = FindListeningPid(port);
            if (pid <= 0)
            {
                Log("端口 " + port + " 上没有找到监听进程。");
                return false;
            }
            if (pid == Process.GetCurrentProcess().Id)
            {
                Log("端口 " + port + " 由启动器自身占用，跳过。");
                return false;
            }
            string who = "";
            try { who = Process.GetProcessById(pid).ProcessName; }
            catch { }
            if (!IsKillableServiceProcess(who))
            {
                Log("端口 " + port + " 上的进程「" + (who.Length > 0 ? who : "未知") +
                    "」（PID " + pid + "）不是 python/node 服务，拒绝强杀。"
                    + "如果这确实是你自己启动的服务，请用任务管理器手动处理。");
                return false;
            }
            Log("正在停止" + name + "（端口 " + port + "，PID " + pid +
                (who.Length > 0 ? "，" + who : "") + "）…");
            bool ok = Taskkill(pid);
            if (ok) Log(name + "已停止。");
            else Log("停止" + name + "失败，可能已被其它程序结束。");
            return ok;
        }

        private static int FindListeningPid(int port)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo("netstat", "-ano -p TCP");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                using (Process p = Process.Start(psi))
                {
                    string output = p.StandardOutput.ReadToEnd();
                    p.WaitForExit(5000);
                    string suffix = ":" + port;
                    foreach (string raw in output.Split('\n'))
                    {
                        string line = raw.Trim();
                        if (line.Length == 0) continue;
                        if (line.IndexOf("LISTENING", StringComparison.OrdinalIgnoreCase) < 0) continue;
                        string[] parts = Regex.Split(line, "\\s+");
                        if (parts.Length < 5) continue;
                        if (!parts[1].EndsWith(suffix, StringComparison.Ordinal)) continue;
                        int pid;
                        if (Int32.TryParse(parts[parts.Length - 1], out pid)) return pid;
                    }
                }
            }
            catch { }
            return 0;
        }

        private static bool Taskkill(int pid)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo("taskkill", "/PID " + pid + " /T /F");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                using (Process killer = Process.Start(psi))
                {
                    killer.WaitForExit(8000);
                    return killer.ExitCode == 0;
                }
            }
            catch
            {
                return false;
            }
        }

        private bool KillTracked(ref Process proc, string name)
        {
            if (proc == null) return false;
            try
            {
                if (proc.HasExited)
                {
                    proc.Dispose();
                    proc = null;
                    return false;
                }
                int pid = proc.Id;
                Log("正在停止" + name + "…");
                proc.Dispose();
                proc = null;
                if (!Taskkill(pid))
                {
                    Log("停止" + name + "失败，可能已被其它程序结束。");
                    return false;
                }
                Log(name + "已停止。");
                return true;
            }
            catch (Exception ex)
            {
                Log("停止" + name + "失败：" + ex.Message);
                return false;
            }
        }

        private bool CheckBackend(out bool dbOk)
        {
            dbOk = false;
            actualDbLabel = "";
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(BackendUrl + "/api/health");
                req.Proxy = null;
                req.KeepAlive = false;
                req.Timeout = 1500;
                req.ReadWriteTimeout = 1500;
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (StreamReader reader = new StreamReader(resp.GetResponseStream()))
                {
                    string body = reader.ReadToEnd();
                    dbOk = body.IndexOf("\"db\":true", StringComparison.OrdinalIgnoreCase) >= 0
                        || body.IndexOf("\"db\": true", StringComparison.OrdinalIgnoreCase) >= 0;
                    actualDbLabel = LauncherConfig.HealthDbLabel(body);
                    // 库结构是否落后于代码（2026-10-26 真机事故：代码升了、库没跑迁移，
                    // 只有碰新列的接口 500）。老后端没有该字段 → 视为正常。
                    schemaOk = LauncherConfig.HealthSchemaOk(body);
                    schemaHead = LauncherConfig.HealthSchemaHead(body);
                    return resp.StatusCode == HttpStatusCode.OK
                        && body.IndexOf("\"status\":\"ok\"", StringComparison.OrdinalIgnoreCase) >= 0;
                }
            }
            catch
            {
                return false;
            }
        }

        private bool CheckFrontend()
        {
            if (SinglePort)
            {
                // 单端口形态下静态资源由后端出，「前端在不在」等于「后端在不在」
                bool dbOk;
                return CheckBackend(out dbOk);
            }
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(FrontendUrl + "/");
                req.Proxy = null;
                req.KeepAlive = false;
                req.Timeout = 1200;
                req.ReadWriteTimeout = 1200;
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                {
                    return resp.StatusCode == HttpStatusCode.OK;
                }
            }
            catch
            {
                return false;
            }
        }

        /// <summary>
        /// 端口是否空闲。按 <c>IPAddress.Any</c> 探测：局域网形态下后端绑的是 0.0.0.0，
        /// 只探回环会漏掉「别的程序绑在通配地址上」这种情况。
        /// </summary>
        private static bool IsPortFree(int port)
        {
            try
            {
                TcpListener listener = new TcpListener(IPAddress.Any, port);
                listener.Start();
                listener.Stop();
                return true;
            }
            catch { return false; }
        }

        /// <summary>
        /// 占用端口的进程（「名字（PID 1234）」）。拿不到就返回空串——
        /// 诊断信息是加分项，不能因为 netstat 被安全软件拦了就把启动卡死。
        /// </summary>
        private static string PortOwnerLabel(int port)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "netstat.exe";
                psi.Arguments = "-ano -p TCP";
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                // netstat 输出是系统 ANSI（中文 Windows 为 GBK）；按 UTF-8 解会乱码，
                // 进程名本身是 ASCII，用 Default 才两头都对。
                psi.StandardOutputEncoding = Encoding.Default;
                using (Process p = Process.Start(psi))
                {
                    string output = p.StandardOutput.ReadToEnd();
                    p.WaitForExit(3000);
                    string suffix = ":" + port;
                    string[] lines = output.Split(new string[] { "\r\n", "\n" },
                                                  StringSplitOptions.RemoveEmptyEntries);
                    foreach (string line in lines)
                    {
                        if (line.IndexOf("LISTENING", StringComparison.OrdinalIgnoreCase) < 0)
                            continue;
                        string[] cols = line.Split(new char[] { ' ', '\t' },
                                                   StringSplitOptions.RemoveEmptyEntries);
                        if (cols.Length < 5) continue;
                        if (!cols[1].EndsWith(suffix, StringComparison.Ordinal)) continue;
                        int pid;
                        if (!int.TryParse(cols[4], out pid)) continue;
                        try
                        {
                            using (Process owner = Process.GetProcessById(pid))
                                return owner.ProcessName + "（PID " + pid + "）";
                        }
                        catch { return "PID " + pid; }
                    }
                }
            }
            catch { }
            return "";
        }

        private PythonCommand FindPortablePython()
        {
            string path = Path.Combine(root, "runtime", "python", "python.exe");
            if (!File.Exists(path))
            {
                Log("内置 Python 运行环境缺失：" + path);
                return null;
            }
            PythonCommand cmd = new PythonCommand(path, "", path);
            return CanImport(cmd) ? cmd : null;
        }

        private PythonCommand PythonForMode()
        {
            return dbMode == "portable" ? FindPortablePython() : FindPython();
        }

        private static int PickFreePort(int preferred)
        {
            for (int port = preferred; port < preferred + 50; port++)
            {
                try
                {
                    TcpListener listener = new TcpListener(IPAddress.Loopback, port);
                    listener.Start();
                    listener.Stop();
                    return port;
                }
                catch { }
            }
            throw new InvalidOperationException("找不到可用的内置数据库端口");
        }

        private PythonCommand FindPython()
        {
            string home = Environment.GetEnvironmentVariable("USERPROFILE");
            string[] paths = new string[]
            {
                Path.Combine(root, "runtime", "python", "python.exe"),
                Path.Combine(root, "backend", ".venv", "Scripts", "python.exe"),
                Path.Combine(root, ".venv", "Scripts", "python.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python", "Python313", "python.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python", "Python312", "python.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python", "Python311", "python.exe")
            };
            foreach (string path in paths)
            {
                if (File.Exists(path))
                {
                    PythonCommand cmd = new PythonCommand(path, "", path);
                    if (CanImport(cmd)) return cmd;
                }
            }
            string onPath = FindOnPath("python.exe");
            if (onPath != null)
            {
                PythonCommand cmd = new PythonCommand(onPath, "", onPath);
                if (CanImport(cmd)) return cmd;
            }
            string py = FindOnPath("py.exe");
            if (py != null)
            {
                PythonCommand cmd = new PythonCommand(py, "-3 ", py + " -3");
                if (CanImport(cmd)) return cmd;
            }
            return null;
        }

        private bool CanImport(PythonCommand cmd)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = cmd.FileName;
                psi.Arguments = cmd.Prefix + "-c \"import fastapi, uvicorn\"";
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                Process p = Process.Start(psi);
                bool ok = p.WaitForExit(6000) && p.ExitCode == 0;
                if (!p.HasExited) p.Kill();
                p.Dispose();
                return ok;
            }
            catch { return false; }
        }

        private string FindNode()
        {
            string home = Environment.GetEnvironmentVariable("USERPROFILE");
            string[] paths = new string[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "nodejs", "node.exe")
            };
            foreach (string path in paths)
                if (File.Exists(path)) return path;
            return FindOnPath("node.exe");
        }

        private string FindNpm(string nodePath)
        {
            string dir = Path.GetDirectoryName(nodePath);
            string npm = Path.Combine(dir, "npm.cmd");
            if (File.Exists(npm)) return npm;
            return FindOnPath("npm.cmd");
        }

        private static string FindOnPath(string file)
        {
            string path = Environment.GetEnvironmentVariable("PATH");
            if (String.IsNullOrEmpty(path)) return null;
            string[] parts = path.Split(';');
            foreach (string part in parts)
            {
                try
                {
                    string p = Path.Combine(part.Trim(), file);
                    if (File.Exists(p)) return p;
                }
                catch { }
            }
            return null;
        }

        private void OpenBrowser()
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(WebUrl);
                psi.UseShellExecute = true;
                Process.Start(psi);
            }
            catch (Exception ex)
            {
                Log("打开浏览器失败：" + ex.Message);
            }
        }

        private void OpenLogs()
        {
            try
            {
                Directory.CreateDirectory(logsDir);
                Process.Start("explorer.exe", "\"" + logsDir + "\"");
            }
            catch (Exception ex)
            {
                Log("打开日志目录失败：" + ex.Message);
            }
        }

        private void ShowError(string message)
        {
            Ui(delegate
            {
                MessageBox.Show(this, message, "简籍启动器",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            });
        }

        private void OnFormClosing(object sender, FormClosingEventArgs e)
        {
            bool alive = (backendProc != null && !backendProc.HasExited)
                || (frontendProc != null && !frontendProc.HasExited);
            if (!alive) return;

            // 默认按钮必须是「否」：这个框一冒出来往往是用户只想关窗口，顺手一个回车
            // 就把服务停了（学生正在借还书）。"停止服务"要主动选，不能让默认动作替他决定。
            DialogResult result = MessageBox.Show(this,
                "服务仍在运行。\n\n是：停止服务并退出（正在办理外借或归还的窗口会中断）\n"
                + "否：保持运行并退出（默认，服务继续跑）\n取消：回到启动器",
                "退出 简籍启动器",
                MessageBoxButtons.YesNoCancel, MessageBoxIcon.Question,
                MessageBoxDefaultButton.Button2);
            if (result == DialogResult.Cancel)
            {
                e.Cancel = true;
                return;
            }
            if (result == DialogResult.Yes)
                StopAllCore();
        }

        /// <summary>
        /// 现有数据库选择器。列表来自服务器而不是本地缓存，避免把已删除的库
        /// 继续摆在界面上；双击或回车直接使用。
        /// </summary>
        private sealed class DbPickerForm : Form
        {
            private readonly ListBox listBox;

            public string SelectedDatabase { get; private set; }

            public DbPickerForm(List<string> databases, string current)
            {
                Text = "选择数据库";
                FormBorderStyle = FormBorderStyle.None;
                StartPosition = FormStartPosition.CenterParent;
                ClientSize = new Size(420, 392);
                BackColor = Palette.BgTop;
                ShowInTaskbar = false;
                KeyPreview = true;
                Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular, GraphicsUnit.Point);

                RoundedPanel card = new RoundedPanel();
                card.Location = new Point(12, 12);
                card.Size = new Size(396, 368);
                card.Radius = 16;
                card.FillColor = Palette.Surface;
                card.BorderColor = Palette.Line;
                card.BackColor = Palette.BgTop;
                Controls.Add(card);

                Label title = new Label();
                title.Text = "选择数据库";
                title.Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Bold, GraphicsUnit.Point);
                title.ForeColor = Palette.Ink;
                title.BackColor = Palette.Surface;
                title.AutoSize = true;
                title.Location = new Point(24, 18);
                card.Controls.Add(title);

                Label hint = new Label();
                hint.Text = "双击数据库名直接使用";
                hint.Font = new Font("Microsoft YaHei UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point);
                hint.ForeColor = Palette.Muted;
                hint.BackColor = Palette.Surface;
                hint.AutoSize = true;
                hint.Location = new Point(24, 46);
                card.Controls.Add(hint);

                Label close = new Label();
                close.Text = "×";
                close.Font = new Font("Segoe UI", 11F, FontStyle.Regular, GraphicsUnit.Point);
                close.ForeColor = Palette.Muted;
                close.BackColor = Palette.Surface;
                close.TextAlign = ContentAlignment.MiddleCenter;
                close.Size = new Size(28, 28);
                close.Location = new Point(344, 10);
                close.Cursor = Cursors.Hand;
                close.Click += delegate { Close(); };
                close.MouseEnter += delegate
                {
                    close.BackColor = Palette.SurfaceSoft;
                    close.ForeColor = Palette.Ink;
                };
                close.MouseLeave += delegate
                {
                    close.BackColor = Palette.Surface;
                    close.ForeColor = Palette.Muted;
                };
                card.Controls.Add(close);

                listBox = new ListBox();
                listBox.Location = new Point(24, 78);
                listBox.Size = new Size(348, 214);
                listBox.BorderStyle = BorderStyle.None;
                listBox.DrawMode = DrawMode.OwnerDrawFixed;
                listBox.ItemHeight = 32;
                listBox.IntegralHeight = false;
                listBox.BackColor = Palette.Surface;
                listBox.ForeColor = Palette.Text;
                listBox.Font = new Font("Microsoft YaHei UI", 10F, FontStyle.Regular, GraphicsUnit.Point);
                listBox.DrawItem += DrawItem;
                listBox.DoubleClick += delegate { AcceptSelection(); };
                listBox.KeyDown += delegate(object s, KeyEventArgs e)
                {
                    if (e.KeyCode == Keys.Enter)
                    {
                        AcceptSelection();
                        e.Handled = true;
                    }
                };
                foreach (string db in databases) listBox.Items.Add(db);
                int index = String.IsNullOrEmpty(current) ? -1 : listBox.Items.IndexOf(current);
                if (index >= 0) listBox.SelectedIndex = index;
                else if (listBox.Items.Count > 0) listBox.SelectedIndex = 0;
                card.Controls.Add(listBox);

                RoundedButton cancel = new RoundedButton();
                cancel.Text = "取消";
                cancel.Location = new Point(192, 312);
                cancel.Size = new Size(84, 36);
                cancel.Radius = 8;
                cancel.BackColor = Palette.Surface;
                cancel.DialogResult = DialogResult.Cancel;
                card.Controls.Add(cancel);

                RoundedButton use = new RoundedButton();
                use.Primary = true;
                use.Text = "使用";
                use.Location = new Point(284, 312);
                use.Size = new Size(84, 36);
                use.Radius = 8;
                use.BackColor = Palette.Surface;
                use.Click += delegate { AcceptSelection(); };
                card.Controls.Add(use);

                AcceptButton = use;
                CancelButton = cancel;

                MouseEventHandler drag = delegate(object s, MouseEventArgs e)
                {
                    if (e.Button != MouseButtons.Left) return;
                    ReleaseCapture();
                    SendMessage(Handle, 0xA1, (IntPtr)0x2, IntPtr.Zero);
                };
                card.MouseDown += drag;
                title.MouseDown += drag;
                hint.MouseDown += drag;
            }

            private void AcceptSelection()
            {
                if (listBox.SelectedItem == null) return;
                SelectedDatabase = Convert.ToString(listBox.SelectedItem);
                DialogResult = DialogResult.OK;
                Close();
            }

            private void DrawItem(object sender, DrawItemEventArgs e)
            {
                if (e.Index < 0) return;
                bool selected = (e.State & DrawItemState.Selected) != 0;
                Rectangle bounds = e.Bounds;
                using (SolidBrush background = new SolidBrush(
                    selected ? Palette.PrimarySoft : Palette.Surface))
                    e.Graphics.FillRectangle(background, bounds);
                using (Pen line = new Pen(Palette.Line))
                    e.Graphics.DrawLine(line, bounds.Left + 10, bounds.Bottom - 1,
                        bounds.Right - 10, bounds.Bottom - 1);
                Color fore = selected ? Palette.Primary
                    : Palette.Ink;
                Rectangle text = new Rectangle(bounds.X + 14, bounds.Y,
                    bounds.Width - 40, bounds.Height);
                TextRenderer.DrawText(e.Graphics, Convert.ToString(listBox.Items[e.Index]),
                    listBox.Font, text, fore, TextFormatFlags.VerticalCenter |
                    TextFormatFlags.Left | TextFormatFlags.EndEllipsis);
                if (selected)
                {
                    using (SolidBrush dot = new SolidBrush(Palette.Primary))
                        e.Graphics.FillEllipse(dot, bounds.Right - 26,
                            bounds.Y + bounds.Height / 2 - 3, 6, 6);
                }
            }

            protected override void OnHandleCreated(EventArgs e)
            {
                base.OnHandleCreated(e);
                try
                {
                    int preference = 2;
                    DwmSetWindowAttribute(Handle, 33, ref preference, sizeof(int));
                }
                catch { }
            }
        }

        private sealed class PythonCommand
        {
            public readonly string FileName;
            public readonly string Prefix;
            public readonly string Display;

            public PythonCommand(string fileName, string prefix, string display)
            {
                FileName = fileName;
                Prefix = prefix;
                Display = display;
            }
        }
    }
}
