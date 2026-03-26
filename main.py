import json
from fastapi.encoders import jsonable_encoder
from fastapi import FastAPI
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
from auditor import SystemAuditor  # Import your new class
import uvicorn
import psutil
app = FastAPI(title="SGA Tech-Concierge API", version="2026.1")

# --- 1. SECURITY (CORS) ---
# This allows your WebStorm/Astro frontend to talk to this Python code
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, change this to your actual domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 2. INITIALIZE ENGINE ---
engine = SystemAuditor()


# --- 3. ENDPOINTS ---

@app.get("/")
def home():
    return {
        "status": "Online",
        "business": "SGA Tech-Concierge Budapest",
        "endpoints": ["/scan", "/health"]
    }


@app.get("/scan")
def get_full_scan():
    """Compiles a full SGA System Intelligence Report."""
    # 1. Fetch raw data from the Auditor engine
    data = engine.run_full_audit()
    json_conpatible_data = jsonable_encoder(data)
    print("data:")
    print(json.dumps(json_conpatible_data, indent=4))
    # 2. Extract key variables for the SGA Intelligence Layer
    gpu_list = data.get('gpus', [])
    cpu_model = data.get('cpu', {}).get('model', 'Unknown')
    ram_usage = data.get('ram', {}).get('usage_pct', 0)
    os_name = data.get('os', {}).get('name', 'Windows')
    # 3. IDENTIFY PRIMARY GAMING HARDWARE
    # We find the first 'dedicated' card (like your RTX 4070)
    primary_gpu = next((g for g in gpu_list if g['is_dedicated']), None)
    # --- 4. THE PRO-ADVICE ENGINE (SGA Strategy) ---

    # GPU Strategy
    if primary_gpu:
        model = primary_gpu['model'].upper()
        if "RTX 40" in model:
            gpu_advice = "RTX 40-Series Elite detected. Frame Generation & Reflex Low Latency tuning active."
        elif "RTX" in model:
            gpu_advice = "Ray-Tracing architecture confirmed. Tensor Core optimization suggested."
        else:
            gpu_advice = "Dedicated GPU detected. Performance driver health check recommended."
    else:
        gpu_advice = "⚠️ Integrated Graphics Only: Gaming performance is severely throttled."

    # CPU & RAM Strategy
    if ram_usage > 75:
        ram_advice = "CRITICAL: Memory bottleneck detected. System cleanup required to reclaim FPS."
        tier = "Premium Deep Clean"
    else:
        ram_advice = "Memory performance stable. Standard maintenance suggested."
        tier = "Standard Audit"

    # Dual-GPU Logic (The Budapest Laptop Check)
    dual_msg = "Hybrid Graphics Active: Ensure high-performance mode is forced in Windows." if len(
        gpu_list) > 1 else "Single GPU detected."

    # --- 5. ENRICH THE RESPONSE ---
    data["pro_analysis"] = {
        "gpu_optimization": gpu_advice,
        "ram_alert": ram_advice,
        "service_tier": tier,
        "display_path": dual_msg,
        "audit_timestamp": datetime.now().strftime("%H:%M:%S")
    }

    # Log to PyCharm for debugging
    return data

@app.get("/health")
def server_health():
    return {"status": "Backend is responsive", "port": 8888}

@app.get("/live-stats")
def live_stats():
    data = engine.get_live_metrics()
    print(data)
    return data

# --- 5. EXECUTION ---


if __name__ == "__main__":
    # We run on 8888 to avoid the "Zombie Port 8000" issue
    uvicorn.run("main:app", host="127.0.0.1", port=5050, reload=True)