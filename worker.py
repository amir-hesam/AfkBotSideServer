import asyncio
import json
import os
import re
import shutil
import subprocess
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

API_KEY = os.getenv("API_KEY", "change-me")
CPANEL_WEBHOOK_URL = os.getenv("CPANEL_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
MCC_DIR = Path(os.getenv("MCC_DIR", "/opt/donut-afk/mcc"))
INSTANCES_DIR = Path(os.getenv("INSTANCES_DIR", "/opt/donut-afk/instances"))
SCRIPT_TEMPLATE = Path(os.getenv("SCRIPT_TEMPLATE", "/opt/donut-afk/DonutBotController.cs"))
SERVER_HOST = os.getenv("SERVER_HOST", "donutsmp.net")

INSTANCES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="DonutSMP MCC Worker API", version="1.0.0")


def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def find_mcc_binary() -> str:
    for name in ("MinecraftClient", "Mcc.Cli", "MinecraftClient.exe"):
        candidate = MCC_DIR / name
        if candidate.exists():
            return str(candidate)
    if MCC_DIR.exists():
        for item in MCC_DIR.iterdir():
            if item.is_file() and item.suffix not in (".sh", ".ini", ".json", ".txt", ".dll", ".so") and os.access(item, os.X_OK):
                return str(item)
    raise HTTPException(status_code=500, detail=f"MCC binary not found in {MCC_DIR}")


class ProxyConfig(BaseModel):
    enabled: bool = False
    type: str = "SOCKS5"  # SOCKS5 or HTTP
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""


class StartBotRequest(BaseModel):
    mc_username: str = "-"
    proxy: Optional[ProxyConfig] = None
    auto_tp_accept: bool = False
    jump_interval: int = 0
    click_interval: int = 0
    click_type: str = "left"
    tp_whitelist: List[str] = []


class ActionRequest(BaseModel):
    action: str  # move | jump | click | command | set_loop | update_whitelist | rtp_and_stop
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    click_type: str = "left"
    command: Optional[str] = None
    auto_tp_accept: Optional[bool] = None
    jump_interval: Optional[int] = None
    click_interval: Optional[int] = None
    tp_whitelist: Optional[List[str]] = None


class BotProcess:
    def __init__(self, account_id: int):
        self.account_id = account_id
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.status: str = "offline"  # offline | auth_pending | connecting | online | reconnecting
        self.device_code: Optional[str] = None
        self.device_url: Optional[str] = None
        self.logs: deque = deque(maxlen=80)
        self.manual_stop: bool = False
        self.last_config: Optional[StartBotRequest] = None
        self.reader_task: Optional[asyncio.Task] = None
        self.reconnect_task: Optional[asyncio.Task] = None

    @property
    def work_dir(self) -> Path:
        d = INSTANCES_DIR / str(self.account_id)
        d.mkdir(parents=True, exist_ok=True)
        return d


BOTS: Dict[int, BotProcess] = {}


def get_bot(account_id: int) -> BotProcess:
    if account_id not in BOTS:
        BOTS[account_id] = BotProcess(account_id)
    return BOTS[account_id]


async def send_webhook(payload: dict):
    if not CPANEL_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(
                CPANEL_WEBHOOK_URL,
                json=payload,
                headers={"X-Webhook-Secret": WEBHOOK_SECRET},
            )
    except Exception:
        pass


