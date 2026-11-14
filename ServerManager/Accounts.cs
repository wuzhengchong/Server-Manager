// 账号管理：查看账号 / 密码摘要、修改密码、新增、删除
using System;
using System.Drawing;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace AiwoServerManager
{
    public partial class MainForm
    {
        private ListView lvAccounts;
        private Label lblAccInfo;
        private System.Windows.Forms.Timer accTimer;
        private bool accBusy;

        private void InitAccounts()
        {
            accTimer = new System.Windows.Forms.Timer();
            accTimer.Interval = 15000;
            accTimer.Tick += delegate { RefreshAccounts(); };
            accTimer.Start();
            FormClosing += delegate { if (accTimer != null) accTimer.Stop(); };
        }

        private void BuildAccountsTab(TabPage page)
        {
            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.ColumnCount = 1;
            root.RowCount = 3;
            root.Padding = new Padding(10);

            lblAccInfo = new Label();
            lblAccInfo.AutoSize = true;
            lblAccInfo.Font = new Font("Microsoft YaHei UI", 9.5f);
            lblAccInfo.ForeColor = Color.FromArgb(120, 120, 120);
            lblAccInfo.Text = "账号列表：加载中…";

            lvAccounts = new ListView();
            lvAccounts.Dock = DockStyle.Fill;
            lvAccounts.View = View.Details;
            lvAccounts.FullRowSelect = true;
            lvAccounts.GridLines = true;
            lvAccounts.HideSelection = false;
            lvAccounts.Columns.Add("账号", 170);
            lvAccounts.Columns.Add("创建时间", 140);
            lvAccounts.Columns.Add("来源 IP", 110);
            lvAccounts.Columns.Add("今日额度", 80);
            lvAccounts.Columns.Add("密码摘要（PBKDF2）", 240);
            lvAccounts.DoubleClick += delegate { ShowAccountDetail(); };

            var ops = new FlowLayoutPanel();
            ops.Dock = DockStyle.Fill;
            ops.FlowDirection = FlowDirection.LeftToRight;

            Button bRefresh = MakeDashButton("刷新列表", Color.FromArgb(52, 152, 219));
            Button bDetail = MakeDashButton("查看详情", Color.FromArgb(41, 128, 185));
            Button bPwd = MakeDashButton("修改密码", Color.FromArgb(230, 126, 34));
            Button bAdd = MakeDashButton("新增账号", Color.FromArgb(46, 204, 113));
            Button bDel = MakeDashButton("删除账号", Color.FromArgb(231, 76, 60));

            bRefresh.Click += delegate { RefreshAccounts(); };
            bDetail.Click += delegate { ShowAccountDetail(); };
            bPwd.Click += delegate { ChangePassword(); };
            bAdd.Click += delegate { AddAccount(); };
            bDel.Click += delegate { DeleteAccount(); };

            ops.Controls.Add(bRefresh);
            ops.Controls.Add(bDetail);
            ops.Controls.Add(bPwd);
            ops.Controls.Add(bAdd);
            ops.Controls.Add(bDel);

            root.Controls.Add(lblAccInfo, 0, 0);
            root.Controls.Add(lvAccounts, 0, 1);
            root.Controls.Add(ops, 0, 2);

            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 26));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 46));

            page.Controls.Add(root);
        }

        private void RefreshAccounts()
        {
            if (accBusy || lvAccounts == null) return;
            accBusy = true;
            Task.Factory.StartNew(delegate
            {
                MgrAccountList al = ServerApi.GetAccounts(cfg);
                try
                {
                    BeginInvoke((MethodInvoker)delegate { accBusy = false; ApplyAccounts(al); });
                }
                catch { accBusy = false; }
            });
        }

        private void ApplyAccounts(MgrAccountList al)
        {
            if (lvAccounts == null) return;
            if (al == null || al.accounts == null)
            {
                lblAccInfo.Text = "账号列表：未连接服务器（请先启动服务器）";
                lblAccInfo.ForeColor = Color.FromArgb(192, 57, 43);
                return;
            }
            lblAccInfo.Text = "账号列表：" + al.accounts.Length + " 个"
                + (string.IsNullOrEmpty(al.admin) ? "" : "（站长 " + al.admin + " 不可删除）")
                + " · 双击查看详情";
            lblAccInfo.ForeColor = Color.FromArgb(39, 174, 96);

            lvAccounts.BeginUpdate();
            lvAccounts.Items.Clear();
            foreach (MgrAccount a in al.accounts)
            {
                string digest = (a.hash == null ? "" : a.hash);
                if (digest.Length > 20) digest = digest.Substring(0, 20) + "…";
                var it = new ListViewItem(new string[]
                {
                    a.name, a.created, a.ip, a.today_tokens.ToString(), digest
                });
                it.Tag = a;
                lvAccounts.Items.Add(it);
            }
            lvAccounts.EndUpdate();
        }

        private MgrAccount SelectedAccount()
        {
            if (lvAccounts == null || lvAccounts.SelectedItems.Count == 0)
            {
                MessageBox.Show("请先在列表中选择一个账号。", "提示");
                return null;
            }
            return (MgrAccount)lvAccounts.SelectedItems[0].Tag;
        }

        private void ShowAccountDetail()
        {
            MgrAccount a = SelectedAccount();
            if (a == null) return;
            var sb = new StringBuilder();
            sb.AppendLine("账号：" + a.name);
            sb.AppendLine("创建时间：" + (string.IsNullOrEmpty(a.created) ? "—" : a.created));
            sb.AppendLine("来源 IP：" + (string.IsNullOrEmpty(a.ip) ? "—" : a.ip));
            sb.AppendLine("密码最后修改：" + (string.IsNullOrEmpty(a.pwd_changed) ? "（从未修改）" : a.pwd_changed));
            sb.AppendLine("今日 AI 额度：" + a.today_tokens);
            sb.AppendLine();
            sb.AppendLine("密码摘要（PBKDF2-HMAC-SHA256 · 100000 轮）");
            sb.AppendLine("salt：" + (string.IsNullOrEmpty(a.salt) ? "—" : a.salt));
            sb.AppendLine("hash：" + (string.IsNullOrEmpty(a.hash) ? "—" : a.hash));
            sb.AppendLine();
            sb.AppendLine("说明：服务器只保存不可逆的哈希，无法还原明文密码。");
            sb.AppendLine("     如需查看用户真实密码只能由用户自行提供；");
            sb.AppendLine("     如需重置，请使用「修改密码」。");
            ShowTextDialog("账号详情 · " + a.name, sb.ToString());
        }

        private void ChangePassword()
        {
            MgrAccount a = SelectedAccount();
            if (a == null) return;
            string pwd = PasswordForm.Ask("修改密码 · " + a.name,
                "请输入 " + a.name + " 的新密码（6-64 位）：", this);
            if (pwd == null) return;
            RunAccountAction("修改账号密码", "set_password", a.name, pwd, true);
        }

        private void AddAccount()
        {
            NewAccountForm f = new NewAccountForm();
            if (f.ShowDialog(this) != DialogResult.OK) return;
            RunAccountAction("新增账号", "add_account", f.AccountName, f.AccountPassword, true);
        }

        private void DeleteAccount()
        {
            MgrAccount a = SelectedAccount();
            if (a == null) return;
            if (MessageBox.Show("确定删除账号「" + a.name + "」？\n该账号的登录会话将一并失效，且不可恢复。",
                    "删除账号", MessageBoxButtons.OKCancel, MessageBoxIcon.Warning) != DialogResult.OK)
                return;
            RunAccountAction("删除账号", "delete_account", a.name, "", false);
        }

        private void RunAccountAction(string label, string action, string arg, string value, bool confirmText)
        {
            Task.Factory.StartNew(delegate
            {
                MgrResult r = ServerApi.ActionEx(cfg, action, arg, value);
                try
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        if (r != null && r.ok)
                        {
                            ToolLog("账号管理：" + label + " " + arg + " 成功");
                            NotifyServer("管理器执行：" + label + " " + arg);
                        }
                        else
                        {
                            string err = (r == null || string.IsNullOrEmpty(r.error)) ? "未知错误" : r.error;
                            ToolLog("账号管理：" + label + " " + arg + " 失败 → " + err);
                            MessageBox.Show(label + "失败：" + err, "账号管理", MessageBoxButtons.OK, MessageBoxIcon.Error);
                        }
                        RefreshAccounts();
                        RefreshDashboard();
                    });
                }
                catch { }
            });
        }

        /** 只读文本对话框（用于展示摘要等长文本） */
        private void ShowTextDialog(string title, string text)
        {
            Form f = new Form();
            f.Text = title;
            f.StartPosition = FormStartPosition.CenterParent;
            f.ClientSize = new Size(560, 340);
            f.MinimizeBox = false;
            f.MaximizeBox = false;

            TextBox box = new TextBox();
            box.Multiline = true;
            box.ReadOnly = true;
            box.ScrollBars = ScrollBars.Vertical;
            box.Dock = DockStyle.Fill;
            box.Font = new Font(FontFamily.GenericMonospace, 9.5f);
            box.BackColor = Color.FromArgb(246, 248, 252);
            box.Text = text;

            Button ok = new Button();
            ok.Text = "关闭";
            ok.Dock = DockStyle.Bottom;
            ok.Height = 38;
            ok.FlatStyle = FlatStyle.Flat;
            ok.BackColor = Color.FromArgb(52, 152, 219);
            ok.ForeColor = Color.White;
            ok.Click += delegate { f.Close(); };

            f.Controls.Add(box);
            f.Controls.Add(ok);
            f.ShowDialog(this);
            f.Dispose();
        }
    }

    // ---------------- 输入对话框 ----------------

    public class PasswordForm : Form
    {
        private TextBox txt;
        private string result;

        public static string Ask(string title, string tip, IWin32Window owner)
        {
            using (var f = new PasswordForm())
            {
                f.Build(title, tip);
                return f.ShowDialog(owner) == DialogResult.OK ? f.result : null;
            }
        }

        private void Build(string title, string tip)
        {
            Text = title;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterParent;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(400, 160);

            var lbl = new Label();
            lbl.Text = tip;
            lbl.AutoSize = false;
            lbl.Size = new Size(360, 22);
            lbl.Location = new Point(20, 20);

            txt = new TextBox();
            txt.PasswordChar = '\u25CF';
            txt.Location = new Point(20, 50);
            txt.Width = 360;
            txt.Font = new Font("Microsoft YaHei UI", 10.5f);

            var ok = new Button();
            ok.Text = "确定";
            ok.Location = new Point(20, 100);
            ok.Width = 170;
            ok.Height = 36;
            ok.FlatStyle = FlatStyle.Flat;
            ok.BackColor = Color.FromArgb(46, 204, 113);
            ok.ForeColor = Color.White;
            ok.Click += delegate
            {
                string v = txt.Text;
                if (v.Length < 6 || v.Length > 64)
                {
                    MessageBox.Show("密码长度需为 6-64 位", "提示");
                    return;
                }
                result = v;
                DialogResult = DialogResult.OK;
                Close();
            };

            var cancel = new Button();
            cancel.Text = "取消";
            cancel.Location = new Point(210, 100);
            cancel.Width = 170;
            cancel.Height = 36;
            cancel.FlatStyle = FlatStyle.Flat;
            cancel.BackColor = Color.FromArgb(189, 195, 199);
            cancel.ForeColor = Color.White;
            cancel.Click += delegate { DialogResult = DialogResult.Cancel; Close(); };

            Controls.Add(lbl);
            Controls.Add(txt);
            Controls.Add(ok);
            Controls.Add(cancel);
            AcceptButton = ok;
        }
    }

    public class NewAccountForm : Form
    {
        private TextBox txtName;
        private TextBox txtPwd;
        public string AccountName;
        public string AccountPassword;

        public NewAccountForm()
        {
            Text = "新增账号";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterParent;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(440, 230);

            var l1 = new Label();
            l1.Text = "账号名（至少 16 位，中文算 2 位；考号可直接填 25040301xx）";
            l1.AutoSize = false;
            l1.Size = new Size(400, 20);
            l1.Location = new Point(20, 16);

            txtName = new TextBox();
            txtName.Location = new Point(20, 40);
            txtName.Width = 400;
            txtName.Font = new Font("Microsoft YaHei UI", 10.5f);

            var l2 = new Label();
            l2.Text = "密码（6-64 位）";
            l2.AutoSize = false;
            l2.Size = new Size(400, 20);
            l2.Location = new Point(20, 78);

            txtPwd = new TextBox();
            txtPwd.PasswordChar = '\u25CF';
            txtPwd.Location = new Point(20, 102);
            txtPwd.Width = 400;
            txtPwd.Font = new Font("Microsoft YaHei UI", 10.5f);

            var ok = new Button();
            ok.Text = "创建";
            ok.Location = new Point(20, 168);
            ok.Width = 190;
            ok.Height = 38;
            ok.FlatStyle = FlatStyle.Flat;
            ok.BackColor = Color.FromArgb(46, 204, 113);
            ok.ForeColor = Color.White;
            ok.Click += delegate
            {
                string name = txtName.Text.Trim();
                string pwd = txtPwd.Text;
                if (name.Length == 0)
                {
                    MessageBox.Show("请填写账号名", "提示");
                    return;
                }
                if (pwd.Length < 6 || pwd.Length > 64)
                {
                    MessageBox.Show("密码长度需为 6-64 位", "提示");
                    return;
                }
                AccountName = name;
                AccountPassword = pwd;
                DialogResult = DialogResult.OK;
                Close();
            };

            var cancel = new Button();
            cancel.Text = "取消";
            cancel.Location = new Point(230, 168);
            cancel.Width = 190;
            cancel.Height = 38;
            cancel.FlatStyle = FlatStyle.Flat;
            cancel.BackColor = Color.FromArgb(189, 195, 199);
            cancel.ForeColor = Color.White;
            cancel.Click += delegate { DialogResult = DialogResult.Cancel; Close(); };

            Controls.Add(l1);
            Controls.Add(txtName);
            Controls.Add(l2);
            Controls.Add(txtPwd);
            Controls.Add(ok);
            Controls.Add(cancel);
            AcceptButton = ok;
        }
    }
}
