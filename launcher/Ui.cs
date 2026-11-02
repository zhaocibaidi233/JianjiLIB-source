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

// 自绘控件与调色板（2026-10-26 从 Launcher.cs 拆出，行为零变化）。
//
// 为什么单独成文件：原先 Launcher.cs 4,745 行 = 控件 + 窗体 + 进程管理 + 建库 + DPAPI
// + 端口查杀 + 日志，改任何一处都要在四千行里找。这里拆的是**与窗体无耦合**的部分：
// 每个控件只依赖 Palette 与标准 WinForms，grep `LauncherForm` 零命中。
//
// ⚠️ 拆分纪律：**只搬不改**。任何行为修改单独提交，否则出问题分不清是搬错还是改错。
namespace JianjiLauncher
{
    internal static class Palette
    {
        public static readonly Color BgTop = Color.FromArgb(246, 245, 241);
        public static readonly Color BgBottom = Color.FromArgb(238, 236, 228);
        public static readonly Color Surface = Color.White;
        public static readonly Color SurfaceSoft = Color.FromArgb(250, 249, 246);
        public static readonly Color SurfaceWarm = Color.FromArgb(251, 250, 245);
        public static readonly Color Ink = Color.FromArgb(22, 33, 29);
        public static readonly Color Text = Color.FromArgb(42, 53, 49);
        public static readonly Color Muted = Color.FromArgb(102, 116, 110);
        public static readonly Color Faint = Color.FromArgb(147, 160, 154);
        public static readonly Color Line = Color.FromArgb(230, 228, 221);
        public static readonly Color LineStrong = Color.FromArgb(214, 211, 201);
        public static readonly Color Primary = Color.FromArgb(23, 96, 79);
        public static readonly Color PrimaryDeep = Color.FromArgb(13, 74, 60);
        public static readonly Color Primary600 = Color.FromArgb(30, 117, 97);
        public static readonly Color PrimarySoft = Color.FromArgb(227, 239, 233);
        public static readonly Color PrimarySofter = Color.FromArgb(240, 246, 242);
        public static readonly Color PrimaryLine = Color.FromArgb(217, 231, 223);
        public static readonly Color Accent = Color.FromArgb(185, 127, 46);
        public static readonly Color AccentDeep = Color.FromArgb(143, 95, 29);
        public static readonly Color AccentSoft = Color.FromArgb(246, 236, 217);
        public static readonly Color Danger = Color.FromArgb(180, 80, 60);
        public static readonly Color DangerSoft = Color.FromArgb(247, 231, 226);
        public static readonly Color Success = Color.FromArgb(47, 138, 96);
        public static readonly Color SuccessSoft = Color.FromArgb(228, 242, 234);
        public static readonly Color Info = Color.FromArgb(58, 107, 156);
        public static readonly Color TitleBar = Color.FromArgb(251, 250, 245);
        public static readonly Color SwitchOff = Color.FromArgb(214, 211, 201);
        public static readonly Color Shadow = Color.FromArgb(22, 33, 29);
        public static readonly Color LogInk = Color.FromArgb(75, 90, 82);
    }

    internal enum UiIcon
    {
        None,
        Power,
        Database,
        Server,
        Play,
        Stop,
        Folder,
        Check,
        Save,
        Plus,
        Refresh,
        ChevronDown,
        Pulse
    }

