// 管理器首次启动登录：账号 / 密码 / 双重验证码与 __remote 完全一致
using System;
using System.Drawing;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace AiwoServerManager
{
    public class LoginForm : Form
    {
        private readonly AppConfig cfg;
        private TextBox txtAccount;
        private TextBox txtPassword;
        private TextBox txtVercode;
        private Label lblMsg;
        private Button btnLogin;
        private Button btnOffline;
        private Button btnExit;
        private bool busy;

        public LoginForm(AppConfig cfg)
        {
            this.cfg = cfg;

            Text = "爱沃服务器管理工具 · 登录";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterScreen;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(432, 372);
            BackColor = Color.White;

            BuildUi();
        }

        private void BuildUi()
        {
            var title = new Label();
            title.Text = "爱沃服务器管理工具";
            title.Font = new Font("Microsoft YaHei UI", 16f, FontStyle.Bold);
            title.ForeColor = Color.FromArgb(41, 128, 185);
            title.AutoSize = true;
            title.Location = new Point(28, 18);

            var sub = new Label();
            sub.Text = "首次启动需登录：账号 / 密码 / 双重验证码与 __remote 完全相同";
            sub.Font = new Font("Microsoft YaHei UI", 9f);
            sub.ForeColor = Color.FromArgb(120, 120, 120);
            sub.AutoSize = true;
            sub.Location = new Point(30, 56);

            var l1 = MakeCaption("账号", 86);
            txtAccount = MakeInput(30, 106, false);

            var l2 = MakeCaption("密码", 136);
            txtPassword = MakeInput(30, 156, true);

            var l3 = MakeCaption("双重验证码", 186);
            txtVercode = MakeInput(30, 206, true);

            lblMsg = new Label();
            lblMsg.AutoSize = false;
            lblMsg.Size = new Size(372, 22);
            lblMsg.Location = new Point(30, 240);
            lblMsg.Font = new Font("Microsoft YaHei UI", 9f);
            lblMsg.ForeColor = Color.FromArgb(192, 57, 43);
            lblMsg.Text = "";

            btnLogin = MakeButton("登录", Color.FromArgb(46, 204, 113));
            btnLogin.Location = new Point(30, 268);
            btnLogin.Width = 176;
            btnLogin.Click += delegate { DoLogin(); };

            btnOffline = MakeButton("离线继续", Color.FromArgb(149, 165, 166));
            btnOffline.Location = new Point(216, 268);
            btnOffline.Width = 110;
            btnOffline.Click += delegate { OfflineContinue(); };

            btnExit = MakeButton("退出", Color.FromArgb(231, 76, 60));
            btnExit.Location = new Point(336, 268);
            btnExit.Width = 66;
            btnExit.Click += delegate { DialogResult = DialogResult.Cancel; Close(); };

            var tip = new Label();
            tip.Text = "登录成功后自动记住，下次启动不再询问。密码由服务器 PBKDF2 校验，管理器不保存明文。";
            tip.Font = new Font("Microsoft YaHei UI", 8.5f);
            tip.ForeColor = Color.FromArgb(140, 140, 140);
            tip.AutoSize = false;
            tip.Size = new Size(372, 34);
            tip.Location = new Point(30, 324);

            Controls.Add(title);
            Controls.Add(sub);
            Controls.Add(l1);
            Controls.Add(txtAccount);
            Controls.Add(l2);
            Controls.Add(txtPassword);
            Controls.Add(l3);
            Controls.Add(txtVercode);
            Controls.Add(lblMsg);
            Controls.Add(btnLogin);
            Controls.Add(btnOffline);
            Controls.Add(btnExit);
            Controls.Add(tip);

            AcceptButton = btnLogin;
            Load += delegate { txtAccount.Focus(); };
        }

        private Label MakeCaption(string text, int y)
        {
            var l = new Label();
            l.Text = text;
            l.AutoSize = true;
            l.Location = new Point(30, y);
            l.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Bold);
            l.ForeColor = Color.FromArgb(90, 90, 90);
            return l;
        }

        /** 输入框（secret=true 时掩码显示） */
        private TextBox MakeInput(int x, int y, bool secret)
        {
            var t = new TextBox();
            t.Location = new Point(x, y);
            t.Width = 372;
            t.Font = new Font("Microsoft YaHei UI", 10.5f);
            if (secret) t.PasswordChar = '\u25CF';
            return t;
        }

        private Button MakeButton(string text, Color back)
        {
            var b = new Button();
            b.Text = text;
            b.Height = 40;
            b.FlatStyle = FlatStyle.Flat;
            b.FlatAppearance.BorderSize = 0;
            b.BackColor = back;
            b.ForeColor = Color.White;
            b.Font = new Font("Microsoft YaHei UI", 10.5f, FontStyle.Bold);
            b.Cursor = Cursors.Hand;
            return b;
        }

        private static string ValueOf(TextBox t)
        {
            return t.Text.Trim();
        }

        private void DoLogin()
        {
            if (busy) return;
            string account = ValueOf(txtAccount);
            string password = txtPassword.Text;
            string vercode = ValueOf(txtVercode);
            if (account.Length == 0 || password.Length == 0 || vercode.Length == 0)
            {
                lblMsg.Text = "请填写账号、密码和双重验证码";
                return;
            }

            busy = true;
            btnLogin.Enabled = false;
            btnOffline.Enabled = false;
            lblMsg.ForeColor = Color.FromArgb(52, 152, 219);
            lblMsg.Text = "正在连接服务器验证…";

            Task.Factory.StartNew(delegate
            {
                MgrResult r = ServerApi.Login(cfg, account, password, vercode);
                try
                {
                    BeginInvoke((MethodInvoker)delegate { FinishLogin(r); });
                }
                catch { busy = false; }
            });
        }

        private void FinishLogin(MgrResult r)
        {
            busy = false;
            btnLogin.Enabled = true;
            btnOffline.Enabled = true;
            if (r != null && r.ok)
            {
                cfg.LoginOk = true;
                cfg.Save();
                DialogResult = DialogResult.OK;
                Close();
                return;
            }
            lblMsg.ForeColor = Color.FromArgb(192, 57, 43);
            lblMsg.Text = "登录失败：" + ((r == null || string.IsNullOrEmpty(r.error))
                ? "未知错误" : r.error);
        }

        private void OfflineContinue()
        {
            if (MessageBox.Show(
                    "离线继续将跳过登录验证，仪表盘与账号管理在服务器启动后才能使用。\n确定继续吗？",
                    "离线继续", MessageBoxButtons.OKCancel, MessageBoxIcon.Warning)
                != DialogResult.OK) return;
            DialogResult = DialogResult.OK;
            Close();
        }
    }
}
