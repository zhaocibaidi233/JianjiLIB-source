using System;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using JianjiLauncher;

internal static class ConfigTests
{
    private static int failures;

    private static void Check(string name, bool actual, bool expected)
    {
        if (actual == expected)
        {
            Console.WriteLine("ok   " + name);
            return;
        }
        failures++;
        Console.WriteLine("FAIL " + name + ": expected " + expected + ", got " + actual);
    }

    private static void CheckText(string name, string actual, string expected)
    {
        if (actual == expected)
        {
            Console.WriteLine("ok   " + name);
            return;
        }
        failures++;
        Console.WriteLine("FAIL " + name + ": expected [" + expected + "], got [" + actual + "]");
    }

    private static Dictionary<string, object> Parse(string json)
    {
        return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
    }

    public static int Main()
    {
        Check("json bool true / lan_access",
            LauncherConfig.ReadBool(Parse("{\"lan_access\":true}"), "lan_access", false), true);
        Check("json bool true / db_configured",
            LauncherConfig.ReadBool(Parse("{\"db_configured\":true}"), "db_configured", false), true);
        Check("json bool false",
            LauncherConfig.ReadBool(Parse("{\"lan_access\":false}"), "lan_access", true), false);
        Check("string true",
            LauncherConfig.ReadBool(Parse("{\"lan_access\":\"true\"}"), "lan_access", false), true);
        Check("string True",
            LauncherConfig.ReadBool(Parse("{\"lan_access\":\"True\"}"), "lan_access", false), true);
        Check("string false",
            LauncherConfig.ReadBool(Parse("{\"lan_access\":\"false\"}"), "lan_access", true), false);
        Check("missing key uses fallback",
            LauncherConfig.ReadBool(Parse("{}"), "lan_access", true), true);
        Check("null uses fallback",
            LauncherConfig.ReadBool(Parse("{\"lan_access\":null}"), "lan_access", true), true);
        CheckText("health db label",
            LauncherConfig.HealthDbLabel(
                "{\"db\":true,\"db_host\":\"127.0.0.1\",\"db_port\":5432,\"db_name\":\"demo_db\"}"),
            "127.0.0.1:5432/demo_db");
        CheckText("health db label missing name",
            LauncherConfig.HealthDbLabel("{\"db\":true}"), "");
        CheckText("health db label malformed",
            LauncherConfig.HealthDbLabel("not-json"), "");
        // 库结构落后提示（2026-10-26 真机事故）：老后端没有 schema_ok 字段 → 视为正常
        Check("schema ok json false",
            LauncherConfig.HealthSchemaOk("{\"schema_ok\":false}"), false);
        Check("schema ok json true",
            LauncherConfig.HealthSchemaOk("{\"schema_ok\":true}"), true);
        Check("schema ok missing field is fine",
            LauncherConfig.HealthSchemaOk("{\"db\":true}"), true);
        Check("schema ok malformed is fine",
            LauncherConfig.HealthSchemaOk("not-json"), true);
        CheckText("schema head",
            LauncherConfig.HealthSchemaHead("{\"schema_head\":\"e2b6c4d8f1a3\"}"),
            "e2b6c4d8f1a3");
        CheckText("schema head missing",
            LauncherConfig.HealthSchemaHead("{\"db\":true}"), "");

        CheckText("db mode default portable",
            LauncherConfig.ReadDbMode(Parse("{}"), false), "portable");
        CheckText("db mode env external",
            LauncherConfig.ReadDbMode(Parse("{}"), true), "external");
        CheckText("db mode old config external",
            LauncherConfig.ReadDbMode(Parse("{\"db_configured\":true}"), false), "external");
        CheckText("db mode explicit portable wins",
            LauncherConfig.ReadDbMode(Parse("{\"db_mode\":\"portable\",\"db_configured\":true}"), true),
            "portable");
        Check("integer fallback",
            LauncherConfig.ReadInt(Parse("{}"), "pg_port", 55432) == 55432, true);
        Check("integer value",
            LauncherConfig.ReadInt(Parse("{\"pg_port\":6000}"), "pg_port", 55432) == 6000, true);
        CheckText("portable data dir default",
            LauncherConfig.ResolvePortableDataDir("D:\\Jianji", ""),
            "D:\\Jianji\\data\\pgdata");
        CheckText("portable data dir rooted",
            LauncherConfig.ResolvePortableDataDir("D:\\Jianji", "E:\\libdata"),
            "E:\\libdata");
        CheckText("portable url escapes password",
            LauncherConfig.PortableDbUrl(55432, "a:b@c"),
            "postgresql+psycopg2://postgres:a%3Ab%40c@127.0.0.1:55432/jianji");

        Console.WriteLine(failures == 0 ? "CONFIG-TESTS-OK" : "CONFIG-TESTS-FAILED");
        return failures == 0 ? 0 : 1;
    }
}