def write_instance_files(bot: BotProcess, req: StartBotRequest):
    d = bot.work_dir
    if SCRIPT_TEMPLATE.exists():
        shutil.copy2(SCRIPT_TEMPLATE, d / "DonutBotController.cs")

    # Write bot_state.json
    state = {
        "jump_interval": req.jump_interval,
        "click_interval": req.click_interval,
        "click_type": req.click_type,
        "auto_tp_accept": req.auto_tp_accept,
    }
    (d / "bot_state.json").write_text(json.dumps(state), encoding="utf-8")
    (d / "tp_whitelist.txt").write_text("\n".join(req.tp_whitelist), encoding="utf-8")

    # Write MinecraftClient.ini
    proxy = req.proxy or ProxyConfig()
    proxy_enabled = "true" if proxy.enabled and proxy.host and proxy.port else "false"
    login_name = req.mc_username if (req.mc_username and req.mc_username != "-") else f"acc_{bot.account_id}"
    ini_content = f"""[Main]
[Main.General]
Account = {{ Login = "{toml_escape(login_name)}", Password = "" }}
Server = {{ Host = "{toml_escape(SERVER_HOST)}" }}
AccountType = "microsoft"
Method = "mcc"

[Main.Advanced]
EnableSentry = false
Language = "en_us"
BotOwners = []
InternalCmdChar = "slash"
TerrainAndMovements = true
EntityHandling = true
InventoryHandling = true
AutoRespawn = true
ExitOnFailure = true

[Main.Proxy]
Enabled = {proxy_enabled}
ProxyType = "{toml_escape(proxy.type)}"
Server = {{ Host = "{toml_escape(proxy.host)}", Port = {proxy.port} }}
Username = "{toml_escape(proxy.username)}"
Password = "{toml_escape(proxy.password)}"

[ChatBot.ScriptScheduler]
Enabled = true
Tasks = [
    {{ TriggerOnLogin = true, Script = "DonutBotController.cs" }}
]
"""
    (d / "MinecraftClient.ini").write_text(ini_content, encoding="utf-8")


async def send_stdin(bot: BotProcess, line: str):
    if not bot.proc or bot.proc.stdin is None or bot.proc.returncode is not None:
        raise HTTPException(status_code=400, detail="Bot is not running")
    bot.proc.stdin.write((line.strip() + "\n").encode("utf-8"))
    await bot.proc.stdin.drain()


async def read_mcc_output(bot: BotProcess):
    assert bot.proc and bot.proc.stdout
    buf = ""
    sent_login_choice = False

    async def process_line(line: str):
        nonlocal sent_login_choice
        if not line:
            return
        bot.logs.append(line)

        # 0. Auto-answer MCC interactive login method prompt
        if not sent_login_choice and "Select a login method" in line:
            sent_login_choice = True
            try:
                await send_stdin(bot, "2")
            except Exception:
                pass
            return

        # 1. Microsoft Device Code / OAuth URL detection
        if "microsoft.com/link" in line or "devicelogin" in line or "login.microsoftonline.com" in line:
            url_m = re.search(r"(https://[^\s]+)", line)
            if url_m:
                bot.device_url = url_m.group(1)
                bot.status = "auth_pending"

        code_m = re.search(r"(?:enter the code|user code|code)[:\s]+([A-Z0-9]{6,10})", line)
        if code_m:
            bot.device_code = code_m.group(1)
            bot.status = "auth_pending"
            asyncio.create_task(
                send_webhook(
                    {
                        "account_id": bot.account_id,
                        "event": "device_code",
                        "device_url": bot.device_url or "https://www.microsoft.com/link",
                        "device_code": bot.device_code,
                    }
                )
            )
        elif bot.device_url and "login.microsoftonline.com" in bot.device_url and not bot.device_code:
            bot.device_code = "OPEN-LINK"
            asyncio.create_task(
                send_webhook(
                    {
                        "account_id": bot.account_id,
                        "event": "device_code",
                        "device_url": bot.device_url,
                        "device_code": bot.device_code,
                    }
                )
            )

        # 2. Online detection
        if "Server was successfully joined" in line or "You have joined" in line or "ChatBot initialized" in line:
            bot.status = "online"
            bot.device_code = None
            bot.device_url = None
            asyncio.create_task(
                send_webhook({"account_id": bot.account_id, "event": "status", "status": "online"})
            )

        # 3. Structured ChatBot events
        if "[DONUT_EVENT]" in line:
            idx = line.find("[DONUT_EVENT]") + len("[DONUT_EVENT]")
            try:
                ev = json.loads(line[idx:])
                asyncio.create_task(
                    send_webhook(
                        {
                            "account_id": bot.account_id,
                            "event": "chat",
                            "chat_type": ev.get("type", "info"),
                            "sender": ev.get("sender", ""),
                            "message": ev.get("message", ""),
                        }
                    )
                )
            except json.JSONDecodeError:
                pass

    try:
        while True:
            chunk = await bot.proc.stdout.read(1024)
            if not chunk:
                if buf.strip():
                    await process_line(strip_ansi(buf).strip())
                break
            buf += chunk.decode("utf-8", errors="replace")

            # Process complete lines (\r or \n) AND check partial buffer for prompts without newline
            parts = re.split(r"[\r\n]+", buf)
            buf = parts.pop()
            for p in parts:
                await process_line(strip_ansi(p).strip())

            clean_tail = strip_ansi(buf).strip()
            if clean_tail and (
                "Select a login method" in clean_tail
                or re.search(r"enter the code[:\s]+[A-Z0-9]{6,10}", clean_tail)
            ):
                await process_line(clean_tail)
                buf = ""

        rc = await bot.proc.wait()
        if not bot.manual_stop:
            bot.status = "reconnecting"
            asyncio.create_task(
                send_webhook(
                    {
                        "account_id": bot.account_id,
                        "event": "status",
                        "status": "reconnecting",
                        "message": f"Disconnected (exit {rc}), reconnecting in 10s",
                    }
                )
            )
            bot.reconnect_task = asyncio.create_task(auto_reconnect(bot))
        else:
            bot.status = "offline"
            asyncio.create_task(
                send_webhook({"account_id": bot.account_id, "event": "status", "status": "offline"})
            )
    except asyncio.CancelledError:
        pass


