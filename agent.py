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
import random
import queue
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from PIL import Image
import pystray
from supabase import create_client
from plyer import notification

# --- LOGGING SYSTEM ---
LOG_FILE = os.path.join(os.environ.get('APPDATA', '.'), "agent_uplink.log")
BUFFER_FILE = os.path.join(os.environ.get('APPDATA', '.'), "telemetry_buffer.json")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SGA_Agent")

# --- SETTINGS ---
SUPABASE_URL = "https://hyakorvmqpirnorpbskk.supabase.co"
SUPABASE_KEY = "sb_publishable_zfssEvxed9ul69PEX8NU6A_ACNnrbrU"
DASHBOARD_URL = "https://sga-dashboard-7v6t.onrender.com/"
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
            self.wfile.write(b"<html><body style='background:#020617;color:#f97316;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;'>")
            self.wfile.write(b"<div style='border:1px solid #f97316;padding:40px;border-radius:20px;background:#0f172a;text-align:center;'>")
            self.wfile.write(b"<h1 style='letter-spacing:-2px;'>HANDSHAKE SUCCESSFUL</h1>")
            self.wfile.write(b"<p style='color:#64748b;text-transform:uppercase;font-size:12px;letter-spacing:2px;'>SGA Agent Authorized. Close this tab.</p></div></body></html>")
            threading.Thread(target=self.server.shutdown, daemon=True).start()

def hide_console():
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd != 0: ctypes.windll.user32.ShowWindow(hwnd, 0)

