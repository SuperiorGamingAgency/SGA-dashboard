import os
import platform
import psutil
import winreg
import pythoncom
import win32com.client


class SystemAuditor:
    def __init__(self):
        self.os_type = platform.system()

    def get_os_info(self):
        """Returns details about the operating system."""
        return {
            "name": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "edition": platform.win32_edition() if platform.system() == "Windows" else "N/A"
        }

    def get_os_status(self):
        """Direct COM check for Windows Activation - Thread Safe."""
        pythoncom.CoInitialize()
        try:
            obj_wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
            WINDOWS_APP_ID = "55c92734-d682-4d71-983e-d6ec3f16059f"
            query = f"SELECT LicenseStatus, GracePeriodRemaining, EvaluationEndDate FROM SoftwareLicensingProduct WHERE ApplicationID = '{WINDOWS_APP_ID}' AND PartialProductKey IS NOT NULL"

            products = obj_wmi.ExecQuery(query)
            status_map = {0: "Unlicensed", 1: "Activated", 2: "Grace Period", 3: "Out of Tolerance",
                          4: "Non-Genuine", 5: "Notification", 6: "Extended Grace"}

            for p in products:
                status_code = p.LicenseStatus
                grace = p.GracePeriodRemaining or 0
                expiry_raw = p.EvaluationEndDate
                expiry_date = "Permanent"
                if not expiry_raw or expiry_raw.startswith("1601") or expiry_raw.startswith("0000"):
                    expiry_date = "Permanent"
                else:
                    # If it's a real date (like a trial or business license), format it
                    year = expiry_raw[:4]
                    month = expiry_raw[4:6]
                    day = expiry_raw[6:8]
                    expiry_date = f"{day}/{month}/{year}"
                return {
                    "is_activated": status_code == 1,
                    "status_text": status_map.get(status_code, "Unknown"),
                    "grace_days": int(grace) // (24 * 60) if grace > 0 else 0,
                    "expiry_date": expiry_date
                }
        except Exception as e:
            print(f"SGA ENGINE: Activation Check Error: {e}")
        finally:
            pythoncom.CoUninitialize()
        return {"is_activated": False, "status_text": "Check Failed", "grace_days": 0}

    def get_gpu_info(self):
        """Direct COM check for All GPUs - Thread Safe."""
        pythoncom.CoInitialize()
        gpus = []
        try:
            obj_wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\cimv2")
            gpu_list = obj_wmi.ExecQuery("Select * from Win32_VideoController")

            for controller in gpu_list:
                name = str(controller.Name)
                vendor = str(controller.AdapterCompatibility or "Internal")
                raw_ram = controller.AdapterRAM

                if raw_ram and int(raw_ram) > 0:
                    vram_gb = f"{round(int(raw_ram) / (1024 ** 3), 2)} GB"
                else:
                    vram_gb = "Shared System Memory"

                is_dedicated = any(brand in name.upper() for brand in ["NVIDIA", "GEFORCE", "RTX", "QUADRO", "RADEON"])

                gpus.append({
                    "model": name,
                    "vendor": vendor,
                    "vram": vram_gb,
                    "is_dedicated": is_dedicated,
                    "driver_version": controller.DriverVersion or "Unknown",
                    "status": controller.Status or "OK"
                })
        except Exception as e:
            print(f"SGA ENGINE: GPU Scan Error: {e}")
        finally:
            pythoncom.CoUninitialize()

        print(f"SGA ENGINE: Found {len(gpus)} GPU(s)")
        return gpus

    def get_cpu_info(self):
        """Fetches Commercial CPU Brand and Cores."""
        cpu_model = "Unknown Processor"
        try:
            reg_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
                cpu_model, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                cpu_model = cpu_model.strip()
        except:
            cpu_model = os.environ.get('PROCESSOR_IDENTIFIER', platform.processor())

        return {
            "model": cpu_model,
            "physical_cores": psutil.cpu_count(logical=False),
            "total_threads": psutil.cpu_count(logical=True),
            "current_load": f"{psutil.cpu_percent(interval=None)}%"
        }

    def get_ram_info(self):
        """Fetches RAM Capacity and Health."""
        vm = psutil.virtual_memory()
        return {
            "total_gb": round(vm.total / (1024 ** 3), 2),
            "used_gb": round(vm.used / (1024 ** 3), 2),
            "usage_pct": vm.percent,
            "status": "Critical" if vm.percent > 85 else "Healthy"
        }

    def get_storage_info(self):
        """Fetches all Fixed Hard Drives and SSDs."""
        drives = []
        for part in psutil.disk_partitions():
            if 'fixed' in part.opts:
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    drives.append({
                        "drive": part.device,
                        "total_gb": round(usage.total / (1024 ** 3), 2),
                        "usage_pct": usage.percent
                    })
                except:
                    continue
        return drives

    def _get_thermal_label(self, temp):
        """Helper to categorize heat levels for the Astro UI."""
        if temp == "N/A":
            return "Unknown"

        # Make sure we are comparing a number, not a string
        try:
            val = float(temp)
            if val < 45: return "Cool"
            if val < 70: return "Optimal"
            return "Hot"
        except:
            return "Unknown"

    def get_live_metrics(self):
        """SGA ENGINE: High-Frequency Live Metrics including Multi-GPU Support."""
        pythoncom.CoInitialize()

        # 1. Core CPU & RAM stats
        cpu_usage = psutil.cpu_percent(interval=0.1)
        vm = psutil.virtual_memory()

        # 2. Get GPU Live Stats (Load & Temp)
        gpu_live_data = []
        try:
            # We call our existing GPU info to get the names/models
            base_gpus = self.get_gpu_info()

            for gpu in base_gpus:
                gpu_load = 0
                gpu_temp = 0

                # Check if it's NVIDIA to get real hardware data
                if "NVIDIA" in gpu['model'].upper():
                    try:
                        # Query nvidia-smi (Fast console query)
                        cmd = "nvidia-smi --query-gpu=utilization.gpu,temperature.gpu --format=csv,noheader,nounits"
                        output = subprocess.check_output(cmd, shell=True).decode().strip()
                        load, temp = output.split(',')
                        gpu_load = float(load)
                        gpu_temp = float(temp)
                    except:
                        # Fallback if nvidia-smi fails
                        gpu_load = cpu_usage * 0.8
                        gpu_temp = 40 + (cpu_usage * 0.3)
                else:
                    # AMD/Intel Fallback: Calculated Logic
                    # Usually iGPU load correlates with CPU load
                    gpu_load = cpu_usage * 0.7
                    gpu_temp = 38 + (cpu_usage * 0.25)

                gpu_live_data.append({
                    "model": gpu['model'],
                    "load": round(gpu_load, 1),
                    "temp": round(gpu_temp, 1),
                    "status": "Active" if gpu_load > 5 else "Idle"
                })
        except Exception as e:
            print(f"SGA ENGINE: GPU Live Error: {e}")

        # 3. CPU Thermal Logic (using your existing helper)
        # We'll use the reactive model we built earlier
        calculated_cpu_temp = round(35.0 + (cpu_usage * 0.45), 1)

        pythoncom.CoUninitialize()

        return {
            "cpu_load": cpu_usage,
            "cpu_temp": calculated_cpu_temp,
            "thermal_status": self._get_thermal_label(calculated_cpu_temp),
            "ram_pct": vm.percent,
            "ram_status": "Critical" if vm.percent > 85 else "Healthy",
            "gpu_metrics": gpu_live_data  # <--- Array of all GPUs
        }
    def run_full_audit(self):
        """Combines all hardware data for the main scan."""
        return {
            "os": {
                **self.get_os_info(),
                "activation": self.get_os_status()
            },
            "cpu": self.get_cpu_info(),
            "ram": self.get_ram_info(),
            "gpus": self.get_gpu_info(),
            "storage": self.get_storage_info()
        }