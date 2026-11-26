//MCCScript 1.0
MCC.LoadBot(new DonutBotController());

//MCCScript Extensions
using System;
using System.IO;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using MinecraftClient.ChatBots;
using MinecraftClient.CommandHandler;
using MinecraftClient.Mapping;

public class DonutBotController : ChatBot
{
    private string stateFilePath;
    private string whitelistFilePath;
    private DateTime lastJumpTime = DateTime.MinValue;
    private DateTime lastClickTime = DateTime.MinValue;
    private DateTime lastStateCheck = DateTime.MinValue;

    private int jumpIntervalSec = 0;
    private int clickIntervalSec = 0;
    private string clickType = "left";
    private bool autoTpAccept = false;
    private HashSet<string> tpWhitelist = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

    public override void Initialize()
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        stateFilePath = Path.Combine(Environment.CurrentDirectory, "bot_state.json");
        whitelistFilePath = Path.Combine(Environment.CurrentDirectory, "tp_whitelist.txt");
        ReloadConfig();
        EmitEvent("status", "system", "ChatBot initialized");
    }

    private void ReloadConfig()
    {
        try
        {
            if (File.Exists(stateFilePath))
            {
                string json = File.ReadAllText(stateFilePath);
                jumpIntervalSec = ExtractInt(json, "jump_interval", 0);
                clickIntervalSec = ExtractInt(json, "click_interval", 0);
                clickType = ExtractString(json, "click_type", "left");
                autoTpAccept = ExtractBool(json, "auto_tp_accept", false);
            }
            if (File.Exists(whitelistFilePath))
            {
                var lines = File.ReadAllLines(whitelistFilePath);
                var next = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (var l in lines)
                {
                    var trimmed = l.Trim();
                    if (!string.IsNullOrEmpty(trimmed)) next.Add(trimmed);
                }
                tpWhitelist = next;
            }
        }
        catch { }
    }

    public override void Update()
    {
        DateTime now = DateTime.UtcNow;

        if ((now - lastStateCheck).TotalSeconds >= 2)
        {
            lastStateCheck = now;
            ReloadConfig();
        }

        if (jumpIntervalSec > 0 && (now - lastJumpTime).TotalSeconds >= jumpIntervalSec)
        {
            lastJumpTime = now;
            DoJump();
        }

        if (clickIntervalSec > 0 && (now - lastClickTime).TotalSeconds >= clickIntervalSec)
        {
            lastClickTime = now;
            DoClick(clickType);
        }
    }

    public override void GetText(string text)
    {
        string clean = GetVerbatim(text);
        if (string.IsNullOrWhiteSpace(clean)) return;

        string sender = "";
        string message = "";

        // 1. Check standard MCC private message detection
        if (IsPrivateMessage(clean, ref message, ref sender))
        {
            EmitEvent("private", sender, message);
            return;
        }

        // 2. DonutSMP / Essentials / Velocity whisper patterns: e.g. "[Player -> You] msg" or "Player whispers: msg"
        var msgMatch = Regex.Match(clean, @"^(?:\[)?([A-Za-z0-9_]{3,16})\s*(?:->|»)\s*(?:me|You|[A-Za-z0-9_]{3,16})(?:\])?:?\s*(.+)$", RegexOptions.IgnoreCase);
        if (msgMatch.Success)
        {
            EmitEvent("private", msgMatch.Groups[1].Value, msgMatch.Groups[2].Value);
            return;
        }

        // 3. TP Request detection
        var tpMatch = Regex.Match(clean, @"([A-Za-z0-9_]{3,16})\s+has requested (?:to teleport to you|that you teleport to them)", RegexOptions.IgnoreCase);
        if (tpMatch.Success)
        {
            string requester = tpMatch.Groups[1].Value;
            EmitEvent("tp_request", requester, clean);

            if (autoTpAccept && tpWhitelist.Contains(requester))
            {
                SendText("/tpaccept");
                EmitEvent("tp_accepted", requester, "Auto-accepted TP from whitelisted user: " + requester);
            }
            return;
        }

        // 4. Nearby / Local chat detection (if server tags [Local] or nearby player speaks without [Global])
        if (clean.StartsWith("[Local]", StringComparison.OrdinalIgnoreCase) ||
            clean.StartsWith("[L]", StringComparison.OrdinalIgnoreCase))
        {
            EmitEvent("nearby", "local", clean);
            return;
        }

        // Check if message mentions a nearby player currently in entity list
        try
        {
            var entities = GetEntities();
            foreach (var kv in entities)
            {
                if (!string.IsNullOrEmpty(kv.Value.Name) && (clean.Contains(kv.Value.Name + ":") || clean.Contains("<" + kv.Value.Name + ">")))
                {
                    EmitEvent("nearby", kv.Value.Name, clean);
                    return;
                }
            }
        }
        catch { }
    }

    public override void OnConnectionLost(DisconnectReason reason, string message)
    {
        EmitEvent("disconnected", reason.ToString(), message ?? "");
    }

    private void DoJump()
    {
        try
        {
            Location loc = GetCurrentLocation();
            Location up = new Location(loc.X, loc.Y + 1.0, loc.Z);
            MoveToLocation(up);
        }
        catch { }
    }

    private void DoClick(string type)
    {
        try
        {
            CmdResult res = new CmdResult();
            if (string.Equals(type, "right", StringComparison.OrdinalIgnoreCase))
            {
                PerformInternalCommand("useitem", ref res);
            }
            else
            {
                // Left click: swing arm / attack nearest or air
                PerformInternalCommand("entity * attack", ref res);
            }
        }
        catch { }
    }

    private void EmitEvent(string eventType, string sender, string message)
    {
        string safeSender = EscapeJson(sender ?? "");
        string safeMsg = EscapeJson(message ?? "");
        string safeType = EscapeJson(eventType ?? "info");
        Console.WriteLine($"[DONUT_EVENT]{{\"type\":\"{safeType}\",\"sender\":\"{safeSender}\",\"message\":\"{safeMsg}\"}}");
    }

    private static string EscapeJson(string s)
    {
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "").Replace("\n", " ");
    }

    private static int ExtractInt(string json, string key, int def)
    {
        var m = Regex.Match(json, $"\"{key}\"\\s*:\\s*(\\d+)");
        return m.Success && int.TryParse(m.Groups[1].Value, out int v) ? v : def;
    }

    private static bool ExtractBool(string json, string key, bool def)
    {
        var m = Regex.Match(json, $"\"{key}\"\\s*:\\s*(true|false)", RegexOptions.IgnoreCase);
        return m.Success ? m.Groups[1].Value.Equals("true", StringComparison.OrdinalIgnoreCase) : def;
    }

    private static string ExtractString(string json, string key, string def)
    {
        var m = Regex.Match(json, $"\"{key}\"\\s*:\\s*\"([^\"]*)\"");
        return m.Success ? m.Groups[1].Value : def;
    }
}
