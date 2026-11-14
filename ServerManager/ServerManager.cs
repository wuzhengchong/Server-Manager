// 爱沃服务器管理工具（托盘 GUI）
// 目标：.NET Framework 4.x（C# 5，使用系统自带 csc.exe 编译，无需安装任何东西）
// 功能：托盘状态显示、启停重启、进程/端口监控、操作日志查看、快捷打开页面、开机自启

using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Management;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;

namespace AiwoServerManager
{
    // ---------------- 程序入口 ----------------

    public static class Program
    {
        [STAThread]
        public static void Main()
        {
            bool createdNew;
            using (var mutex = new Mutex(true, "AiwoServerManager.SingleInstance", out createdNew))
            {
                if (!createdNew)
                {
                    MessageBox.Show("爱沃服务器管理工具已在运行（请查看系统托盘图标）。", "提示",
                        MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
                Application.ThreadException += delegate(object s, ThreadExceptionEventArgs e)
                {
                    LogCrash("UI线程异常", e.Exception);
                };
                AppDomain.CurrentDomain.UnhandledException +=
                    delegate(object s, UnhandledExceptionEventArgs e)
                    {
                        LogCrash("后台线程异常", e.ExceptionObject as Exception);
                    };
                // 首次启动需登录：使用 __remote 的账号 / 密码 / 双重验证码
                AppConfig cfg = AppConfig.Load();
                if (!cfg.LoginOk)
                {
                    using (var login = new LoginForm(cfg))
                    {
                        if (login.ShowDialog() != DialogResult.OK) return;
                    }
                }
                Application.Run(new MainForm());
            }
        }

        private static void LogCrash(string where, Exception ex)
        {
            try
            {
                string line = DateTime.Now.ToString("MM-dd HH:mm:ss") + "  [崩溃] " + where
                    + ": " + (ex == null ? "(未知)" : ex.ToString()) + Environment.NewLine;
                File.AppendAllText(AppConfig.LogFile(), line, Encoding.UTF8);
            }
            catch { }
        }
    }

    // ---------------- 配置 ----------------

    public class AppConfig
    {
        public string TaskName = "AiwoServer";
        public string ScriptPath = "d:\\server\\server1.1\\server.py";
        public string PythonwPath = "C:\\Users\\wzc\\AppData\\Local\\Programs\\Python\\Python313\\pythonw.exe";
        public int LocalPort = 80;
        public bool LoginOk = false;   // 是否已完成管理器登录（首次启动需登录）

        private static string Dir()
        {
            string d = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "AiwoServerManager");
            if (!Directory.Exists(d)) Directory.CreateDirectory(d);
            return d;
        }

        public static string LogFile()
        {
            return Path.Combine(Dir(), "manager.log");
        }

        public static AppConfig Load()
        {
            var c = new AppConfig();
            try
            {
                string p = Path.Combine(Dir(), "config.txt");
                if (!File.Exists(p)) return c;
                foreach (string line in File.ReadAllLines(p, Encoding.UTF8))
                {
                    int i = line.IndexOf('=');
                    if (i <= 0) continue;
                    string k = line.Substring(0, i).Trim();
                    string v = line.Substring(i + 1).Trim();
                    switch (k)
                    {
                        case "TaskName": c.TaskName = v; break;
                        case "ScriptPath": c.ScriptPath = v; break;
                        case "PythonwPath": c.PythonwPath = v; break;
                        case "LocalPort":
                            {
                                int port;
                                if (int.TryParse(v, out port) && port > 0 && port < 65536) c.LocalPort = port;
                                break;
                            }
                        case "LoginOk": c.LoginOk = (v == "1"); break;
                    }
                }
            }
            catch { }
            return c;
        }

        public void Save()
        {
            try
            {
                var sb = new StringBuilder();
                sb.AppendLine("TaskName=" + TaskName);
                sb.AppendLine("ScriptPath=" + ScriptPath);
                sb.AppendLine("PythonwPath=" + PythonwPath);
                sb.AppendLine("LocalPort=" + LocalPort);
                sb.AppendLine("LoginOk=" + (LoginOk ? "1" : "0"));
                File.WriteAllText(Path.Combine(Dir(), "config.txt"), sb.ToString(), Encoding.UTF8);
            }
            catch { }
        }

        /** 服务器操作日志目录（oplog.jsonl / seclog.jsonl 所在目录） */
        public string AssetsDir()
        {
            try
            {
                string baseDir = Path.GetDirectoryName(ScriptPath);
                if (string.IsNullOrEmpty(baseDir)) return null;
                return Path.Combine(baseDir, "asssssets");
            }
            catch { return null; }
        }
    }

    // ---------------- 服务器状态快照 ----------------

    public class ServerStatus
    {
        public bool ProcessFound;
        public int Pid;
        public double CpuPercent;
        public long MemBytes;
        public DateTime? StartTime;
        public bool PortOpen;
        public bool PortChangedByUs;   // 端口被其他进程占用（而非服务器）
        public string Error;

