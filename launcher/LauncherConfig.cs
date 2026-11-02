using System;
using System.Collections.Generic;
using System.IO;
using System.Web.Script.Serialization;

namespace JianjiLauncher
{
    internal static class LauncherConfig
    {
        public static bool ReadBool(Dictionary<string, object> cfg, string key, bool fallback)
        {
            if (cfg == null || !cfg.ContainsKey(key)) return fallback;
            object value = cfg[key];
            if (value == null) return fallback;
            if (value is bool) return (bool)value;
            string text = Convert.ToString(value);
            if (String.IsNullOrEmpty(text)) return fallback;
            bool parsed;
            return Boolean.TryParse(text, out parsed) ? parsed : fallback;
        }

        public static int ReadInt(Dictionary<string, object> cfg, string key,
            int fallback)
        {
            if (cfg == null || !cfg.ContainsKey(key)) return fallback;
            int value;
            return Int32.TryParse(Convert.ToString(cfg[key]), out value)
                ? value : fallback;
        }

        public static string ReadDbMode(Dictionary<string, object> cfg,
            bool hasExternalEnv)
        {
            if (cfg != null)
            {
                string mode = cfg.ContainsKey("db_mode")
                    ? Convert.ToString(cfg["db_mode"]) : "";
                if (String.Equals(mode, "portable", StringComparison.OrdinalIgnoreCase))
                    return "portable";
                if (String.Equals(mode, "external", StringComparison.OrdinalIgnoreCase))
                    return "external";
                if (cfg.ContainsKey("db_host") ||
                    ReadBool(cfg, "db_configured", false))
                    return "external";
            }
            return hasExternalEnv ? "external" : "portable";
        }

        public static string ResolvePortableDataDir(string root, string raw)
        {
            string value = String.IsNullOrWhiteSpace(raw)
                ? Path.Combine("data", "pgdata") : raw.Trim();
            return Path.GetFullPath(Path.IsPathRooted(value)
                ? value : Path.Combine(root, value));
        }

        public static string PortableDbUrl(int port, string password)
        {
            return "postgresql+psycopg2://postgres:"
                + Uri.EscapeDataString(password ?? "")
                + "@127.0.0.1:" + port + "/jianji";
        }

        public static string HealthDbLabel(string json)
        {
            if (String.IsNullOrEmpty(json)) return "";
            try
            {
                Dictionary<string, object> cfg =
                    new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                if (cfg == null || !cfg.ContainsKey("db_name")) return "";
                string name = Convert.ToString(cfg["db_name"]);
                if (String.IsNullOrEmpty(name)) return "";
                string host = cfg.ContainsKey("db_host")
                    ? Convert.ToString(cfg["db_host"]) : "";
                string port = cfg.ContainsKey("db_port")
                    ? Convert.ToString(cfg["db_port"]) : "";
                if (!String.IsNullOrEmpty(host) && !String.IsNullOrEmpty(port))
                    return host + ":" + port + "/" + name;
                return name;
            }
            catch
            {
                return "";
            }
        }

        /// <summary>
        /// 库结构是否落后于代码（`/api/health` 的 `schema_ok`）。
        ///
        /// 2026-10-26 真机踩过：代码升级了、库没跑迁移，应用照常启动、页面能开，
        /// 只有碰新列的接口 500（工作台/馆藏/借还全挂）。启动器是现场唯一入口，
        /// 所以把结论摆到界面上，而不是等人去日志里找 `UndefinedColumn`。
        ///
        /// 老版本后端没有这个字段 → 返回 true（不提示），避免误报。
        /// </summary>
        public static bool HealthSchemaOk(string json)
        {
            if (String.IsNullOrEmpty(json)) return true;
            try
            {
                Dictionary<string, object> cfg =
                    new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                if (cfg == null || !cfg.ContainsKey("schema_ok")) return true;
                object raw = cfg["schema_ok"];
                if (raw is bool) return (bool)raw;
                return !String.Equals(Convert.ToString(raw), "false",
                                      StringComparison.OrdinalIgnoreCase);
            }
            catch
            {
                return true;
            }
        }

        /// <summary>代码要求的迁移版本（落后时显示在提示里，便于现场描述问题）。</summary>
        public static string HealthSchemaHead(string json)
        {
            if (String.IsNullOrEmpty(json)) return "";
            try
            {
                Dictionary<string, object> cfg =
                    new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                if (cfg == null || !cfg.ContainsKey("schema_head")) return "";
                return Convert.ToString(cfg["schema_head"]) ?? "";
            }
            catch
            {
                return "";
            }
        }
    }
}