    /// <summary>
    /// 统一线性图标。24×24 设计稿坐标缩放到目标矩形，线宽按目标尺寸换算，
    /// 保证 12px 的标签和 16px 的按钮图标看起来是一套东西。
    /// </summary>
    internal static class IconPainter
    {
        public static void Draw(Graphics g, UiIcon icon, Rectangle bounds, Color color, float stroke)
        {
            if (icon == UiIcon.None || bounds.Width <= 0 || bounds.Height <= 0) return;
            GraphicsState state = g.Save();
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TranslateTransform(bounds.X, bounds.Y);
            g.ScaleTransform(bounds.Width / 24F, bounds.Height / 24F);
            float size = Math.Max(1F, Math.Min(bounds.Width, bounds.Height));
            float penWidth = Math.Max(1.1F, stroke * 24F / size);
            using (Pen pen = new Pen(color, penWidth))
            {
                pen.StartCap = LineCap.Round;
                pen.EndCap = LineCap.Round;
                pen.LineJoin = LineJoin.Round;
                switch (icon)
                {
                    case UiIcon.Power:
                        g.DrawLine(pen, 12F, 3F, 12F, 11F);
                        g.DrawArc(pen, 4F, 4F, 16F, 16F, -55F, 290F);
                        break;
                    case UiIcon.Database:
                        g.DrawEllipse(pen, 5F, 3F, 14F, 6F);
                        g.DrawLine(pen, 5F, 6F, 5F, 18F);
                        g.DrawLine(pen, 19F, 6F, 19F, 18F);
                        g.DrawArc(pen, 5F, 9F, 14F, 6F, 0F, 180F);
                        g.DrawArc(pen, 5F, 15F, 14F, 6F, 0F, 180F);
                        break;
                    case UiIcon.Server:
                        using (GraphicsPath top = RoundedPanel.RoundRect(
                            new Rectangle(4, 4, 16, 6), 2))
                            g.DrawPath(pen, top);
                        using (GraphicsPath bottom = RoundedPanel.RoundRect(
                            new Rectangle(4, 14, 16, 6), 2))
                            g.DrawPath(pen, bottom);
                        using (SolidBrush dot = new SolidBrush(color))
                        {
                            g.FillEllipse(dot, 7.2F, 6.4F, 1.7F, 1.7F);
                            g.FillEllipse(dot, 7.2F, 16.4F, 1.7F, 1.7F);
                        }
                        break;
                    case UiIcon.Play:
                        using (SolidBrush fill = new SolidBrush(color))
                        {
                            PointF[] triangle = new PointF[]
                            {
                                new PointF(8F, 5.5F),
                                new PointF(18.5F, 12F),
                                new PointF(8F, 18.5F)
                            };
                            g.FillPolygon(fill, triangle);
                        }
                        break;
                    case UiIcon.Stop:
                        using (GraphicsPath path = RoundedPanel.RoundRect(
                            new Rectangle(7, 7, 10, 10), 2))
                        using (SolidBrush fill = new SolidBrush(color))
                            g.FillPath(fill, path);
                        break;
                    case UiIcon.Folder:
                        using (GraphicsPath path = new GraphicsPath())
                        {
                            path.AddLines(new PointF[]
                            {
                                new PointF(4F, 7.5F), new PointF(9.5F, 7.5F),
                                new PointF(11.2F, 9.5F), new PointF(20F, 9.5F),
                                new PointF(20F, 18F), new PointF(4F, 18F)
                            });
                            path.CloseFigure();
                            g.DrawPath(pen, path);
                        }
                        break;
                    case UiIcon.Check:
                        g.DrawLines(pen, new PointF[]
                        {
                            new PointF(5F, 12.5F), new PointF(9.4F, 17F),
                            new PointF(19F, 7.2F)
                        });
                        break;
                    case UiIcon.Save:
                        using (GraphicsPath path = new GraphicsPath())
                        {
                            path.AddLines(new PointF[]
                            {
                                new PointF(5F, 4F), new PointF(16F, 4F),
                                new PointF(19F, 7F), new PointF(19F, 20F),
                                new PointF(5F, 20F)
                            });
                            path.CloseFigure();
                            g.DrawPath(pen, path);
                        }
                        g.DrawLines(pen, new PointF[]
                        {
                            new PointF(8F, 4F), new PointF(8F, 9F),
                            new PointF(16F, 9F), new PointF(16F, 4F)
                        });
                        g.DrawLines(pen, new PointF[]
                        {
                            new PointF(8F, 20F), new PointF(8F, 14F),
                            new PointF(16F, 14F), new PointF(16F, 20F)
                        });
                        break;
                    case UiIcon.Plus:
                        g.DrawLine(pen, 12F, 5F, 12F, 19F);
                        g.DrawLine(pen, 5F, 12F, 19F, 12F);
                        break;
                    case UiIcon.Refresh:
                        g.DrawArc(pen, 4F, 4F, 16F, 16F, -50F, 300F);
                        g.DrawLines(pen, new PointF[]
                        {
                            new PointF(20F, 4.5F), new PointF(20F, 10F),
                            new PointF(14.5F, 10F)
                        });
                        break;
                    case UiIcon.ChevronDown:
                        g.DrawLines(pen, new PointF[]
                        {
                            new PointF(6F, 9F), new PointF(12F, 15F),
                            new PointF(18F, 9F)
                        });
                        break;
                    case UiIcon.Pulse:
                        g.DrawLines(pen, new PointF[]
                        {
                            new PointF(3F, 12F), new PointF(7F, 12F),
                            new PointF(9.5F, 6F), new PointF(14.5F, 18F),
                            new PointF(17F, 12F), new PointF(21F, 12F)
                        });
                        break;
                }
            }
            g.Restore(state);
        }
    }

