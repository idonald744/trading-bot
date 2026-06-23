import json
import os
from datetime import datetime

LOG_FILE = "logs/triggers.json"

def log_trigger(state_matrix: dict):
    """Save every trigger to a log file for review"""
    
    # Load existing logs
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f:
            try:
                logs = json.load(f)
            except:
                logs = []
    else:
        logs = []
    
    # Append new trigger
    logs.append(state_matrix)
    
    # Save back to file
    with open(LOG_FILE, 'w') as f:
        json.dump(logs, f, indent=2)
    
    print(f"[✓] Trigger logged to {LOG_FILE}")

def view_logs():
    """Print all logged triggers"""
    if not os.path.exists(LOG_FILE):
        print("[!] No triggers logged yet")
        return
    
    with open(LOG_FILE, 'r') as f:
        logs = json.load(f)
    
    print(f"\n[*] Total triggers logged: {len(logs)}")
    for i, trigger in enumerate(logs):
        print(f"\n--- Trigger #{i+1} ---")
        print(json.dumps(trigger, indent=2))

if __name__ == "__main__":
    view_logs()