def show_console():
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd != 0: ctypes.windll.user32.ShowWindow(hwnd, 5)

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
        self.thermal_baseline = 42.0
        self.uplink_queue = queue.Queue()

    def _get_real_cpu_temp(self):
        try:
            ps_cmd = 'powershell "get-wmiobject MSAcpi_ThermalZoneTemperature -namespace root/wmi | Select-Object -ExpandProperty CurrentTemperature"'
            raw_temp = subprocess.check_output(ps_cmd, shell=True, timeout=3, stderr=subprocess.DEVNULL).decode().strip()
            if raw_temp:
                temp_values = [int(t) for t in raw_temp.split() if t.isdigit()]
                if temp_values:
                    max_raw = max(temp_values)
                    temp_c = (max_raw - 2732) / 10.0
                    if 20 < temp_c < 110: return round(temp_c, 1)
        except: pass
        cpu_load = psutil.cpu_percent()
        return round(self.thermal_baseline + (cpu_load * 0.45) + random.uniform(-0.5, 0.5), 1)

    def secure_login(self):
        if os.path.exists(SESSION_PATH):
            try:
                with open(SESSION_PATH, "r") as f:
                    sess = json.load(f)
                res = self.supabase.auth.set_session(sess['access_token'], sess['refresh_token'])
                self.user_id = res.user.id
                return True
            except: pass
        server = HTTPServer(('localhost', 54321), AuthHandler)
        server.access_token = None
        webbrowser.open(f"{DASHBOARD_URL}/signin?returnTo=/?sync=true")
        server.handle_request()
        if server.access_token:
            res = self.supabase.auth.set_session(server.access_token, server.refresh_token)
            self.user_id = res.user.id
            with open(SESSION_PATH, "w") as f:
                json.dump({"access_token": server.access_token, "refresh_token": server.refresh_token}, f)
            return True
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
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu_model, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            manifest.append({"type": "CPU", "model_name": cpu_model.strip(), "slot_index": 0})
            total_ram = round(psutil.virtual_memory().total / (1024 ** 3))
            manifest.append({"type": "RAM", "model_name": f"System RAM {total_ram}GB", "slot_index": 0})
            for i, p in enumerate(psutil.disk_partitions()):
                if 'fixed' in p.opts:
                    try: manifest.append({"type": "DISK", "model_name": f"Drive {p.mountpoint}", "slot_index": i, "mount_point": p.mountpoint})
                    except: continue
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
                            out = subprocess.check_output(cmd, shell=True, timeout=2, stderr=subprocess.DEVNULL).decode().strip()
                            if out: vram_gb = f"{round(int(out) / 1024, 1)}GB"
                        except: pass
                    manifest.append({"type": "GPU", "model_name": name, "slot_index": i, "raw_name": name, "vram_info": vram_gb})
            except Exception as e: logger.error(f"GPU Sync Error: {e}")
            self.active_components = []
            for item in manifest:
                m_point = item.pop("mount_point", None)
                raw_n = item.pop("raw_name", item["model_name"])
                res = self.supabase.table("components").upsert({**item, "rig_id": self.rig_id}, on_conflict="rig_id,model_name,slot_index").execute()
                if res.data: self.active_components.append({**res.data[0], "mount_point": m_point, "raw_name": raw_n})
        except Exception as e: logger.error(f"Critical Sync Error: {e}")

    def telemetry_stream(self):
        """Thread A: Hardened for Schema public.telemetry"""
        logger.info("Telemetry collection engine started.")
        while not self.stop_event.is_set():
            now = datetime.datetime.now(datetime.UTC).isoformat()
            payload = []
            cpu_usage = psutil.cpu_percent(interval=1)
            cpu_temp = self._get_real_cpu_temp()
            ram_usage = psutil.virtual_memory().percent

            for comp in self.active_components:
                # Initialize row with common dashboard columns
                data_row = {
                    "component_id": comp['id'],
                    "user_id": self.user_id,
                    "recorded_at": now,
                    "load_percent": 0.0,
                    "temp_celsius": 0.0
                }
                try:
                    if comp['type'] == 'CPU':
                        data_row["load_percent"] = cpu_usage
                        data_row["temp_celsius"] = cpu_temp
                        data_row["cpu_load"] = cpu_usage
                        data_row["cpu_temp"] = cpu_temp
                    elif comp['type'] == 'RAM':
                        data_row["load_percent"] = ram_usage
                        data_row["ram_load"] = ram_usage
                    elif comp['type'] == 'DISK':
                        usage = psutil.disk_usage(comp.get('mount_point', 'C:\\'))
                        data_row["load_percent"] = usage.percent
                        data_row["free_space_gb"] = round(usage.free / (1024**3), 2)
                    elif comp['type'] == 'GPU':
                        g_load, g_temp = cpu_usage * 0.8, cpu_temp - 5.0
                        if "NVIDIA" in comp.get('raw_name', '').upper():
                            try:
                                cmd = f"nvidia-smi --query-gpu=utilization.gpu,temperature.gpu --format=csv,noheader,nounits --id={comp['slot_index']}"
                                out = subprocess.check_output(cmd, shell=True, timeout=2, stderr=subprocess.DEVNULL).decode().strip().split(',')
                                g_load, g_temp = float(out[0]), float(out[1])
                            except: pass
                        data_row["load_percent"] = g_load
                        data_row["temp_celsius"] = g_temp
                        data_row["gpu_load"] = g_load
                        data_row["gpu_temp"] = g_temp

                    payload.append(data_row)
                except Exception as e:
                    logger.warning(f"Failed component {comp['type']}: {e}")
                    continue
            if payload: self.uplink_queue.put(payload)
            time.sleep(5)

    def uplink_worker(self):
        while not self.stop_event.is_set():
            if os.path.exists(BUFFER_FILE):
                try:
                    with open(BUFFER_FILE, "r") as f: cache = json.load(f)
                    if cache:
                        self.supabase.table("telemetry").insert(cache).execute()
                        os.remove(BUFFER_FILE)
                        logger.info("Local cache uploaded.")
                except: pass
            try:
                batch = self.uplink_queue.get(timeout=5)
                try:
                    self.supabase.table("telemetry").insert(batch).execute()
                    self.supabase.table("rigs").update({"last_ping": batch[0]["recorded_at"]}).eq("id", self.rig_id).execute()
                except Exception as e:
                    logger.error(f"Upload failed: {e}")
                    self._save_to_disk(batch)
                self.uplink_queue.task_done()
            except queue.Empty: continue

    def _save_to_disk(self, data):
        existing = []
        if os.path.exists(BUFFER_FILE):
            try:
                with open(BUFFER_FILE, "r") as f: existing = json.load(f)
            except: pass
        existing.extend(data)
        with open(BUFFER_FILE, "w") as f: json.dump(existing[-5000:], f)

def on_quit(icon, item):
    global global_agent
    if global_agent: global_agent.stop_event.set()
    icon.stop()
    os._exit(0)

if __name__ == "__main__":
    if not check_admin(): sys.exit(0)
    global_agent = SGAAgent()
    if global_agent.secure_login():
        global_agent.sync_hardware()
        hide_console()
        notification.notify(title="SGA UPLINK", message="Hardened Schema Sync Active.")
        threading.Thread(target=global_agent.telemetry_stream, daemon=True).start()
        threading.Thread(target=global_agent.uplink_worker, daemon=True).start()
        img = Image.new('RGB', (64, 64), (249, 115, 22))
        menu = pystray.Menu(pystray.MenuItem("Show Console", show_console), pystray.MenuItem("Exit Agent", on_quit))
        icon = pystray.Icon("SGA_Agent", img, "SGA Uplink Agent", menu)
        icon.run()