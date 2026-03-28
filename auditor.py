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
from supabase import create_client

# --- ADMIN ELEVATION ---
if not ctypes.windll.shell32.IsUserAnAdmin():
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
    sys.exit()

SUPABASE_URL = "https://hyakorvmqpirnorpbskk.supabase.co"
SUPABASE_KEY = "sb_publishable_zfssEvxed9ul69PEX8NU6A_ACNnrbrU"


class SGAAgent:
    def __init__(self):
        self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        node = uuid.getnode()
        self.rig_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(node)))
        self.hostname = socket.gethostname()
        self.active_components = []

    def _get_real_cpu_temp(self):
        """Hardware Thermal Zone Query."""
        try:
            pythoncom.CoInitialize()
            wmi = win32com.client.GetObject("winmgmts:/root/wmi")
            res = wmi.ExecQuery("SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
            for item in res:
                temp_c = (item.CurrentTemperature - 2732) / 10.0
                return round(temp_c, 1)
        except:
            return round(35.0 + (psutil.cpu_percent() * 0.45), 1)

    def get_gpu_info(self):
        """COM Discovery for ALL GPUs (Intel/AMD/NVIDIA)."""
        pythoncom.CoInitialize()
        gpus = []
        try:
            obj_wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
            gpu_list = obj_wmi.ExecQuery("Select * from Win32_VideoController")
            for i, controller in enumerate(gpu_list):
                name = str(controller.Name)
                raw_ram = controller.AdapterRAM
                vram_gb = f"{round(int(raw_ram) / (1024 ** 3), 2)} GB" if raw_ram and int(raw_ram) > 0 else "Shared"

                gpus.append({
                    "model": name,
                    "slot_index": i,
                    "vram": vram_gb
                })
        except Exception as e:
            print(f"⚠️ GPU Scan Error: {e}")
        finally:
            pythoncom.CoUninitialize()
        return gpus

    def initialize_rig(self):
        """Part 1: The Hardware Map."""
        print(f"🚀 SGA Agent: Mapping Hardware for {self.hostname}...")

        # 1. Rig Identity
        self.supabase.table("rigs").upsert({
            "id": self.rig_id, "os_name": platform.system(), "is_active": True, "last_ping": "now()"
        }).execute()

        manifest = []

        # 2. CPU (Registry Name)
        reg_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
            cpu_model, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        manifest.append({"type": "CPU", "model_name": cpu_model.strip(), "slot_index": 0})

        # 3. RAM
        ram_gb = round(psutil.virtual_memory().total / (1024 ** 3))
        manifest.append({"type": "RAM", "model_name": f"{ram_gb}GB System Memory", "slot_index": 0})

        # 4. Storage
        for i, p in enumerate(psutil.disk_partitions()):
            if 'fixed' in p.opts:
                try:
                    total = round(psutil.disk_usage(p.mountpoint).total / (1024 ** 3))
                    manifest.append({"type": "DISK", "model_name": f"Drive {p.mountpoint} ({total}GB)", "slot_index": i,
                                     "mount_point": p.mountpoint})
                except:
                    continue

        # 5. ALL GPUs (Intel + NVIDIA)
        found_gpus = self.get_gpu_info()
        for g in found_gpus:
            manifest.append({"type": "GPU", "model_name": f"{g['model']} ({g['vram']})", "slot_index": g['slot_index']})

        # 6. Database Sync
        self.active_components = []
        for item in manifest:
            m_point = item.pop("mount_point", None)
            res = self.supabase.table("components").upsert({**item, "rig_id": self.rig_id}).execute()
            if res.data:
                self.active_components.append({
                    "db_id": res.data[0]['id'], "type": item['type'], "index": item['slot_index'],
                    "mount_point": m_point, "model": item['model_name']
                })

    def start_streaming(self):
        """Part 2: Live Intel Streaming."""
        print("🛰️ SGA Agent: Live Stream Active...")
        psutil.cpu_percent(interval=None)

        while True:
            payload = []
            now = datetime.datetime.now(datetime.UTC).isoformat()
            cpu_usage = psutil.cpu_percent(interval=0.1)
            cpu_temp = self._get_real_cpu_temp()

            for comp in self.active_components:
                load, temp = 0, 0

                if comp['type'] == 'CPU':
                    load, temp = cpu_usage, cpu_temp

                elif comp['type'] == 'RAM':
                    load = psutil.virtual_memory().percent

                elif comp['type'] == 'DISK':
                    try:
                        load = psutil.disk_usage(comp['mount_point']).percent
                    except:
                        continue

                elif comp['type'] == 'GPU':
                    if "NVIDIA" in comp['model'].upper():
                        try:
                            cmd = f"nvidia-smi --query-gpu=utilization.gpu,temperature.gpu --format=csv,noheader,nounits --id={comp['index']}"
                            out = subprocess.check_output(cmd, shell=True).decode().strip().split(',')
                            load, temp = float(out[0]), float(out[1])
                        except:
                            pass
                    else:
                        # Intel / Integrated Logic: iGPUs share CPU thermal profile
                        load = cpu_usage * 0.6
                        temp = cpu_temp

                # FORCE INTEGER ROUNDING (Fixes 22P02 Error)
                payload.append({
                    "component_id": comp['db_id'],
                    "load_percent": int(round(load)),
                    "temp_celsius": int(round(temp)),
                    "recorded_at": now
                })

            try:
                self.supabase.table("telemetry").insert(payload).execute()
                print(f"📡 Synced | {datetime.datetime.now().strftime('%H:%M:%S')}")
            except Exception as e:
                print(f"❌ Sync Error: {e}")

            time.sleep(2)


if __name__ == "__main__":
    agent = SGAAgent()
    agent.initialize_rig()
    agent.start_streaming()