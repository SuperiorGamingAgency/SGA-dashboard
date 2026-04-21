import os
import sys
import json
import ctypes
import time
import uuid
import psutil
import platform
import datetime
import winreg
import subprocess
import pythoncom
import win32com.client
import threading
import webbrowser
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from PIL import Image
import pystray
from supabase import create_client
from plyer import notification

# --- LOGGING SYSTEM CONFIGURATION ---
LOG_FILE = os.path.join(os.environ.get('APPDATA', '.'), "agent_uplink.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("SGA_Agent")

# --- SETTINGS ---
SUPABASE_URL = "https://hyakorvmqpirnorpbskk.supabase.co"
SUPABASE_KEY = "sb_publishable_zfssEvxed9ul69PEX8NU6A_ACNnrbrU"
DASHBOARD_URL = "http://localhost:4321"
SESSION_PATH = os.path.join(os.environ.get('APPDATA', '.'), "sga_agent_session.json")


class AuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query_components = parse_qs(urlparse(self.path).query)
        if "access" in query_components and "refresh" in query_components:
            self.server.access_token = query_components["access"][0]
            self.server.refresh_token = query_components["refresh"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='background:#020617;color:#f97316;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;'>")
            self.wfile.write(
                b"<div style='border:1px solid #f97316;padding:40px;border-radius:20px;background:#0f172a;text-align:center;'>")
            self.wfile.write(b"<h1 style='letter-spacing:-2px;'>HANDSHAKE SUCCESSFUL</h1>")
            self.wfile.write(
                b"<p style='color:#64748b;text-transform:uppercase;font-size:12px;letter-spacing:2px;'>SGA Agent Authorized. You may close this tab.</p></div>")
            self.wfile.write(b"</body></html>")
            logger.info("Handshake received from browser.")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        return


def hide_console():
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd != 0: ctypes.windll.user32.ShowWindow(hwnd, 0)


def show_console():
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd != 0:
        ctypes.windll.user32.ShowWindow(hwnd, 5)
        ctypes.windll.user32.SetForegroundWindow(hwnd)


def check_admin():
    if ctypes.windll.shell32.IsUserAnAdmin(): return True
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
    return False


class SGAAgent:
    def __init__(self):
        self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.rig_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(uuid.getnode())))
        self.user_id = None
        self.active_components = []
        self.stop_event = threading.Event()
        logger.info(f"Agent Initialized. Rig ID: {self.rig_id}")

    def _get_real_cpu_temp(self):
        try:
            pythoncom.CoInitialize()
            wmi = win32com.client.GetObject("winmgmts:/root/wmi")
            res = wmi.ExecQuery("SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
            for item in res:
                temp_c = (item.CurrentTemperature - 2732) / 10.0
                if 10 < temp_c < 110: return round(temp_c, 1)
        except:
            pass
        return round(35.0 + (psutil.cpu_percent() * 0.45), 1)

    def secure_login(self):
        if os.path.exists(SESSION_PATH):
            try:
                with open(SESSION_PATH, "r") as f:
                    sess = json.load(f)
                res = self.supabase.auth.set_session(sess['access_token'], sess['refresh_token'])
                self.user_id = res.user.id
                logger.info(f"Session restored for User: {self.user_id}")
                return True
            except:
                pass
        logger.info("Starting Bilateral Handshake Protocol...")
        server = HTTPServer(('localhost', 54321), AuthHandler)
        server.access_token = None
        webbrowser.open(f"{DASHBOARD_URL}/signin?returnTo=/?sync=true")
        server.handle_request()
        if server.access_token:
            try:
                res = self.supabase.auth.set_session(server.access_token, server.refresh_token)
                self.user_id = res.user.id
                with open(SESSION_PATH, "w") as f:
                    json.dump({"access_token": server.access_token, "refresh_token": server.refresh_token}, f)
                logger.info(f"Bilateral Handshake complete. User: {self.user_id}")
                return True
            except:
                return False
        return False

    def sync_hardware(self):
        logger.info("Synchronizing hardware manifest...")
        try:
            self.supabase.table("rigs").upsert({
                "id": self.rig_id, "user_id": self.user_id,
                "os_name": f"{platform.system()} {platform.release()}",
                "is_active": True, "last_ping": datetime.datetime.now(datetime.UTC).isoformat()
            }).execute()

            manifest = []
            # CPU
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu_model, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            manifest.append({"type": "CPU", "model_name": cpu_model.strip(), "slot_index": 0})

            # RAM
            total_ram = round(psutil.virtual_memory().total / (1024 ** 3))
            manifest.append({"type": "RAM", "model_name": f"{total_ram}GB System RAM", "slot_index": 0})

            # DISK
            for i, p in enumerate(psutil.disk_partitions()):
                if 'fixed' in p.opts:
                    try:
                        usage = psutil.disk_usage(p.mountpoint)
                        size_gb = round(usage.total / (1024 ** 3))
                        manifest.append(
                            {"type": "DISK", "model_name": f"Drive {p.mountpoint} ({size_gb}GB)", "slot_index": i,
                             "mount_point": p.mountpoint})
                    except:
                        continue

            # GPU (STABLE VRAM FIX)
            try:
                pythoncom.CoInitialize()
                wmi = win32com.client.Dispatch("WbemScripting.SWbemLocator").ConnectServer(".", "root\\cimv2")
                gpu_list = wmi.ExecQuery("Select * from Win32_VideoController")
                for i, controller in enumerate(gpu_list):
                    name = str(controller.Name).strip()
                    if "Microsoft Basic" in name: continue

                    vram_gb = "Shared"
                    if "NVIDIA" in name.upper():
                        try:
                            cmd = f"nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits --id={i}"
                            out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode().strip()
                            if out: vram_gb = f"{round(int(out) / 1024, 1)}GB"
                        except:
                            pass

                    # If not NVIDIA or nvidia-smi failed, try calculating from WMI
                    if vram_gb == "Shared":
                        try:
                            ram_val = int(controller.AdapterRAM)
                            if ram_val < 0: ram_val += 2 ** 32  # Correct 32-bit signed overflow
                            if ram_val > 0: vram_gb = f"{round(ram_val / (1024 ** 3), 1)}GB"
                        except:
                            pass

                    manifest.append(
                        {"type": "GPU", "model_name": f"{name} ({vram_gb})", "slot_index": i, "raw_name": name})
            except Exception as e:
                logger.error(f"GPU Sync Error: {e}")

            self.active_components = []
            for item in manifest:
                m_point = item.pop("mount_point", None)
                raw_n = item.pop("raw_name", item["model_name"])
                res = self.supabase.table("components").upsert({**item, "rig_id": self.rig_id},
                                                               on_conflict="rig_id,model_name,slot_index").execute()
                if res.data: self.active_components.append({**res.data[0], "mount_point": m_point, "raw_name": raw_n})
            logger.info(f"Manifest synced. {len(self.active_components)} components registered.")
        except Exception as e:
            logger.error(f"Critical Sync Error: {e}")

    def telemetry_stream(self):
        logger.info("Telemetry stream started.")
        while not self.stop_event.is_set():
            now = datetime.datetime.now(datetime.UTC).isoformat()
            payload = []
            cpu_usage = psutil.cpu_percent(interval=None)
            cpu_temp = self._get_real_cpu_temp()
            for comp in self.active_components:
                load, temp = 0, 0
                if comp['type'] == 'CPU':
                    load, temp = cpu_usage, cpu_temp
                elif comp['type'] == 'RAM':
                    load = psutil.virtual_memory().percent
                elif comp['type'] == 'DISK' and comp.get('mount_point'):
                    try:
                        load = psutil.disk_usage(comp['mount_point']).percent
                    except:
                        pass
                elif comp['type'] == 'GPU':
                    if "NVIDIA" in comp['raw_name'].upper():
                        try:
                            cmd = f"nvidia-smi --query-gpu=utilization.gpu,temperature.gpu --format=csv,noheader,nounits --id={comp['slot_index']}"
                            out = subprocess.check_output(cmd, shell=True,
                                                          stderr=subprocess.DEVNULL).decode().strip().split(',')
                            load, temp = float(out[0]), float(out[1])
                        except:
                            load, temp = cpu_usage * 0.5, cpu_temp
                    else:
                        load, temp = cpu_usage * 0.8, cpu_temp
                payload.append({"component_id": comp['id'], "user_id": self.user_id, "load_percent": int(round(load)),
                                "temp_celsius": int(round(temp)), "recorded_at": now})
            try:
                self.supabase.table("telemetry").insert(payload).execute()
                self.supabase.table("rigs").update({"is_active": True, "last_ping": now}).eq("id",
                                                                                             self.rig_id).execute()
            except Exception as e:
                logger.error(f"Telemetry uplink failed: {e}")
            time.sleep(5)


global_agent = None


def on_quit(icon, item):
    global global_agent
    if global_agent: global_agent.stop_event.set()
    icon.stop();
    os._exit(0)


if __name__ == "__main__":
    if not check_admin(): sys.exit(0)
    global_agent = SGAAgent()
    if global_agent.secure_login():
        global_agent.sync_hardware()
        hide_console()
        notification.notify(title="SGA UPLINK LIVE", message="Handshake verified. Hardware streaming active.",
                            timeout=5)
        threading.Thread(target=global_agent.telemetry_stream, daemon=True).start()
        try:
            img = Image.open("SGA_LOGO.ico")
        except:
            img = Image.new('RGB', (64, 64), (249, 115, 22))
        menu = pystray.Menu(pystray.MenuItem(f"Rig ID: {global_agent.rig_id[:8]}", lambda: None, enabled=False),
                            pystray.MenuItem("Show Console", show_console, default=True),
                            pystray.MenuItem("Exit SGA Agent", on_quit))
        icon = pystray.Icon("SGA_Agent", img, "SGA Uplink Agent", menu)
        icon.run()
    else:
        sys.exit()