    /// <summary>
    /// 胶囊标签：版本号、实时状态、端口徽标、数据库状态条共用。
    /// 用 DrawPath 而不是 Label 的背景色，圆角不会出现方角。
    /// </summary>
    internal sealed class PillLabel : Control
    {
        public Color FillColor = Palette.SurfaceWarm;
        public Color BorderColor = Palette.Line;
        public Color TextColor = Palette.Muted;
        public Color DotColor = Palette.Accent;
        public bool ShowDot;
        public int Radius = 999;
        public int SidePadding = 8;
        public ContentAlignment TextAlign = ContentAlignment.MiddleCenter;

        public PillLabel()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            BackColor = Palette.BgTop;
            Font = new Font("Microsoft YaHei UI", 8F, FontStyle.Regular, GraphicsUnit.Point);
            Height = 22;
        }

        public void SetPalette(Color fill, Color border, Color text, Color dot)
        {
            FillColor = fill;
            BorderColor = border;
            TextColor = text;
            DotColor = dot;
            Invalidate();
        }

        protected override void OnTextChanged(EventArgs e)
        {
            base.OnTextChanged(e);
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            Rectangle rect = new Rectangle(0, 0, Width - 1, Height - 1);
            using (GraphicsPath path = RoundedPanel.RoundRect(rect,
                Math.Min(Radius, Math.Max(2, Height / 2))))
            {
                using (SolidBrush fill = new SolidBrush(FillColor))
                    e.Graphics.FillPath(fill, path);
                using (Pen pen = new Pen(BorderColor))
                    e.Graphics.DrawPath(pen, path);
            }

            TextFormatFlags flags = TextFormatFlags.VerticalCenter |
                TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis |
                TextFormatFlags.NoPadding;
            int textTop = 0;
            int textHeight = Height;
            if (ShowDot)
            {
                Size textSize = TextRenderer.MeasureText(Text, Font,
                    new Size(Int32.MaxValue, Int32.MaxValue), TextFormatFlags.NoPadding);
                int total = 5 + 5 + textSize.Width;
                int start = TextAlign == ContentAlignment.MiddleCenter
                    ? (Width - total) / 2
                    : TextAlign == ContentAlignment.MiddleRight
                        ? Width - SidePadding - total
                        : SidePadding;
                if (start < 2) start = 2;
                using (SolidBrush dot = new SolidBrush(DotColor))
                    e.Graphics.FillEllipse(dot, start, Height / 2 - 2, 5, 5);
                int rightPad = TextAlign == ContentAlignment.MiddleCenter
                    ? Math.Max(2, (Width - total) / 2)
                    : SidePadding;
                Rectangle textRect = new Rectangle(start + 10, textTop,
                    Math.Max(1, Width - start - 10 - rightPad), textHeight);
                TextRenderer.DrawText(e.Graphics, Text, Font, textRect, TextColor, flags);
                return;
            }

            Rectangle normalRect = new Rectangle(SidePadding, textTop,
                Math.Max(1, Width - SidePadding * 2), textHeight);
            if (TextAlign == ContentAlignment.MiddleCenter)
                flags |= TextFormatFlags.HorizontalCenter;
            else if (TextAlign == ContentAlignment.MiddleRight)
                flags |= TextFormatFlags.Right;
            else
                flags |= TextFormatFlags.Left;
            TextRenderer.DrawText(e.Graphics, Text, Font, normalRect, TextColor, flags);
        }
    }

    /// <summary>页面标题左侧的薄荷色图标底座。</summary>
    internal sealed class IconChip : Control
    {
        public UiIcon Icon = UiIcon.Server;
        public Color FillColor = Palette.PrimarySoft;
        public Color BorderColor = Palette.PrimaryLine;
        public Color IconColor = Palette.Primary;
        public int Radius = 11;

        public IconChip()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            BackColor = Palette.BgTop;
            Size = new Size(36, 36);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            Rectangle rect = new Rectangle(0, 0, Width - 1, Height - 1);
            using (GraphicsPath path = RoundedPanel.RoundRect(rect, Radius))
            {
                using (SolidBrush fill = new SolidBrush(FillColor))
                    e.Graphics.FillPath(fill, path);
                using (Pen pen = new Pen(BorderColor))
                    e.Graphics.DrawPath(pen, path);
            }
            int size = Math.Min(18, Math.Min(Width, Height) - 10);
            IconPainter.Draw(e.Graphics, Icon,
                new Rectangle((Width - size) / 2, (Height - size) / 2, size, size),
                IconColor, 1.55F);
        }
    }

    /// <summary>日志头部的文字链接，带 13px 线性图标和键盘可达的焦点态。</summary>
    internal sealed class IconLink : Control
    {
        public UiIcon Icon = UiIcon.None;
        public Color TextColor = Palette.Primary;
        public Color HoverColor = Palette.PrimaryDeep;
        private bool hover;

        public IconLink()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw |
                     ControlStyles.Selectable | ControlStyles.SupportsTransparentBackColor, true);
            BackColor = Palette.BgTop;
            Cursor = Cursors.Hand;
            TabStop = true;
            Font = new Font("Microsoft YaHei UI", 8.5F, FontStyle.Bold, GraphicsUnit.Point);
            Height = 18;
        }

        protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hover = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
        protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Enter || e.KeyCode == Keys.Space)
            {
                OnClick(EventArgs.Empty);
                e.Handled = true;
                return;
            }
            base.OnKeyDown(e);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            Color color = hover || Focused ? HoverColor : TextColor;
            int iconSize = 13;
            int x = 0;
            if (Icon != UiIcon.None)
            {
                IconPainter.Draw(e.Graphics, Icon,
                    new Rectangle(0, (Height - iconSize) / 2, iconSize, iconSize),
                    color, 1.6F);
                x = iconSize + 5;
            }
            Rectangle textRect = new Rectangle(x, 0, Math.Max(1, Width - x), Height);
            TextRenderer.DrawText(e.Graphics, Text, Font, textRect, color,
                TextFormatFlags.Left | TextFormatFlags.VerticalCenter |
                TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis |
                TextFormatFlags.NoPadding);
            if (Focused)
            {
                int w = TextRenderer.MeasureText(Text, Font, new Size(Int32.MaxValue, Int32.MaxValue),
                    TextFormatFlags.NoPadding).Width;
                using (Pen pen = new Pen(Color.FromArgb(90, color)))
                    e.Graphics.DrawLine(pen, x, Height - 1, Math.Min(Width - 1, x + w), Height - 1);
            }
        }
    }

    internal sealed class RoundedPanel : Panel
    {
        public int Radius = 12;
        public Color FillColor = Palette.Surface;
        public Color BorderColor = Palette.Line;

        public RoundedPanel()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            BackColor = Palette.BgTop;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            Rectangle rect = new Rectangle(1, 1, Width - 2, Height - 2);
            using (GraphicsPath path = RoundRect(rect, Radius))
            {
                using (SolidBrush fill = new SolidBrush(FillColor))
                    e.Graphics.FillPath(fill, path);
                using (Pen pen = new Pen(BorderColor))
                    e.Graphics.DrawPath(pen, path);
            }
        }

        public static GraphicsPath RoundRect(Rectangle rect, int radius)
        {
            GraphicsPath path = new GraphicsPath();
            int d = Math.Min(radius * 2, Math.Min(rect.Width, rect.Height));
            if (d <= 0)
            {
                path.AddRectangle(rect);
                return path;
            }
            path.AddArc(rect.X, rect.Y, d, d, 180, 90);
            path.AddArc(rect.Right - d, rect.Y, d, d, 270, 90);
            path.AddArc(rect.Right - d, rect.Bottom - d, d, d, 0, 90);
            path.AddArc(rect.X, rect.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }
    }

    internal sealed class RoundedButton : Button
    {
        public bool Primary;
        public int Radius = 8;
        public UiIcon Icon = UiIcon.None;
        public int IconSize = 14;
        private bool hover;
        private bool pressed;

        public RoundedButton()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 0;
            UseVisualStyleBackColor = false;
            BackColor = Palette.BgTop;
            Cursor = Cursors.Hand;
            Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold, GraphicsUnit.Point);
            Height = 38;
        }

        protected override void OnMouseEnter(EventArgs e)
        {
            hover = true;
            Invalidate();
            base.OnMouseEnter(e);
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            hover = false;
            pressed = false;
            Invalidate();
            base.OnMouseLeave(e);
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            if (e.Button == MouseButtons.Left)
            {
                pressed = true;
                Invalidate();
            }
            base.OnMouseDown(e);
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            pressed = false;
            Invalidate();
            base.OnMouseUp(e);
        }

        protected override void OnEnabledChanged(EventArgs e)
        {
            Invalidate();
            base.OnEnabledChanged(e);
        }

        protected override void OnGotFocus(EventArgs e)
        {
            Invalidate();
            base.OnGotFocus(e);
        }

        protected override void OnLostFocus(EventArgs e)
        {
            Invalidate();
            base.OnLostFocus(e);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            Color fill;
            Color fore;
            Color border;
            if (!Enabled)
            {
                fill = Primary ? Palette.PrimarySoft : Palette.SurfaceSoft;
                fore = Palette.Faint;
                border = Primary ? Palette.PrimarySoft : Palette.Line;
            }
            else if (Primary)
            {
                fill = pressed ? Palette.PrimaryDeep
                    : hover ? Color.FromArgb(35, 132, 109) : Palette.Primary;
                fore = Color.White;
                border = Palette.PrimaryDeep;
            }
            else
            {
                fill = pressed ? Palette.SurfaceSoft
                    : hover ? Palette.SurfaceWarm : Palette.Surface;
                fore = Palette.Text;
                border = hover ? Color.FromArgb(200, 194, 180) : Palette.LineStrong;
            }

            Rectangle rect = new Rectangle(1, 1, Width - 2, Height - 2);
            using (GraphicsPath path = RoundedPanel.RoundRect(rect, Radius))
            {
                if (Primary && Enabled)
                {
                    using (LinearGradientBrush gradient = new LinearGradientBrush(
                        rect, Palette.Primary600, Palette.Primary, 90F))
                        e.Graphics.FillPath(gradient, path);
                    if (hover || pressed)
                    {
                        using (SolidBrush overlay = new SolidBrush(pressed
                            ? Color.FromArgb(18, 20, 20, 20) : Color.FromArgb(16, 255, 255, 255)))
                            e.Graphics.FillPath(overlay, path);
                    }
                }
                else
                {
                    using (SolidBrush brush = new SolidBrush(fill))
                        e.Graphics.FillPath(brush, path);
                }
                using (Pen pen = new Pen(border, 1F))
                    e.Graphics.DrawPath(pen, path);
            }

            string text = Text ?? "";
            Size textSize = TextRenderer.MeasureText(text, Font,
                new Size(Int32.MaxValue, Int32.MaxValue), TextFormatFlags.NoPadding);
            int iconSize = Icon == UiIcon.None ? 0 : IconSize;
            int gap = Icon == UiIcon.None ? 0 : 7;
            int total = iconSize + gap + textSize.Width;
            int startX = Math.Max(2, (Width - total) / 2);
            if (Icon != UiIcon.None)
            {
                IconPainter.Draw(e.Graphics, Icon,
                    new Rectangle(startX, (Height - IconSize) / 2, IconSize, IconSize),
                    fore, 1.7F);
                startX += iconSize + gap;
            }
            Rectangle textRect = new Rectangle(startX, 0,
                Math.Max(1, Width - startX - 2), Height);
            TextRenderer.DrawText(e.Graphics, text, Font, textRect, fore,
                TextFormatFlags.Left | TextFormatFlags.VerticalCenter |
                TextFormatFlags.SingleLine | TextFormatFlags.NoPadding);

            if (Focused)
            {
                // 焦点提示只做一层极淡的内描边：原来 5px 内缩 + 高对比度，
                // 看起来像按钮里又套了个方框。
                Rectangle inner = new Rectangle(3, 3, Width - 6, Height - 6);
                Color focusColor = !Enabled ? Color.FromArgb(35, Palette.Faint)
                    : Primary ? Color.FromArgb(90, 255, 255, 255)
                    : Color.FromArgb(70, Palette.Primary);
                using (GraphicsPath focusPath = RoundedPanel.RoundRect(inner, Math.Max(2, Radius - 2)))
                using (Pen focusPen = new Pen(focusColor, 1F))
                    e.Graphics.DrawPath(focusPen, focusPath);
            }
        }
    }

    /// <summary>
    /// 开关控件。用自绘的胶囊轨道 + 圆钮替代 WinForms 那个系统风格的 CheckBox，
    /// 否则一行按钮旁边挂一个 95 年代外观的方框，整个界面就散了。
    /// 支持鼠标点击与空格键，CheckedChanged 与原生语义一致。
    /// </summary>
    internal sealed class ToggleSwitch : Control
    {
        private bool on;
        private bool hover;

        public event EventHandler CheckedChanged;

        public ToggleSwitch()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw |
                     ControlStyles.Selectable, true);
            Size = new Size(44, 24);
            Cursor = Cursors.Hand;
            TabStop = true;
            BackColor = Palette.BgTop;
            // 自绘控件默认在无障碍树里只报成「窗格」，读屏软件念不出这是个开关、
            // 也念不出当前是开还是关（2026-10-26 评测 P2）。
            AccessibleRole = AccessibleRole.CheckButton;
            AccessibleDescription = "已关闭";
        }

        public bool Checked
        {
            get { return on; }
            set
            {
                if (on == value) return;
                on = value;
                AccessibleDescription = on ? "已开启" : "已关闭";
                Invalidate();
                EventHandler handler = CheckedChanged;
                if (handler != null) handler(this, EventArgs.Empty);
            }
        }

        protected override void OnClick(EventArgs e)
        {
            Focus();
            Checked = !Checked;
            base.OnClick(e);
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Space)
            {
                Checked = !Checked;
                e.Handled = true;
                return;
            }
            base.OnKeyDown(e);
        }

        protected override void OnMouseEnter(EventArgs e)
        {
            hover = true;
            Invalidate();
            base.OnMouseEnter(e);
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            hover = false;
            Invalidate();
            base.OnMouseLeave(e);
        }

        protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
        protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }
        protected override void OnEnabledChanged(EventArgs e) { Invalidate(); base.OnEnabledChanged(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            Rectangle track = new Rectangle(0, 0, Width - 1, Height - 1);
            Color fill;
            if (!Enabled) fill = Color.FromArgb(232, 230, 223);
            else if (on) fill = hover ? Palette.Primary600 : Palette.Primary;
            else fill = hover ? Color.FromArgb(200, 196, 187) : Palette.SwitchOff;

            using (GraphicsPath path = RoundedPanel.RoundRect(track, Height / 2))
            using (SolidBrush brush = new SolidBrush(fill))
                e.Graphics.FillPath(brush, path);

            int knob = Height - 6;
            int x = on ? Width - knob - 3 : 3;
            if (on && Enabled)
            {
                using (SolidBrush soft = new SolidBrush(Color.FromArgb(28, 0, 0, 0)))
                    e.Graphics.FillEllipse(soft, x, 4, knob, knob);
            }
            using (SolidBrush white = new SolidBrush(Enabled ? Color.White : Color.FromArgb(247, 249, 246)))
                e.Graphics.FillEllipse(white, x, 3, knob, knob);
            if (Focused)
            {
                using (Pen pen = new Pen(Color.FromArgb(110, Palette.Primary), 1F))
                    e.Graphics.DrawEllipse(pen, x - 1, 2, knob + 2, knob + 2);
            }
        }
    }

    /// <summary>
    /// 两段式页内切换。用自绘胶囊轨道而不是两个原生按钮：启动器的标题区是
    /// 浅色卡片语言，两颗高饱和按钮并排会像两个主操作，抢掉「启动」的注意力。
    /// </summary>
    internal sealed class SegmentedTabs : Control
    {
        private string[] labels = new string[0];
        private UiIcon[] icons = new UiIcon[0];
        private int selectedIndex;
        private int hoverIndex = -1;

        public event EventHandler SelectedIndexChanged;

        public SegmentedTabs()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw |
                     ControlStyles.Selectable, true);
            Size = new Size(192, 36);
            Cursor = Cursors.Hand;
            TabStop = true;
            BackColor = Palette.BgTop;
            Font = new Font("Microsoft YaHei UI", 9.5F, FontStyle.Bold, GraphicsUnit.Point);
            // 同上：自绘页签在无障碍树里要报成页签列表，否则读屏只念「窗格」。
            AccessibleRole = AccessibleRole.PageTabList;
        }

        public int SelectedIndex
        {
            get { return selectedIndex; }
            set
            {
                if (labels.Length == 0) return;
                if (value < 0) value = 0;
                if (value >= labels.Length) value = labels.Length - 1;
                if (selectedIndex == value) return;
                selectedIndex = value;
                // 读屏要知道「现在停在哪一页签」，只报控件类型是不够的
                AccessibleDescription = "当前页签：" + labels[value];
                Invalidate();
                EventHandler handler = SelectedIndexChanged;
                if (handler != null) handler(this, EventArgs.Empty);
            }
        }

        public void SetLabels(params string[] values)
        {
            labels = values ?? new string[0];
            selectedIndex = 0;
            Invalidate();
        }

        public void SetIcons(params UiIcon[] values)
        {
            icons = values ?? new UiIcon[0];
            Invalidate();
        }

        private int HitIndex(int x)
        {
            if (labels.Length == 0) return -1;
            int width = Math.Max(1, (Width - 4) / labels.Length);
            int index = (x - 2) / width;
            if (index < 0) index = 0;
            if (index >= labels.Length) index = labels.Length - 1;
            return index;
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            int index = HitIndex(e.X);
            if (index != hoverIndex)
            {
                hoverIndex = index;
                Invalidate();
            }
            base.OnMouseMove(e);
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            hoverIndex = -1;
            Invalidate();
            base.OnMouseLeave(e);
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            if (e.Button == MouseButtons.Left)
            {
                Focus();
                SelectedIndex = HitIndex(e.X);
            }
            base.OnMouseDown(e);
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Left) { SelectedIndex--; e.Handled = true; return; }
            if (e.KeyCode == Keys.Right) { SelectedIndex++; e.Handled = true; return; }
            base.OnKeyDown(e);
        }

        protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
        protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            if (labels.Length == 0) return;

            Rectangle track = new Rectangle(1, 1, Width - 2, Height - 2);
            using (GraphicsPath path = RoundedPanel.RoundRect(track, Height / 2))
            {
                using (SolidBrush brush = new SolidBrush(Color.FromArgb(236, 233, 224)))
                    e.Graphics.FillPath(brush, path);
                using (Pen pen = new Pen(Color.FromArgb(227, 223, 212)))
                    e.Graphics.DrawPath(pen, path);
            }

            int segment = (Width - 4) / labels.Length;
            for (int i = 0; i < labels.Length; i++)
            {
                Rectangle bounds = new Rectangle(2 + i * segment, 2, segment, Height - 4);
                bool selected = i == selectedIndex;
                if (selected)
                {
                    using (GraphicsPath path = RoundedPanel.RoundRect(bounds, Height / 2 - 2))
                    {
                        using (SolidBrush brush = new SolidBrush(Color.White))
                            e.Graphics.FillPath(brush, path);
                        using (Pen pen = new Pen(Palette.Line))
                            e.Graphics.DrawPath(pen, path);
                    }
                }
                Color fore = selected ? Palette.Primary
                    : i == hoverIndex ? Palette.Text
                    : Palette.Muted;

                string label = labels[i] ?? "";
                Size textSize = TextRenderer.MeasureText(label, Font,
                    new Size(Int32.MaxValue, Int32.MaxValue), TextFormatFlags.NoPadding);
                UiIcon icon = i < icons.Length ? icons[i] : UiIcon.None;
                int iconSize = icon == UiIcon.None ? 0 : 13;
                int gap = icon == UiIcon.None ? 0 : 6;
                int total = iconSize + gap + textSize.Width;
                int startX = bounds.X + Math.Max(2, (bounds.Width - total) / 2);
                if (icon != UiIcon.None)
                {
                    IconPainter.Draw(e.Graphics, icon,
                        new Rectangle(startX, bounds.Y + (bounds.Height - iconSize) / 2,
                            iconSize, iconSize), fore, 1.6F);
                    startX += iconSize + gap;
                }
                Rectangle textRect = new Rectangle(startX, bounds.Y,
                    textSize.Width + 2, bounds.Height);
                TextRenderer.DrawText(e.Graphics, label, Font, textRect, fore,
                    TextFormatFlags.Left | TextFormatFlags.VerticalCenter |
                    TextFormatFlags.SingleLine | TextFormatFlags.NoPadding);
            }

            if (Focused)
            {
                using (GraphicsPath path = RoundedPanel.RoundRect(
                    new Rectangle(1, 1, Width - 2, Height - 2), Height / 2))
                using (Pen pen = new Pen(Color.FromArgb(90, Palette.Primary), 1F))
                    e.Graphics.DrawPath(pen, path);
            }
        }
    }

    internal sealed class BrandMark : Control
    {
        public BrandMark()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            Size = new Size(44, 44);
            BackColor = Palette.TitleBar;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            float scale = Math.Min(Width, Height) / 44F;
            e.Graphics.ScaleTransform(scale, scale);
            Rectangle rect = new Rectangle(1, 1, 42, 42);
            using (GraphicsPath path = RoundedPanel.RoundRect(rect, 10))
            {
                using (LinearGradientBrush brush = new LinearGradientBrush(
                    rect, Palette.Primary600, Palette.PrimaryDeep, 45F))
                    e.Graphics.FillPath(brush, path);
            }

            Point[] left = new Point[]
            {
                new Point(9, 16), new Point(21, 19),
                new Point(21, 36), new Point(9, 33)
            };
            Point[] right = new Point[]
            {
                new Point(35, 16), new Point(23, 19),
                new Point(23, 36), new Point(35, 33)
            };
            using (SolidBrush white = new SolidBrush(Color.White))
            {
                e.Graphics.FillPolygon(white, left);
                e.Graphics.FillPolygon(white, right);
            }
            using (Pen line = new Pen(Color.FromArgb(190, Palette.Accent), 1.5F))
            {
                e.Graphics.DrawLine(line, 12, 23, 19, 24.5F);
                e.Graphics.DrawLine(line, 12, 27, 19, 28.5F);
                e.Graphics.DrawLine(line, 25, 24.5F, 32, 23);
                e.Graphics.DrawLine(line, 25, 28.5F, 32, 27);
            }
            using (Pen spine = new Pen(Color.FromArgb(235, 255, 255, 255), 1.5F))
                e.Graphics.DrawLine(spine, 22, 19, 22, 36);
        }
    }
}
