// 管理器仪表盘：与服务器的双向通讯（令牌绑定 + 状态统计 + 远程操作审计）
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace AiwoServerManager
{
    // ---------------- 服务器返回的数据结构 ----------------

    public class MgrPath
    {
        public string path { get; set; }
        public int pv { get; set; }
    }

    public class MgrSeries
    {
        public string date { get; set; }
        public int pv { get; set; }
        public int uv { get; set; }
    }

    public class MgrVisitor
    {
        public string ip { get; set; }
        public string first { get; set; }
        public string last { get; set; }
        public int hits { get; set; }
        public string ua { get; set; }
    }

    public class MgrVisits
    {
        public string today { get; set; }
        public int pv { get; set; }
        public int uv { get; set; }
        public int online { get; set; }
        public int[] hours { get; set; }
        public MgrPath[] top_paths { get; set; }
        public MgrSeries[] series { get; set; }
        public MgrVisitor[] visitors { get; set; }
    }

    public class MgrStats
    {
        public bool ok { get; set; }
        public string name { get; set; }
        public string version { get; set; }
        public int port { get; set; }
        public int uptime { get; set; }
        public int accounts { get; set; }
        public int accounts_new_today { get; set; }
        public int sessions_active { get; set; }
        public int sms { get; set; }
        public int files { get; set; }
        public long files_bytes { get; set; }
        public int chats { get; set; }
        public int class_files { get; set; }
        public int chars { get; set; }
        public bool manager_bound { get; set; }
        public int manager_last { get; set; }
        public MgrVisits visits { get; set; }

        public string Error;   // 本地字段，非 JSON
    }

    public class MgrAccount
    {
        public string name { get; set; }
        public string created { get; set; }
        public string ip { get; set; }
        public string salt { get; set; }
        public string hash { get; set; }
        public string pwd_changed { get; set; }
        public int today_tokens { get; set; }
    }

    public class MgrAccountList
    {
        public MgrAccount[] accounts { get; set; }
        public string admin { get; set; }
    }

    /** 通用结果：登录 / 操作 都复用 */
    public class MgrResult
    {
        public bool ok { get; set; }
        public string account { get; set; }
        public string error { get; set; }
        public string url { get; set; }
        public string key { get; set; }
    }

    // ---------------- 与服务器通讯 ----------------

    public static class ServerApi
    {
        public static string TokenPath(AppConfig cfg)
        {
            try
            {
                string baseDir = Path.GetDirectoryName(cfg.ScriptPath);
                if (string.IsNullOrEmpty(baseDir)) return null;
                return Path.Combine(Path.Combine(baseDir, "admax"), "mgr_token.json");
            }
            catch { return null; }
        }

        /** 读取令牌（每次读文件，避免服务器重建令牌后缓存失效） */
        public static string ReadToken(AppConfig cfg)
        {
            try
            {
                string p = TokenPath(cfg);
                if (p == null || !File.Exists(p)) return "";
                string txt = File.ReadAllText(p, Encoding.UTF8);
                var ser = new JavaScriptSerializer();
                var d = ser.Deserialize<Dictionary<string, object>>(txt);
                if (d != null && d.ContainsKey("token"))
                    return Convert.ToString(d["token"]);
                return "";
            }
            catch { return ""; }
        }

        private static string Request(AppConfig cfg, string path, string body)
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(
                "http://127.0.0.1:" + cfg.LocalPort + path);
            req.Method = body == null ? "GET" : "POST";
            req.Timeout = 8000;
            req.ReadWriteTimeout = 8000;
            req.UserAgent = "AiwoServerManager/1.1";
            string token = ReadToken(cfg);
            if (token.Length > 0) req.Headers.Add("X-Mgr-Token", token);
            if (body != null)
            {
                byte[] data = Encoding.UTF8.GetBytes(body);
                req.ContentType = "application/json; charset=utf-8";
                req.ContentLength = data.Length;
                using (Stream s = req.GetRequestStream()) s.Write(data, 0, data.Length);
            }
            using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
            using (StreamReader sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                return sr.ReadToEnd();
        }

        public static MgrStats GetStats(AppConfig cfg)
        {
            try
            {
                string json = Request(cfg, "/__mgr/stats", null);
                var ser = new JavaScriptSerializer();
                ser.MaxJsonLength = 8 * 1024 * 1024;
                MgrStats st = ser.Deserialize<MgrStats>(json);
                if (st != null) st.ok = true;
                return st;
            }
            catch (Exception ex)
            {
                var st = new MgrStats();
                st.Error = ex is WebException ? "无法连接服务器（可能已停止）" : ex.Message;
                return st;
            }
        }

        public static string Action(AppConfig cfg, string action)
        {
            try
            {
                var ser = new JavaScriptSerializer();
                var body = ser.Serialize(new Dictionary<string, object> { { "action", action } });
                return Request(cfg, "/__mgr/action", body);
            }
            catch (Exception ex) { return "失败：" + ex.Message; }
        }

        public static void Note(AppConfig cfg, string text)
        {
            try
            {
                var ser = new JavaScriptSerializer();
                var body = ser.Serialize(new Dictionary<string, object> { { "text", text } });
                Request(cfg, "/__mgr/note", body);
            }
            catch { }
        }

        /** POST 并解析结果；HTTP 4xx 也读取响应体，网络失败返回 error 文本 */
        public static MgrResult PostResult(AppConfig cfg, string path, string body)
        {
            var r = new MgrResult();
            try
            {
                string json = Request(cfg, path, body);
                var ser = new JavaScriptSerializer();
                var got = ser.Deserialize<MgrResult>(json);
                return got ?? r;
            }
            catch (WebException ex)
            {
                if (ex.Response is HttpWebResponse)
                {
                    try
                    {
                        using (HttpWebResponse resp = (HttpWebResponse)ex.Response)
                        using (StreamReader sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                        {
                            var ser = new JavaScriptSerializer();
                            var got = ser.Deserialize<MgrResult>(sr.ReadToEnd());
                            if (got != null) { got.ok = false; return got; }
                        }
                    }
                    catch { }
                }
                r.ok = false;
                r.error = "无法连接服务器（可能已停止）";
                return r;
            }
            catch (Exception ex)
            {
                r.ok = false;
                r.error = ex.Message;
                return r;
            }
        }

        /** 管理器登录：账号 / 密码 / 双重验证码（与 __remote 完全一致） */
        public static MgrResult Login(AppConfig cfg, string account, string password, string vercode)
        {
            var ser = new JavaScriptSerializer();
            string body = ser.Serialize(new Dictionary<string, object>
            {
                { "account", account }, { "password", password }, { "vercode", vercode }
            });
            return PostResult(cfg, "/__mgr/login", body);
        }

        public static MgrAccountList GetAccounts(AppConfig cfg)
        {
            try
            {
                string json = Request(cfg, "/__mgr/accounts", null);
                var ser = new JavaScriptSerializer();
                return ser.Deserialize<MgrAccountList>(json);
            }
            catch { return null; }
        }

        public static MgrResult ActionEx(AppConfig cfg, string action, string arg, string value)
        {
            var ser = new JavaScriptSerializer();
            var body = ser.Serialize(new Dictionary<string, object>
            {
                { "action", action }, { "arg", arg ?? "" }, { "value", value ?? "" }
            });
            return PostResult(cfg, "/__mgr/action", body);
        }

        /** 获取 10 位访问 key，返回可直接打开的地址 wzc.life/?key=xxx（失败返回空串） */
        public static string RemoteUrl(AppConfig cfg, string next)
        {
            try
            {
                string json = Request(cfg, "/__mgr/remote_key", null);
                var ser = new JavaScriptSerializer();
                var r = ser.Deserialize<MgrResult>(json);
                if (r != null && r.ok && !string.IsNullOrEmpty(r.key))
                    return "http://wzc.life/?key=" + r.key;
            }
            catch { }
            return "";
        }
    }

    // ---------------- 主窗体：仪表盘 ----------------

    public partial class MainForm
    {
        private readonly Dictionary<string, Label> cardValues = new Dictionary<string, Label>();
        private Label lblBind;
        private TextBox txtTrend;
        private ListView lvVisitors;
        private System.Windows.Forms.Timer dashTimer;
        private bool dashBusy;
        private bool? dashConnected;

        private void InitDashboard()
        {
            dashTimer = new System.Windows.Forms.Timer();
            dashTimer.Interval = 5000;
            dashTimer.Tick += delegate { RefreshDashboard(); };
            dashTimer.Start();
            FormClosing += delegate { if (dashTimer != null) dashTimer.Stop(); };
        }

        private void BuildDashboardTab(TabPage page)
        {
            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.ColumnCount = 1;
            root.RowCount = 5;
            root.Padding = new Padding(10);

            // 1) 绑定状态
            lblBind = new Label();
            lblBind.AutoSize = true;
            lblBind.Font = new Font("Microsoft YaHei UI", 9.5f);
            lblBind.ForeColor = Color.FromArgb(120, 120, 120);
            lblBind.Text = "管理器绑定：检测中…";
            lblBind.Height = 24;

            // 2) 指标卡片
            var cards = new FlowLayoutPanel();
            cards.Dock = DockStyle.Fill;
            cards.FlowDirection = FlowDirection.LeftToRight;
            cards.WrapContents = true;
            AddCard(cards, "uv", "今日访客");
            AddCard(cards, "pv", "今日浏览");
            AddCard(cards, "online", "在线(5分钟)");
            AddCard(cards, "accounts", "注册账号");
            AddCard(cards, "new", "今日新增");
            AddCard(cards, "sms", "短信");
            AddCard(cards, "files", "公共文件");
            AddCard(cards, "sessions", "活跃会话");

            // 3) 趋势文本
            txtTrend = new TextBox();
            txtTrend.Dock = DockStyle.Fill;
            txtTrend.Multiline = true;
            txtTrend.ReadOnly = true;
            txtTrend.ScrollBars = ScrollBars.Vertical;
            txtTrend.BackColor = Color.FromArgb(246, 248, 252);
            txtTrend.Font = new Font(FontFamily.GenericMonospace, 9f);
            txtTrend.WordWrap = false;
            txtTrend.Text = "（等待服务器数据…）";

            // 4) 今日访客列表
            lvVisitors = new ListView();
            lvVisitors.Dock = DockStyle.Fill;
            lvVisitors.View = View.Details;
            lvVisitors.FullRowSelect = true;
            lvVisitors.GridLines = true;
            lvVisitors.Columns.Add("IP", 130);
            lvVisitors.Columns.Add("首次", 80);
            lvVisitors.Columns.Add("最近", 80);
            lvVisitors.Columns.Add("次数", 55);
            lvVisitors.Columns.Add("客户端", 320);

            // 5) 操作按钮
            var ops = new FlowLayoutPanel();
            ops.Dock = DockStyle.Fill;
            ops.FlowDirection = FlowDirection.LeftToRight;
            Button bRefresh = MakeDashButton("刷新统计", Color.FromArgb(52, 152, 219));
            Button bClearVisit = MakeDashButton("清空访问统计", Color.FromArgb(230, 126, 34));
            Button bClearSess = MakeDashButton("清理会话", Color.FromArgb(155, 89, 182));
            Button bClearSms = MakeDashButton("清空短信", Color.FromArgb(231, 76, 60));
            bRefresh.Click += delegate { RefreshDashboard(); };
            bClearVisit.Click += delegate { ConfirmAction("确定清空今日及历史访问统计？", "clear_visits"); };
            bClearSess.Click += delegate { ConfirmAction("确定清理全部登录会话（所有人需重新登录）？", "clear_sessions"); };
            bClearSms.Click += delegate { ConfirmAction("确定清空服务器短信收件箱？", "clear_sms"); };
            ops.Controls.Add(bRefresh);
            ops.Controls.Add(bClearVisit);
            ops.Controls.Add(bClearSess);
            ops.Controls.Add(bClearSms);

            root.Controls.Add(lblBind, 0, 0);
            root.Controls.Add(cards, 0, 1);
            root.Controls.Add(txtTrend, 0, 2);
            root.Controls.Add(lvVisitors, 0, 3);
            root.Controls.Add(ops, 0, 4);

            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 26));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 160));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 122));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 46));

            page.Controls.Add(root);
        }

        private void AddCard(FlowLayoutPanel parent, string key, string caption)
        {
            var p = new Panel();
            p.Width = 168;
            p.Height = 72;
            p.Margin = new Padding(0, 0, 8, 6);
            p.BackColor = Color.FromArgb(240, 244, 250);

            var val = new Label();
            val.AutoSize = false;
            val.Width = 164;
            val.Height = 40;
            val.Location = new Point(2, 4);
            val.TextAlign = ContentAlignment.MiddleCenter;
            val.Font = new Font("Microsoft YaHei UI", 16f, FontStyle.Bold);
            val.ForeColor = Color.FromArgb(41, 128, 185);
            val.Text = "-";

            var cap = new Label();
            cap.AutoSize = false;
            cap.Width = 164;
            cap.Height = 20;
            cap.Location = new Point(2, 46);
            cap.TextAlign = ContentAlignment.MiddleCenter;
            cap.Font = new Font("Microsoft YaHei UI", 8.5f);
            cap.ForeColor = Color.FromArgb(110, 110, 110);
            cap.Text = caption;

            p.Controls.Add(val);
            p.Controls.Add(cap);
            parent.Controls.Add(p);
            cardValues[key] = val;
        }

        private Button MakeDashButton(string text, Color back)
        {
            var b = new Button();
            b.Text = text;
            b.Width = 116;
            b.Height = 36;
            b.FlatStyle = FlatStyle.Flat;
            b.FlatAppearance.BorderSize = 0;
            b.BackColor = back;
            b.ForeColor = Color.White;
            b.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Bold);
            b.Margin = new Padding(0, 4, 8, 0);
            return b;
        }

        private void RefreshDashboard()
        {
            if (dashBusy) return;
            dashBusy = true;
            Task.Factory.StartNew(delegate
            {
                MgrStats st = ServerApi.GetStats(cfg);
                try
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        dashBusy = false;
                        ApplyDashboard(st);
                    });
                }
                catch { dashBusy = false; }
            });
        }

        private void ApplyDashboard(MgrStats st)
        {
            if (st == null || !st.ok)
            {
                lblBind.Text = "管理器绑定：未连接（" + (st == null ? "未知错误" : st.Error) + "）";
                lblBind.ForeColor = Color.FromArgb(192, 57, 43);
                if (dashConnected != false)
                {
                    dashConnected = false;
                    ToolLog("仪表盘：与服务器断开（" + (st == null ? "未知" : st.Error) + "）");
                }
                return;
            }
            bool justConnected = false;
            if (dashConnected != true)
            {
                dashConnected = true;
                justConnected = true;
            }
            MgrVisits v = st.visits ?? new MgrVisits();

            lblBind.Text = "管理器绑定：已连接 · " + st.name + " v" + st.version
                + " · 端口 " + st.port + " · 运行 " + FmtUptime(st.uptime)
                + " · 令牌文件已读取";
            lblBind.ForeColor = Color.FromArgb(39, 174, 96);

            SetCard("uv", v.uv);
            SetCard("pv", v.pv);
            SetCard("online", v.online);
            SetCard("accounts", st.accounts);
            SetCard("new", st.accounts_new_today);
            SetCard("sms", st.sms);
            SetCard("files", st.files);
            SetCard("sessions", st.sessions_active);

            // 趋势 + 热门页面 + 内容概览
            var sb = new StringBuilder();
            sb.AppendLine("近 7 日访问（柱=访客数，右侧为浏览量）");
            if (v.series != null)
            {
                int maxUv = 1;
                foreach (MgrSeries s in v.series) if (s.uv > maxUv) maxUv = s.uv;
                foreach (MgrSeries s in v.series)
                {
                    int bars = (int)Math.Round(24.0 * s.uv / maxUv);
                    if (s.uv > 0 && bars < 1) bars = 1;
                    sb.AppendLine("  " + (s.date.Length >= 5 ? s.date.Substring(5) : s.date)
                        + "  " + new string('#', bars).PadRight(24)
                        + " " + s.uv + " 人 / " + s.pv + " 次");
                }
            }
            sb.AppendLine();
            sb.Append("热门页面：");
            if (v.top_paths != null && v.top_paths.Length > 0)
            {
                foreach (MgrPath p in v.top_paths)
                    sb.Append("  " + p.path + "(" + p.pv + ")");
            }
            else sb.Append("  暂无");
            sb.AppendLine();
            sb.AppendLine();
            sb.Append("内容：账号 " + st.accounts + " · 角色 " + st.chars
                + " · 聊天记录 " + st.chats + " · 短信 " + st.sms
                + " · 文件 " + st.files + " (" + HumanSize(st.files_bytes) + ")");
            sb.Append("   · 管理器绑定：" + (st.manager_bound ? "已连接" : "未连接"));
            txtTrend.Text = sb.ToString();

            // 今日访客
            lvVisitors.BeginUpdate();
            lvVisitors.Items.Clear();
            if (v.visitors != null)
            {
                foreach (MgrVisitor vi in v.visitors)
                {
                    var it = new ListViewItem(new string[]
                    { vi.ip, vi.first, vi.last, vi.hits.ToString(), vi.ua });
                    lvVisitors.Items.Add(it);
                }
            }
            lvVisitors.EndUpdate();

            if (justConnected)
            {
                ToolLog("仪表盘：已绑定服务器 " + st.name + " v" + st.version
                    + "，今日访客 " + v.uv + " 人 / 浏览 " + v.pv
                    + " 次，趋势文本 " + txtTrend.TextLength + " 字");
            }
        }

        private void SetCard(string key, int value)
        {
            Label l;
            if (cardValues.TryGetValue(key, out l)) l.Text = value.ToString();
        }

        private void ConfirmAction(string question, string action)
        {
            if (MessageBox.Show(question, "确认", MessageBoxButtons.OKCancel,
                    MessageBoxIcon.Warning) != DialogResult.OK) return;
            Task.Factory.StartNew(delegate
            {
                string resp = ServerApi.Action(cfg, action);
                BeginInvoke((MethodInvoker)delegate
                {
                    ToolLog("服务器操作 " + action + " → " + resp);
                    RefreshDashboard();
                });
            });
        }

        /** 把管理器的操作写进服务器审计日志（管理器 → 服务器） */
        private void NotifyServer(string text)
        {
            Task.Factory.StartNew(delegate { ServerApi.Note(cfg, text); });
        }

        private static string FmtUptime(int sec)
        {
            if (sec >= 86400) return (sec / 86400) + "天" + (sec % 86400 / 3600) + "小时";
            if (sec >= 3600) return (sec / 3600) + "小时" + (sec % 3600 / 60) + "分";
            return (sec / 60) + "分" + (sec % 60) + "秒";
        }

        private static string HumanSize(long n)
        {
            if (n >= 1048576) return (n / 1048576.0).ToString("F1") + " MB";
            if (n >= 1024) return (n / 1024.0).ToString("F1") + " KB";
            return n + " B";
        }
    }
}
