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
from PIL import Image
import pystray
from supabase import create_client
from plyer import notification


# --- WINDOW CONTROL LOGIC ---
def hide_console():
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd != 0:
        ctypes.windll.user32.ShowWindow(hwnd, 0)


def show_console():
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd != 0:
        ctypes.windll.user32.ShowWindow(hwnd, 5)
        ctypes.windll.user32.SetForegroundWindow(hwnd)


# --- SETTINGS ---
SUPABASE_URL = "https://hyakorvmqpirnorpbskk.supabase.co"
SUPABASE_KEY = "sb_publishable_zfssEvxed9ul69PEX8NU6A_ACNnrbrU"
SESSION_PATH = os.path.join(os.environ.get('APPDATA', '.'), "sga_agent_session.json")


def print_header():
    os.system('cls' if os.name == 'nt' else 'clear')
    print("=" * 60)
    print("        SUPERIOR GAMING AGENCY | SECURE UPLINK AGENT")
    print("=" * 60)
    print("\n [!] LOGIN REQUIRED TO ESTABLISH SECURE HANDSHAKE.")
    print(" [!] AFTER LOGIN, THIS WINDOW WILL VANISH TO THE BACKGROUND.")
    print("\n" + "-" * 60 + "\n")


def status(msg):
    print(f" [+] {msg}...")


def check_admin():
    if ctypes.windll.shell32.IsUserAnAdmin():
        return True
    else:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
        return False


class SGAAgent:
    def __init__(self):
        self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        node = uuid.getnode()
        self.rig_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(node)))
        self.user_id = None
        self.active_components = []
        self.stop_event = threading.Event()

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

    def get_gpu_manifest(self):
        gpus = []
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
                if vram_gb == "Shared":
                    try:
                        ram = int(controller.AdapterRAM)
                        if ram < 0: ram += 2 ** 32
                        vram_gb = f"{round(ram / (1024 ** 3), 2)}GB"
                    except:
                        pass
                gpus.append({"type": "GPU", "model_name": f"{name} ({vram_gb})", "slot_index": i, "raw_name": name})
        except:
            pass
        return gpus

    def secure_login(self):
        if os.path.exists(SESSION_PATH):
            try:
                with open(SESSION_PATH, "r") as f:
                    sess = json.load(f)
                res = self.supabase.auth.set_session(sess['access_token'], sess['refresh_token'])
                self.user_id = res.user.id
                status("Session restored from local vault")
                return True
            except:
                pass

        print_header()
        while not self.user_id:
            email = input(" 📧 Email: ").strip()
            password = input(" 🔑 Password: ").strip()
            try:
                res = self.supabase.auth.sign_in_with_password({"email": email, "password": password})
                self.user_id = res.user.id
                with open(SESSION_PATH, "w") as f:
                    json.dump({"access_token": res.session.access_token, "refresh_token": res.session.refresh_token}, f)
                status("Handshake successful")
                return True
            except:
                print(" [!] Login Failed. Check credentials.")
        return False

    def sync_hardware(self):
        self.supabase.table("rigs").upsert({
            "id": self.rig_id,
            "user_id": self.user_id,
            "os_name": f"{platform.system()} {platform.release()}",
            "is_active": True,
            "last_ping": datetime.datetime.now(datetime.UTC).isoformat()
        }).execute()

        # 2. Immediately fetch it back to confirm the Handshake to the Dashboard
        rig_sync = self.supabase.table("rigs").select("*").eq("id", self.rig_id).execute()

        if rig_sync.data:
            status(f"Identity Verified: {rig_sync.data[0]['os_name']}")

        manifest = []
        reg_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
            cpu_model, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        manifest.append({"type": "CPU", "model_name": cpu_model.strip(), "slot_index": 0})
        manifest.append({"type": "RAM", "model_name": f"{round(psutil.virtual_memory().total / (1024 ** 3))}GB RAM",
                         "slot_index": 0})

        for i, p in enumerate(psutil.disk_partitions()):
            if 'fixed' in p.opts or 'rw' in p.opts:
                try:
                    total = round(psutil.disk_usage(p.mountpoint).total / (1024 ** 3))
                    manifest.append({"type": "DISK", "model_name": f"Drive {p.mountpoint} ({total}GB)", "slot_index": i,
                                     "mount_point": p.mountpoint})
                except:
                    continue

        manifest.extend(self.get_gpu_manifest())
        self.active_components = []
        for item in manifest:
            m_point = item.pop("mount_point", None)
            raw_n = item.pop("raw_name", item["model_name"])
            res = self.supabase.table("components").upsert({**item, "rig_id": self.rig_id},
                                                           on_conflict="rig_id,model_name,slot_index").execute()
            if res.data:
                self.active_components.append({**res.data[0], "mount_point": m_point, "raw_name": raw_n})
        status("Uplink active")

    def telemetry_stream(self):
        while not self.stop_event.is_set():
            now = datetime.datetime.now(datetime.UTC).isoformat()
            payload = []
            cpu_usage = psutil.cpu_percent(interval=1)
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

                payload.append({
                    "component_id": comp['id'], "user_id": self.user_id,
                    "load_percent": int(round(load)), "temp_celsius": int(round(temp)),
                    "recorded_at": now
                })
            try:
                self.supabase.table("telemetry").insert(payload).execute()
                self.supabase.table("rigs").update({"is_active": True, "last_ping": now}).eq("id",
                                                                                             self.rig_id).execute()
            except:
                pass
            time.sleep(5)


# --- GLOBAL APP STATE ---
global_agent = None


def on_quit(icon, item):
    global global_agent
    if global_agent:
        global_agent.stop_event.set()
    try:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        rid = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(uuid.getnode())))
        sb.table("rigs").update({"is_active": False}).eq("id", rid).execute()
    except:
        pass
    icon.stop()
    os._exit(0)


def on_show_console(icon, item):
    show_console()


if __name__ == "__main__":
    if not check_admin():
        sys.exit(0)

    global_agent = SGAAgent()

    if global_agent.secure_login():
        global_agent.sync_hardware()

        print(" [+] Handshake complete. Relocating to background...")
        time.sleep(2)
        hide_console()

        notification.notify(title="SGA UPLINK LIVE", message="Secure background streaming active.", timeout=5)

        t = threading.Thread(target=global_agent.telemetry_stream, daemon=True)
        t.start()

        try:
            img = Image.open("SGA_LOGO.ico")
        except:
            img = Image.new('RGB', (64, 64), (249, 115, 22))

        # --- REPAIRED MENU (NO SEPARATOR) ---
        menu = pystray.Menu(
            pystray.MenuItem(f"Rig Online: {global_agent.rig_id[:8]}", lambda: None, enabled=False),
            pystray.MenuItem("Show Console", on_show_console, default=True),
            pystray.MenuItem("Exit SGA Agent", on_quit)
        )

        icon = pystray.Icon("SGA_Agent", img, "SGA Uplink Agent", menu)
        icon.run()
    else:
        sys.exit()