async def auto_reconnect(bot: BotProcess):
    await asyncio.sleep(10)
    if not bot.manual_stop and bot.last_config is not None:
        await start_mcc_process(bot, bot.last_config)


async def start_mcc_process(bot: BotProcess, req: StartBotRequest):
    if bot.reconnect_task and not bot.reconnect_task.done():
        bot.reconnect_task.cancel()

    if bot.proc and bot.proc.returncode is None:
        bot.manual_stop = True
        bot.proc.terminate()
        try:
            await asyncio.wait_for(bot.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            bot.proc.kill()

    if bot.reader_task and not bot.reader_task.done():
        bot.reader_task.cancel()
        try:
            await bot.reader_task
        except asyncio.CancelledError:
            pass

    bot.manual_stop = False
    bot.last_config = req
    bot.status = "connecting"
    bot.device_code = None
    bot.device_url = None
    write_instance_files(bot, req)

    mcc_bin = find_mcc_binary()
    bot.proc = await asyncio.create_subprocess_exec(
        mcc_bin,
        str(bot.work_dir / "MinecraftClient.ini"),
        cwd=str(bot.work_dir),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    bot.reader_task = asyncio.create_task(read_mcc_output(bot))


@app.get("/health", dependencies=[Depends(verify_api_key)])
async def health():
    mem_total = mem_avail = swap_total = swap_free = 0
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1]) // 1024
                elif line.startswith("SwapTotal:"):
                    swap_total = int(line.split()[1]) // 1024
                elif line.startswith("SwapFree:"):
                    swap_free = int(line.split()[1]) // 1024
    except Exception:
        pass

    active_count = sum(1 for b in BOTS.values() if b.status in ("online", "connecting", "auth_pending", "reconnecting"))
    return {
        "ok": True,
        "mem_total_mb": mem_total,
        "mem_used_mb": max(0, mem_total - mem_avail),
        "swap_total_mb": swap_total,
        "swap_used_mb": max(0, swap_total - swap_free),
        "active_bots": active_count,
    }