        public bool Healthy { get { return ProcessFound && PortOpen; } }
    }

    // ---------------- 主窗体 ----------------

    public partial class MainForm : Form
    {
        private readonly AppConfig cfg;
        private readonly NotifyIcon tray;
        private readonly Icon iconGreen;
        private readonly Icon iconRed;
        private readonly Icon iconGray;
        private readonly ContextMenuStrip trayMenu;

        private readonly System.Windows.Forms.Timer pollTimer;          // 状态轮询（2 秒）
        private readonly System.Windows.Forms.Timer uiLogTimer;         // 界面日志刷新

        // 状态展示
        private Label lblState;
        private Label lblPid;
        private Label lblPort;
        private Label lblCpu;
        private Label lblMem;
        private Label lblUptime;
        private Panel statusPanel;

        // 操作按钮
        private Button btnStart;
        private Button btnStop;
        private Button btnRestart;
        private Button btnOpenSms;
        private Button btnOpenRemote;
        private Button btnOpenHome;
        private Button btnOpenLogDir;

        // 日志
        private ComboBox cboLog;
        private Button btnRefreshLog;
        private TextBox serverLogBox;
        private TextBox toolLogBox;
        private TabControl tabs;

        // 设置
        private TextBox txtTaskName;
        private TextBox txtScriptPath;
        private TextBox txtPort;
        private CheckBox chkAutoStart;
        private Button btnSaveCfg;

        // 进程监控缓存
        private int cachedPid = -1;
        private TimeSpan lastCpuTime;
        private DateTime lastCpuTick;
        private int lastState;                 // 0=未知 1=运行 2=停止（用于托盘气泡通知）
        private bool realExit;                 // 托盘"退出"标记
        private bool balloonShownOnce;

        public MainForm()
        {
            cfg = AppConfig.Load();

            iconGreen = MakeIcon(Color.FromArgb(46, 204, 113));
            iconRed = MakeIcon(Color.FromArgb(231, 76, 60));
            iconGray = MakeIcon(Color.FromArgb(127, 140, 141));

            Text = "爱沃服务器管理工具";
            Icon = iconGreen;
            FormSizeMode();

            // ---------- 托盘 ----------
            trayMenu = BuildTrayMenu();
            tray = new NotifyIcon();
            tray.Icon = iconGray;
            tray.Text = "爱沃服务器管理工具";
            tray.ContextMenuStrip = trayMenu;
            tray.Visible = true;
            tray.DoubleClick += delegate { ShowWindow(); };

            // ---------- 布局 ----------
            BuildUi();

            // ---------- 定时器 ----------
            pollTimer = new System.Windows.Forms.Timer();
            pollTimer.Interval = 2000;
            pollTimer.Tick += delegate { PollOnce(); };
            pollTimer.Start();

            uiLogTimer = new System.Windows.Forms.Timer();
            uiLogTimer.Interval = 3000;
            uiLogTimer.Tick += delegate { AppendPendingToolLog(); };
            uiLogTimer.Start();

            InitDashboard();
            InitAccounts();

            Load += delegate
            {
                PollOnce();
                RefreshServerLog();
                RefreshAccounts();
                RefreshAutoStartCheck();
                if (!balloonShownOnce)
                {
                    balloonShownOnce = true;
                    tray.ShowBalloonTip(3000, "爱沃服务器管理工具",
                        "已启动，已在系统托盘运行。双击图标打开主界面。", ToolTipIcon.Info);
                }
            };
            FormClosing += delegate(object s, FormClosingEventArgs e)
            {
                if (!realExit && e.CloseReason == CloseReason.UserClosing)
                {
                    e.Cancel = true;
                    Hide();
                    return;
                }
                if (realExit)
                {
                    tray.Visible = false;
                }
            };
        }

        private void FormSizeMode()
        {
            StartPosition = FormStartPosition.CenterScreen;
            Size = new Size(780, 700);
            MinimumSize = new Size(720, 640);
            MaximizeBox = true;
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                try
                {
                    pollTimer.Stop();
                    uiLogTimer.Stop();
                    tray.Visible = false;
                    tray.Dispose();
                }
                catch { }
            }
            base.Dispose(disposing);
        }

        // ---------------- 托盘菜单 ----------------

