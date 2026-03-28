import os
import sys
import ctypes
import time
import uuid
import psutil
import platform
import socket
import datetime
import winreg
import subprocess
import pythoncom
import win32com.client
import threading
from PIL import Image
import pystray
from supabase import create_client
from plyer import notification

# --- ADMIN ELEVATION ---
def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()
    except: return False

if not is_admin():
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
    sys.exit()

# --- CONFIG ---
SUPABASE_URL = "https://hyakorvmqpirnorpbskk.supabase.co"
SUPABASE_KEY = "sb_publishable_zfssEvxed9ul69PEX8NU6A_ACNnrbrU"

def trigger_handshake_notification():
    """Trigger the official SGA startup alert."""
    try:
        notification.notify(
            title="SGA | SECURE UPLINK ESTABLISHED",
            message="SGA Agent is live now.\nAttention: We are only collecting rig vitals.",
            app_name='SGA Agent',
            app_icon="SGA_LOGO.ico", # Ensure this exists in the folder
            timeout=10,
        )
    except Exception as e:
        print(f"⚠️ Notification Error: {e}")

class SGAAgent:
    def __init__(self):
        pythoncom.CoInitialize()
        self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        node = uuid.getnode()
        self.rig_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(node)))
        self.hostname = socket.gethostname()
        self.active_components = []

    def _get_real_cpu_temp(self):
        try:
            wmi = win32com.client.GetObject("winmgmts:/root/wmi")
            res = wmi.ExecQuery("SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
            for item in res:
                temp_c = (item.CurrentTemperature - 2732) / 10.0
                if 10 < temp_c < 110: return round(temp_c, 1)
        except: pass
        return round(35.0 + (psutil.cpu_percent() * 0.45), 1)

    def get_gpu_manifest(self):
        gpus = []
        try:
            wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
            gpu_list = wmi.ExecQuery("Select * from Win32_VideoController")
            for i, controller in enumerate(gpu_list):
                name = str(controller.Name).strip()
                vram_gb = "Shared"
                if "NVIDIA" in name.upper():
                    try:
                        cmd = f"nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits --id={i}"
                        smi_out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode().strip()
                        if smi_out: vram_gb = f"{round(int(smi_out) / 1024, 1)}GB"
                    except: pass
                if vram_gb == "Shared":
                    raw_ram = controller.AdapterRAM
                    if raw_ram:
                        ram_val = int(raw_ram)
                        if ram_val < 0: ram_val += 2 ** 32
                        if ram_val > 0: vram_gb = f"{round(ram_val / (1024 ** 3), 2)}GB"
                gpus.append({"type": "GPU", "model_name": f"{name} ({vram_gb})", "slot_index": i, "raw_name": name})
        except: pass
        return gpus

    def initialize_rig(self):
        self.supabase.table("rigs").upsert({
            "id": self.rig_id,
            "os_name": f"{platform.system()} {platform.release()}",
            "is_active": True,
            "last_ping": datetime.datetime.now(datetime.UTC).isoformat()
        }).execute()
        manifest = []
        # CPU
        reg_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
            cpu_model, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        manifest.append({"type": "CPU", "model_name": cpu_model.strip(), "slot_index": 0})
        # RAM
        ram_gb = round(psutil.virtual_memory().total / (1024 ** 3))
        manifest.append({"type": "RAM", "model_name": f"{ram_gb}GB System Memory", "slot_index": 0})
        # DISK
        for i, p in enumerate(psutil.disk_partitions()):
            if 'fixed' in p.opts or 'rw' in p.opts:
                try:
                    total = round(psutil.disk_usage(p.mountpoint).total / (1024 ** 3))
                    manifest.append({"type": "DISK", "model_name": f"Drive {p.mountpoint} ({total}GB)", "slot_index": i, "mount_point": p.mountpoint})
                except: continue
        # GPU
        manifest.extend(self.get_gpu_manifest())
        self.active_components = []
        for item in manifest:
            m_point = item.pop("mount_point", None)
            raw_name = item.pop("raw_name", item["model_name"])
            res = self.supabase.table("components").upsert({**item, "rig_id": self.rig_id}).execute()
            if res.data:
                self.active_components.append({"db_id": res.data[0]['id'], "type": item['type'], "index": item['slot_index'], "mount_point": m_point, "model": raw_name})

    def start_streaming(self):
        psutil.cpu_percent(interval=None)
        trigger_handshake_notification()
        while True:
            payload = []
            now = datetime.datetime.now(datetime.UTC).isoformat()
            cpu_usage = psutil.cpu_percent(interval=0.1)
            cpu_temp = self._get_real_cpu_temp()
            for comp in self.active_components:
                load, temp = 0, 0
                if comp['type'] == 'CPU': load, temp = cpu_usage, cpu_temp
                elif comp['type'] == 'RAM': load = psutil.virtual_memory().percent
                elif comp['type'] == 'DISK':
                    try: load = psutil.disk_usage(comp['mount_point']).percent
                    except: continue
                elif comp['type'] == 'GPU':
                    if "NVIDIA" in comp['model'].upper():
                        try:
                            cmd = f"nvidia-smi --query-gpu=utilization.gpu,temperature.gpu --format=csv,noheader,nounits --id={comp['index']}"
                            out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode().strip().split(',')
                            load, temp = float(out[0]), float(out[1])
                        except: pass
                    else: load, temp = cpu_usage * 0.6, cpu_temp
                payload.append({"component_id": comp['db_id'], "load_percent": int(round(load)), "temp_celsius": int(round(temp)), "recorded_at": now})
            try:
                self.supabase.table("telemetry").insert(payload).execute()
                self.supabase.table("rigs").update({"last_ping": now}).eq("id", self.rig_id).execute()
            except: pass
            time.sleep(2)

# --- SYSTEM TRAY LOGIC ---
def on_quit(icon, item):
    icon.stop()
    os._exit(0)

def run_agent_service():
    agent = SGAAgent()
    agent.initialize_rig()
    agent.start_streaming()


# Inside agent.py
def on_quit(icon, item):
    """Tell Supabase we are offline before closing."""
    print("👋 SGA Agent: Sending goodbye signal...")
    try:
        # 1. Update the Rig status to False immediately
        # We use a new 'agent' instance or a global one to send the signal
        temp_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        node = uuid.getnode()
        rig_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(node)))

        temp_supabase.table("rigs").update({
            "is_active": False,
            "last_ping": datetime.datetime.now(datetime.UTC).isoformat()
        }).eq("id", rig_id).execute()

        print("✅ Goodbye signal sent.")
    except Exception as e:
        print(f"⚠️ Goodbye signal failed: {e}")

    icon.stop()
    os._exit(0)

if __name__ == "__main__":
    # Start the Agent logic in a background thread
    data_thread = threading.Thread(target=run_agent_service, daemon=True)
    data_thread.start()

    # Create the System Tray Icon
    try:
        image = Image.open("SGA_LOGO.ico")
    except:
        image = Image.new('RGB', (64, 64), color=(249, 115, 22)) # Fallback orange square

    menu = pystray.Menu(
        pystray.MenuItem("SGA | Uplink Active", lambda: None, enabled=False),
        pystray.MenuItem("Exit Agent", on_quit)  # <--- CALL IT HERE
    )

    # 4. RUN THE TRAY
    icon = pystray.Icon("SGA_Agent", image, "Superior Gaming Agency", menu)
    icon.run()