@app.post("/bots/{account_id}/start", dependencies=[Depends(verify_api_key)])
async def start_bot(account_id: int, req: StartBotRequest):
    bot = get_bot(account_id)
    await start_mcc_process(bot, req)
    return {"ok": True, "status": bot.status}


@app.post("/bots/{account_id}/stop", dependencies=[Depends(verify_api_key)])
async def stop_bot(account_id: int):
    bot = get_bot(account_id)
    bot.manual_stop = True
    if bot.reconnect_task and not bot.reconnect_task.done():
        bot.reconnect_task.cancel()
    if bot.proc and bot.proc.returncode is None:
        try:
            await send_stdin(bot, "/quit")
        except Exception:
            pass
        try:
            await asyncio.wait_for(bot.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            bot.proc.terminate()
    bot.status = "offline"
    return {"ok": True, "status": "offline"}


@app.get("/bots/{account_id}/status", dependencies=[Depends(verify_api_key)])
async def bot_status(account_id: int):
    bot = get_bot(account_id)
    ram_mb = 0
    if bot.proc and bot.proc.returncode is None:
        try:
            with open(f"/proc/{bot.proc.pid}/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        ram_mb = int(line.split()[1]) // 1024
                        break
        except Exception:
            pass
    return {
        "ok": True,
        "account_id": account_id,
        "status": bot.status,
        "device_url": bot.device_url,
        "device_code": bot.device_code,
        "ram_mb": ram_mb,
        "logs": list(bot.logs)[-20:],
    }


@app.post("/bots/{account_id}/action", dependencies=[Depends(verify_api_key)])
async def bot_action(account_id: int, req: ActionRequest):
    bot = get_bot(account_id)
    if req.action == "move":
        if req.x is None or req.y is None or req.z is None:
            raise HTTPException(status_code=400, detail="x, y, z required")
        await send_stdin(bot, f"/move {req.x} {req.y} {req.z}")
    elif req.action == "jump":
        # One-shot jump via MCC move up 1 block
        await send_stdin(bot, "/move up")
    elif req.action == "click":
        if req.click_type == "right":
            await send_stdin(bot, "/useitem")
        else:
            await send_stdin(bot, "/entity * attack")
    elif req.action == "command":
        if not req.command:
            raise HTTPException(status_code=400, detail="command required")
        cmd = req.command if req.command.startswith("/") else "/" + req.command
        await send_stdin(bot, f"/send {cmd}")
    elif req.action == "set_loop":
        state_file = bot.work_dir / "bot_state.json"
        current = {}
        if state_file.exists():
            current = json.loads(state_file.read_text(encoding="utf-8"))
        if req.jump_interval is not None:
            current["jump_interval"] = req.jump_interval
        if req.click_interval is not None:
            current["click_interval"] = req.click_interval
        if req.click_type:
            current["click_type"] = req.click_type
        if req.auto_tp_accept is not None:
            current["auto_tp_accept"] = req.auto_tp_accept
        state_file.write_text(json.dumps(current), encoding="utf-8")
        if bot.last_config:
            bot.last_config.jump_interval = current.get("jump_interval", 0)
            bot.last_config.click_interval = current.get("click_interval", 0)
            bot.last_config.click_type = current.get("click_type", "left")
            bot.last_config.auto_tp_accept = current.get("auto_tp_accept", False)
    elif req.action == "update_whitelist":
        wl = req.tp_whitelist or []
        (bot.work_dir / "tp_whitelist.txt").write_text("\n".join(wl), encoding="utf-8")
        if bot.last_config:
            bot.last_config.tp_whitelist = wl
    elif req.action == "rtp_and_stop":
        # Grace period expired: run /rtp if online, wait 4s, then disconnect cleanly
        if bot.proc and bot.proc.returncode is None:
            try:
                await send_stdin(bot, "/send /rtp")
                await asyncio.sleep(4)
            except Exception:
                pass
        await stop_bot(account_id)
    else:
        raise HTTPException(status_code=400, detail="Unknown action")

    return {"ok": True}