        private ContextMenuStrip BuildTrayMenu()
        {
            var menu = new ContextMenuStrip();
            menu.Items.Add("打开主界面", null, delegate { ShowWindow(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("启动服务器", null, delegate { DoStartSafe(); });
            menu.Items.Add("停止服务器", null, delegate { DoStopSafe(); });
            menu.Items.Add("重启服务器", null, delegate { DoRestartSafe(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("打开短信页", null, delegate { OpenRemote("/__remote/sms"); });
            menu.Items.Add("打开远程控制", null, delegate { OpenRemote("/__remote"); });
            menu.Items.Add("打开主页", null, delegate { OpenUrl("/"); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("退出", null, delegate
            {
                realExit = true;
                Application.Exit();
            });
            return menu;
        }

        // ---------------- 主界面布局 ----------------

        private void BuildUi()
        {
            // ===== 状态面板 =====
            statusPanel = new Panel();
            statusPanel.Dock = DockStyle.Top;
            statusPanel.Height = 128;
            statusPanel.Padding = new Padding(12, 10, 12, 6);

            lblState = MakeStatusLabel(Color.FromArgb(192, 57, 43), 16f);
            lblState.Location = new Point(12, 10);

            lblPid = MakeStatusLabel(Color.FromArgb(60, 60, 60), 10f);
            lblPid.Location = new Point(14, 48);

            lblPort = MakeStatusLabel(Color.FromArgb(60, 60, 60), 10f);
            lblPort.Location = new Point(14, 70);

            lblCpu = MakeStatusLabel(Color.FromArgb(60, 60, 60), 10f);
            lblCpu.Location = new Point(300, 48);

            lblMem = MakeStatusLabel(Color.FromArgb(60, 60, 60), 10f);
            lblMem.Location = new Point(300, 70);

            lblUptime = MakeStatusLabel(Color.FromArgb(60, 60, 60), 10f);
            lblUptime.Location = new Point(14, 92);

            statusPanel.Controls.Add(lblState);
            statusPanel.Controls.Add(lblPid);
            statusPanel.Controls.Add(lblPort);
            statusPanel.Controls.Add(lblCpu);
            statusPanel.Controls.Add(lblMem);
            statusPanel.Controls.Add(lblUptime);

            // ===== 操作按钮 =====
            var opPanel = new FlowLayoutPanel();
            opPanel.Dock = DockStyle.Top;
            opPanel.Height = 74;
            opPanel.Padding = new Padding(10, 4, 10, 4);
            opPanel.FlowDirection = FlowDirection.LeftToRight;

            btnStart = MakeOpButton("启动", Color.FromArgb(46, 204, 113));
            btnStop = MakeOpButton("停止", Color.FromArgb(231, 76, 60));
            btnRestart = MakeOpButton("重启", Color.FromArgb(52, 152, 219));
            btnOpenSms = MakeOpButton("短信页", Color.FromArgb(155, 89, 182));
            btnOpenRemote = MakeOpButton("远程控制", Color.FromArgb(155, 89, 182));
            btnOpenHome = MakeOpButton("主页", Color.FromArgb(155, 89, 182));
            btnOpenLogDir = MakeOpButton("日志目录", Color.FromArgb(149, 165, 166));

            btnStart.Click += delegate { DoStartSafe(); };
            btnStop.Click += delegate { DoStopSafe(); };
            btnRestart.Click += delegate { DoRestartSafe(); };
            btnOpenSms.Click += delegate { OpenRemote("/__remote/sms"); };
            btnOpenRemote.Click += delegate { OpenRemote("/__remote"); };
            btnOpenHome.Click += delegate { OpenUrl("/"); };
            btnOpenLogDir.Click += delegate { OpenLogDir(); };

            opPanel.Controls.Add(btnStart);
            opPanel.Controls.Add(btnStop);
            opPanel.Controls.Add(btnRestart);
            opPanel.Controls.Add(btnOpenSms);
            opPanel.Controls.Add(btnOpenRemote);
            opPanel.Controls.Add(btnOpenHome);
            opPanel.Controls.Add(btnOpenLogDir);

            // ===== 日志标签页 =====
            tabs = new TabControl();
            tabs.Dock = DockStyle.Fill;

            var tabDash = new TabPage("仪表盘");
            BuildDashboardTab(tabDash);

            var tabAcc = new TabPage("账号管理");
            BuildAccountsTab(tabAcc);

            var tabServer = new TabPage("服务器日志");
            var topBar = new FlowLayoutPanel();
            topBar.Dock = DockStyle.Top;
            topBar.Height = 34;
            topBar.Padding = new Padding(8, 5, 8, 0);
            cboLog = new ComboBox();
            cboLog.DropDownStyle = ComboBoxStyle.DropDownList;
            cboLog.Width = 150;
            cboLog.Items.Add("操作日志 (oplog)");
            cboLog.Items.Add("安全日志 (seclog)");
            cboLog.SelectedIndex = 0;
            btnRefreshLog = new Button();
            btnRefreshLog.Text = "刷新";
            btnRefreshLog.Width = 70;
            btnRefreshLog.Click += delegate { RefreshServerLog(); };
            topBar.Controls.Add(cboLog);
            topBar.Controls.Add(btnRefreshLog);

            serverLogBox = MakeLogBox();
            serverLogBox.Dock = DockStyle.Fill;
            tabServer.Controls.Add(serverLogBox);
            tabServer.Controls.Add(topBar);

            var tabTool = new TabPage("工具日志");
            toolLogBox = MakeLogBox();
            toolLogBox.Dock = DockStyle.Fill;
            tabTool.Controls.Add(toolLogBox);

            var tabCfg = new TabPage("设置");
            BuildCfgTab(tabCfg);

            tabs.TabPages.Add(tabDash);
            tabs.TabPages.Add(tabAcc);
            tabs.TabPages.Add(tabServer);
            tabs.TabPages.Add(tabTool);
            tabs.TabPages.Add(tabCfg);

            Controls.Add(tabs);
            Controls.Add(opPanel);
            Controls.Add(statusPanel);

            cboLog.SelectedIndexChanged += delegate { RefreshServerLog(); };
        }

        private void BuildCfgTab(TabPage page)
        {
            var panel = new TableLayoutPanel();
            panel.Dock = DockStyle.Fill;
            panel.Padding = new Padding(16);
            panel.ColumnCount = 2;
            panel.RowCount = 6;
            panel.AutoSize = false;

            var lbl1 = new Label();
            lbl1.Text = "计划任务名：";
            lbl1.AutoSize = true;
            lbl1.TextAlign = ContentAlignment.MiddleRight;
            txtTaskName = new TextBox();
            txtTaskName.Text = cfg.TaskName;
            txtTaskName.Dock = DockStyle.Fill;

            var lbl2 = new Label();
            lbl2.Text = "服务器脚本：";
            lbl2.AutoSize = true;
            lbl2.TextAlign = ContentAlignment.MiddleRight;
            txtScriptPath = new TextBox();
            txtScriptPath.Text = cfg.ScriptPath;
            txtScriptPath.Dock = DockStyle.Fill;

            var lbl3 = new Label();
            lbl3.Text = "本地端口：";
            lbl3.AutoSize = true;
            lbl3.TextAlign = ContentAlignment.MiddleRight;
            txtPort = new TextBox();
            txtPort.Text = cfg.LocalPort.ToString();
            txtPort.Dock = DockStyle.Fill;

            chkAutoStart = new CheckBox();
            chkAutoStart.Text = "开机自动运行本管理工具（当前用户登录时）";
            chkAutoStart.AutoSize = true;

            btnSaveCfg = new Button();
            btnSaveCfg.Text = "保存设置";
            btnSaveCfg.Width = 110;
            btnSaveCfg.Click += delegate { SaveConfig(); };

            panel.Controls.Add(lbl1, 0, 0);
            panel.Controls.Add(txtTaskName, 1, 0);
            panel.Controls.Add(lbl2, 0, 1);
            panel.Controls.Add(txtScriptPath, 1, 1);
            panel.Controls.Add(lbl3, 0, 2);
            panel.Controls.Add(txtPort, 1, 2);
            panel.Controls.Add(chkAutoStart, 1, 3);
            panel.Controls.Add(new Label(), 0, 4);
            panel.Controls.Add(btnSaveCfg, 1, 4);
            page.Controls.Add(panel);
        }

        private static Label MakeStatusLabel(Color color, float size)
        {
            var l = new Label();
            l.AutoSize = true;
            l.ForeColor = color;
            l.Font = new Font("Microsoft YaHei UI", size, FontStyle.Bold);
            return l;
        }

        private Button MakeOpButton(string text, Color back)
        {
            var b = new Button();
            b.Text = text;
            b.Width = 92;
            b.Height = 44;
            b.FlatStyle = FlatStyle.Flat;
            b.FlatAppearance.BorderSize = 0;
            b.BackColor = back;
            b.ForeColor = Color.White;
            b.Font = new Font("Microsoft YaHei UI", 10.5f, FontStyle.Bold);
            b.Cursor = Cursors.Hand;
            b.Margin = new Padding(6, 6, 0, 0);
            return b;
        }

        private static TextBox MakeLogBox()
        {
            var t = new TextBox();
            t.Multiline = true;
            t.ScrollBars = ScrollBars.Vertical;
            t.ReadOnly = true;
            t.BackColor = Color.FromArgb(16, 19, 26);
            t.ForeColor = Color.FromArgb(124, 224, 160);
            t.Font = new Font(FontFamily.GenericMonospace, 9f);
            t.WordWrap = false;
            return t;
        }

        // ---------------- 托盘图标绘制 ----------------

        private static Icon MakeIcon(Color core)
        {
            using (var bmp = new Bitmap(16, 16))
            {
                using (var g = Graphics.FromImage(bmp))
                {
                    g.SmoothingMode = SmoothingMode.AntiAlias;
                    using (var b = new SolidBrush(core)) g.FillEllipse(b, 2, 2, 12, 12);
                    using (var p = new Pen(Color.FromArgb(50, 50, 50))) g.DrawEllipse(p, 2, 2, 12, 12);
                }
                IntPtr h = bmp.GetHicon();
                return Icon.FromHandle(h);
            }
        }

        // ---------------- 状态轮询 ----------------

        private void PollOnce()
        {
            Task.Factory.StartNew(delegate
            {
                ServerStatus st = QueryStatus();
                BeginInvoke((MethodInvoker)delegate { ApplyStatus(st); });
            });
        }

        /** 查询服务器进程与端口状态（后台线程调用） */
        private ServerStatus QueryStatus()
        {
            var st = new ServerStatus();
            try
            {
                // 1) 找进程：优先用缓存 PID，失效后走 WMI 按命令行匹配
                Process proc = FindServerProcess();
                if (proc != null)
                {
                    st.ProcessFound = true;
                    st.Pid = proc.Id;
                    try
                    {
                        proc.Refresh();
                        st.MemBytes = proc.WorkingSet64;
                        if (cachedPid == proc.Id && lastCpuTick != DateTime.MinValue)
                        {
                            TimeSpan now = proc.TotalProcessorTime;
                            double wall = (DateTime.UtcNow - lastCpuTick).TotalSeconds;
                            if (wall > 0.5)
                            {
                                double cpu = (now - lastCpuTime).TotalSeconds / wall * 100.0
                                             / Environment.ProcessorCount;
                                st.CpuPercent = Math.Max(0, Math.Min(100, cpu));
                            }
                            lastCpuTime = now;
                            lastCpuTick = DateTime.UtcNow;
                        }
                        else
                        {
                            lastCpuTime = proc.TotalProcessorTime;
                            lastCpuTick = DateTime.UtcNow;
                        }
                        st.StartTime = proc.StartTime;
                    }
                    catch { }
                }
                else
                {
                    cachedPid = -1;
                    lastCpuTick = DateTime.MinValue;
                }

                // 2) 端口探测
                st.PortOpen = ProbePort(cfg.LocalPort, 600);
            }
            catch (Exception ex)
            {
                st.Error = ex.Message;
            }
            return st;
        }

        /** 查找 server.py 进程（先试缓存 PID，再 WMI 按命令行匹配） */
        private Process FindServerProcess()
        {
            if (cachedPid > 0)
            {
                try
                {
                    Process p = Process.GetProcessById(cachedPid);
                    if (!p.HasExited) return p;
                }
                catch { }
                cachedPid = -1;
            }
            try
            {
                string scriptName = Path.GetFileName(cfg.ScriptPath);   // server.py
                string filter = "SELECT ProcessId, CommandLine FROM Win32_Process";
                using (var searcher = new ManagementObjectSearcher(filter))
                using (ManagementObjectCollection results = searcher.Get())
                {
                    foreach (ManagementObject o in results)
                    {
                        try
                        {
                            if (Convert.ToInt32(o["ProcessId"]) == Process.GetCurrentProcess().Id) continue;
                            string cl = Convert.ToString(o["CommandLine"]);
                            if (string.IsNullOrEmpty(cl)) continue;
                            if (cl.IndexOf(scriptName, StringComparison.OrdinalIgnoreCase) < 0) continue;
                            if (cl.IndexOf(Path.GetDirectoryName(cfg.ScriptPath) ?? "server1.1",
                                    StringComparison.OrdinalIgnoreCase) < 0
                                && cl.IndexOf("server1.1", StringComparison.OrdinalIgnoreCase) < 0) continue;
                            cachedPid = Convert.ToInt32(o["ProcessId"]);
                            return Process.GetProcessById(cachedPid);
                        }
                        catch { }
                        finally
                        {
                            try { o.Dispose(); } catch { }
                        }
                    }
                }
            }
            catch { }
            return null;
        }

        private static bool ProbePort(int port, int timeoutMs)
        {
            try
            {
                using (var c = new TcpClient())
                {
                    var ar = c.BeginConnect("127.0.0.1", port, null, null);
                    if (ar.AsyncWaitHandle.WaitOne(timeoutMs))
                    {
                        c.EndConnect(ar);
                        return true;
                    }
                    c.Close();
                    return false;
                }
            }
            catch { return false; }
        }

        /** 应用状态到界面与托盘（UI 线程） */
        private void ApplyStatus(ServerStatus st)
        {
            bool running = st.ProcessFound;
            string stateText;
            Color stateColor;
            if (running && st.PortOpen)
            {
                stateText = "● 运行中";
                stateColor = Color.FromArgb(39, 174, 96);
            }
            else if (running)
            {
                stateText = "● 进程在，端口无响应";
                stateColor = Color.FromArgb(230, 126, 34);
            }
            else
            {
                stateText = "● 已停止";
                stateColor = Color.FromArgb(192, 57, 43);
            }
            lblState.Text = stateText;
            lblState.ForeColor = stateColor;

            lblPid.Text = running ? ("进程 PID：" + st.Pid) : "进程 PID：-";
            lblPort.Text = "端口 " + cfg.LocalPort + "：" + (st.PortOpen ? "监听正常 ✓" : "无响应 ✗");
            lblCpu.Text = running ? string.Format("CPU：{0:F1} %", st.CpuPercent) : "CPU：-";
            lblMem.Text = running ? ("内存：" + (st.MemBytes / 1024.0 / 1024.0).ToString("F1") + " MB") : "内存：-";
            if (running && st.StartTime.HasValue)
            {
                TimeSpan up = DateTime.Now - st.StartTime.Value;
                lblUptime.Text = "已运行：" + FmtDuration(up) + "（自 " + st.StartTime.Value.ToString("MM-dd HH:mm:ss") + "）";
            }
            else
            {
                lblUptime.Text = "已运行：-";
            }

            // 托盘
            tray.Icon = running ? iconGreen : iconRed;
            tray.Text = Truncate("爱沃服务器：" + (running ? "运行中 (PID " + st.Pid + ")" : "已停止"), 60);

            // 状态变化气泡通知
            int nowState = running ? 1 : 2;
            if (lastState != 0 && lastState != nowState)
            {
                tray.ShowBalloonTip(2500, "爱沃服务器状态变更",
                    nowState == 1 ? "服务器已启动" : "服务器已停止", ToolTipIcon.Info);
            }
            lastState = nowState;
        }

        private static string FmtDuration(TimeSpan t)
        {
            if (t.TotalDays >= 1)
                return string.Format("{0}天 {1}小时{2}分", (int)t.TotalDays, t.Hours, t.Minutes);
            if (t.TotalHours >= 1)
                return string.Format("{0}小时{1}分{2}秒", (int)t.TotalHours, t.Minutes, t.Seconds);
            return string.Format("{0}分{1}秒", (int)t.TotalMinutes, t.Seconds);
        }

        private static string Truncate(string s, int max)
        {
            if (string.IsNullOrEmpty(s) || s.Length <= max) return s;
            return s.Substring(0, max);
        }

        // ---------------- 启动 / 停止 / 重启 ----------------

        private void SetBusy(bool busy)
        {
            btnStart.Enabled = btnStop.Enabled = btnRestart.Enabled = !busy;
            EnableTrayOps(!busy);
        }

        private void EnableTrayOps(bool enabled)
        {
            // 托盘菜单第 3/4/5 项为启动/停止/重启（0=打开主界面, 1=分隔线）
            try
            {
                ((ToolStripMenuItem)trayMenu.Items[2]).Enabled = enabled;
                ((ToolStripMenuItem)trayMenu.Items[3]).Enabled = enabled;
                ((ToolStripMenuItem)trayMenu.Items[4]).Enabled = enabled;
            }
            catch { }
        }

        private void DoStartSafe()
        {
            SetBusy(true);
            ToolLog("尝试启动服务器…");
            Task.Factory.StartNew(delegate
            {
                string err = StartServer();
                BeginInvoke((MethodInvoker)delegate
                {
                    SetBusy(false);
                    ToolLog(string.IsNullOrEmpty(err) ? "启动指令已发出 ✓" : ("启动失败：" + err));
                    if (string.IsNullOrEmpty(err)) NotifyServer("管理器执行：启动服务器");
                    if (!string.IsNullOrEmpty(err)) MessageBox.Show("启动失败：\n" + err, "错误",
                        MessageBoxButtons.OK, MessageBoxIcon.Error);
                    PollOnce();
                });
            });
        }

        private void DoStopSafe()
        {
            SetBusy(true);
            ToolLog("尝试停止服务器…");
            Task.Factory.StartNew(delegate
            {
                string err = StopServer();
                BeginInvoke((MethodInvoker)delegate
                {
                    SetBusy(false);
                    ToolLog(string.IsNullOrEmpty(err) ? "服务器已停止 ✓" : ("停止失败：" + err));
                    if (string.IsNullOrEmpty(err)) NotifyServer("管理器执行：停止服务器");
                    if (!string.IsNullOrEmpty(err)) MessageBox.Show("停止失败：\n" + err, "错误",
                        MessageBoxButtons.OK, MessageBoxIcon.Error);
                    PollOnce();
                });
            });
        }

        private void DoRestartSafe()
        {
            SetBusy(true);
            ToolLog("尝试重启服务器…");
            Task.Factory.StartNew(delegate
            {
                string err = StopServer();
                if (string.IsNullOrEmpty(err))
                {
                    Thread.Sleep(1200);
                    err = StartServer();
                }
                BeginInvoke((MethodInvoker)delegate
                {
                    SetBusy(false);
                    ToolLog(string.IsNullOrEmpty(err) ? "重启完成 ✓" : ("重启失败：" + err));
                    if (string.IsNullOrEmpty(err)) NotifyServer("管理器执行：重启服务器");
                    if (!string.IsNullOrEmpty(err)) MessageBox.Show("重启失败：\n" + err, "错误",
                        MessageBoxButtons.OK, MessageBoxIcon.Error);
                    PollOnce();
                });
            });
        }

        /** 启动：schtasks /Run，任务不存在则直接 pythonw 拉起 */
        private string StartServer()
        {
            if (ProbePort(cfg.LocalPort, 500))
            {
                // 端口已被占用：若服务进程存在则视为已在运行
                if (FindServerProcess() != null) return "服务器已在运行";
            }
            var psi = new ProcessStartInfo();
            psi.FileName = "schtasks";
            psi.Arguments = "/Run /TN \"" + cfg.TaskName + "\"";
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardError = true;
            psi.RedirectStandardOutput = true;
            try
            {
                using (Process p = Process.Start(psi))
                {
                    string outp = p.StandardOutput.ReadToEnd();
                    string errp = p.StandardError.ReadToEnd();
                    p.WaitForExit(10000);
                    bool ok = p.ExitCode == 0;
                    if (ok) return WaitProcess(15);
                    // 任务不存在 → 直接拉起
                    if ((outp + errp).IndexOf("ERROR: The scheduled task", StringComparison.OrdinalIgnoreCase) >= 0
                        || (outp + errp).IndexOf("不存在", StringComparison.Ordinal) >= 0)
                    {
                        return StartDirect();
                    }
                    return "schtasks /Run 失败: " + (outp + errp).Trim();
                }
            }
            catch (Exception ex)
            {
                return StartDirect() ?? ("schtasks 异常: " + ex.Message);
            }
        }

        private string StartDirect()
        {
            try
            {
                if (!File.Exists(cfg.PythonwPath)) return "找不到 pythonw：" + cfg.PythonwPath;
                if (!File.Exists(cfg.ScriptPath)) return "找不到脚本：" + cfg.ScriptPath;
                ProcessStartInfo pi = new ProcessStartInfo();
                pi.FileName = cfg.PythonwPath;
                pi.Arguments = "\"" + cfg.ScriptPath + "\"";
                pi.UseShellExecute = false;
                pi.CreateNoWindow = true;
                pi.WorkingDirectory = Path.GetDirectoryName(cfg.ScriptPath);
                Process.Start(pi);
                return WaitProcess(15);
            }
            catch (Exception ex)
            {
                return "直接启动失败: " + ex.Message;
            }
        }

        /** 停止：schtasks /End，仍存活则杀进程 */
        private string StopServer()
        {
            var psi = new ProcessStartInfo();
            psi.FileName = "schtasks";
            psi.Arguments = "/End /TN \"" + cfg.TaskName + "\"";
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardError = true;
            psi.RedirectStandardOutput = true;
            try
            {
                using (Process p = Process.Start(psi))
                {
                    p.StandardOutput.ReadToEnd();
                    p.StandardError.ReadToEnd();
                    p.WaitForExit(10000);
                }
            }
            catch { }

            // 等待进程消失，最多 5 秒
            for (int i = 0; i < 10; i++)
            {
                Process proc = FindServerProcess();
                if (proc == null) return null;
                Thread.Sleep(500);
            }
            // 仍存活 → 强杀
            try
            {
                Process proc = FindServerProcess();
                if (proc != null)
                {
                    proc.Kill();
                    proc.WaitForExit(5000);
                }
                cachedPid = -1;
                return null;
            }
            catch (Exception ex)
            {
                return "强杀进程失败: " + ex.Message;
            }
        }

        /** 等待进程出现（后台线程） */
        private string WaitProcess(int maxSeconds)
        {
            for (int i = 0; i < maxSeconds * 2; i++)
            {
                Thread.Sleep(500);
                if (FindServerProcess() != null) return null;
            }
            return "启动后 " + maxSeconds + " 秒内未检测到进程";
        }

        // ---------------- 页面 / 日志 / 设置 ----------------

        private void OpenUrl(string path)
        {
            try
            {
                Process.Start("http://127.0.0.1:" + cfg.LocalPort + path);
            }
            catch (Exception ex)
            {
                MessageBox.Show("打开浏览器失败：" + ex.Message);
            }
        }

        /** 免登录打开 __remote 页面：先取 10 位访问 key，再交给浏览器（key 10 分钟有效） */
        private void OpenRemote(string next)
        {
            Task.Factory.StartNew(delegate
            {
                string url = ServerApi.RemoteUrl(cfg, next);
                if (string.IsNullOrEmpty(url))
                {
                    try
                    {
                        BeginInvoke((MethodInvoker)delegate
                        {
                            ToolLog("访问 key 获取失败，改为普通打开：" + next);
                            OpenUrl(next);
                        });
                    }
                    catch { }
                    return;
                }
                try
                {
                    Process.Start(url);
                    BeginInvoke((MethodInvoker)delegate
                    {
                        ToolLog("已免登录打开 " + next + "（票据一次性，60 秒有效）");
                    });
                }
                catch (Exception ex)
                {
                    try
                    {
                        BeginInvoke((MethodInvoker)delegate
                        {
                            ToolLog("打开失败：" + ex.Message + "，改为普通打开");
                            OpenUrl(next);
                        });
                    }
                    catch { }
                }
            });
        }

        private void OpenLogDir()
        {
            try
            {
                string dir = cfg.AssetsDir();
                if (!string.IsNullOrEmpty(dir) && Directory.Exists(dir)) Process.Start("explorer.exe", dir);
                else MessageBox.Show("日志目录不存在：" + dir);
            }
            catch (Exception ex)
            {
                MessageBox.Show("打开失败：" + ex.Message);
            }
        }

        private void RefreshServerLog()
        {
            try
            {
                string dir = cfg.AssetsDir();
                if (string.IsNullOrEmpty(dir)) return;
                string file = cboLog.SelectedIndex == 1
                    ? Path.Combine(dir, "seclog.jsonl")
                    : Path.Combine(dir, "oplog.jsonl");
                if (!File.Exists(file))
                {
                    serverLogBox.Text = "（暂无日志：" + file + "）";
                    return;
                }
                string[] all = File.ReadAllLines(file, Encoding.UTF8);
                int take = Math.Min(300, all.Length);
                var sb = new StringBuilder();
                for (int i = all.Length - take; i < all.Length; i++)
                {
                    sb.AppendLine(FormatJsonl(all[i]));
                }
                if (sb.Length == 0) sb.Append("（暂无记录）");
                serverLogBox.Text = sb.ToString();
                serverLogBox.SelectionStart = serverLogBox.TextLength;
                serverLogBox.ScrollToCaret();
            }
            catch (Exception ex)
            {
                serverLogBox.Text = "读取失败：" + ex.Message;
            }
        }

        /** 简单美化 jsonl：把字段抽成 key=value 竖排 */
        private static string FormatJsonl(string line)
        {
            try
            {
                if (string.IsNullOrWhiteSpace(line)) return "";
                string s = line.Trim();
                if (!s.StartsWith("{") || !s.EndsWith("}")) return s;
                var sb = new StringBuilder();
                string body = s.Substring(1, s.Length - 2);
                foreach (string pair in body.Split(','))   // 粗分割，值里含逗号时仅影响展示
                {
                    int i = pair.IndexOf(':');
                    if (i <= 0) continue;
                    string k = pair.Substring(0, i).Trim().Trim('"');
                    string v = pair.Substring(i + 1).Trim().Trim('"');
                    if (sb.Length > 0) sb.Append("  ");
                    sb.Append(k).Append('=').Append(v);
                }
                return sb.ToString();
            }
            catch { return line; }
        }

        // ---------- 工具日志（内存缓冲 + 文件落盘） ----------

        private readonly object logSync = new object();
        private readonly StringBuilder pendingLog = new StringBuilder();

        private void ToolLog(string msg)
        {
            string line = DateTime.Now.ToString("MM-dd HH:mm:ss") + "  " + msg;
            lock (logSync)
            {
                pendingLog.AppendLine(line);
            }
            try
            {
                File.AppendAllText(AppConfig.LogFile(),
                    line + Environment.NewLine, Encoding.UTF8);
            }
            catch { }
        }

        private void AppendPendingToolLog()
        {
            string chunk;
            lock (logSync)
            {
                if (pendingLog.Length == 0) return;
                chunk = pendingLog.ToString();
                pendingLog.Length = 0;
            }
            toolLogBox.AppendText(chunk);
        }

        // ---------- 设置 ----------

        private void SaveConfig()
        {
            string task = txtTaskName.Text.Trim();
            string script = txtScriptPath.Text.Trim();
            int port;
            if (task.Length == 0 || script.Length == 0
                || !int.TryParse(txtPort.Text.Trim(), out port)
                || port <= 0 || port > 65535)
            {
                MessageBox.Show("请检查设置：任务名/脚本路径不能为空，端口须为 1-65535。", "提示");
                return;
            }
            cfg.TaskName = task;
            cfg.ScriptPath = script;
            cfg.LocalPort = port;
            cfg.Save();
            ToolLog("设置已保存（任务=" + task + "，端口=" + port + "）");
            MessageBox.Show("设置已保存。", "提示");
            PollOnce();
        }

        private void RefreshAutoStartCheck()
        {
            chkAutoStart.Checked = IsAutoStartOn();
            chkAutoStart.CheckedChanged += delegate
            {
                try
                {
                    SetAutoStart(chkAutoStart.Checked);
                    ToolLog(chkAutoStart.Checked ? "已设置开机自启" : "已取消开机自启");
                }
                catch (Exception ex)
                {
                    MessageBox.Show("设置开机自启失败：" + ex.Message);
                    chkAutoStart.Checked = IsAutoStartOn();
                }
            };
        }

        private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        private const string RunValue = "AiwoServerManager";

        private static bool IsAutoStartOn()
        {
            using (RegistryKey k = Registry.CurrentUser.OpenSubKey(RunKey, false))
            {
                if (k == null) return false;
                object v = k.GetValue(RunValue);
                return v != null;
            }
        }

        private static void SetAutoStart(bool on)
        {
            using (RegistryKey k = Registry.CurrentUser.CreateSubKey(RunKey))
            {
                if (on) k.SetValue(RunValue, "\"" + Application.ExecutablePath + "\"");
                else k.DeleteValue(RunValue, false);
            }
        }

        // ---------------- 窗口行为 ----------------

        private void ShowWindow()
        {
            Show();
            WindowState = FormWindowState.Normal;
            Activate();
        }
    }
}
