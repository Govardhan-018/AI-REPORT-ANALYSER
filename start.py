import subprocess
import sys
import time

def main():
    print("=======================================")
    print("Starting ComplianceAI...")
    print("=======================================")
    print()

    print("[1/2] Starting Python Backend Server...")
    # Start backend
    backend_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd="backend"
    )

    # Give backend a moment to start
    time.sleep(2)

    print("[2/2] Starting Electron Frontend...")
    # Start frontend
    # Use shell=True for npm to resolve correctly on Windows
    frontend_process = subprocess.Popen(
        "npm run electron",
        cwd="frontend",
        shell=True
    )

    print()
    print("Both services are running! Press Ctrl+C in this terminal to stop them.")

    try:
        # Wait for processes to exit
        backend_process.wait()
        frontend_process.wait()
    except KeyboardInterrupt:
        print("\nStopping services...")
        backend_process.terminate()
        frontend_process.terminate()
        print("Services stopped.")

if __name__ == "__main__":
    main()
