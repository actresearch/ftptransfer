from pathlib import Path
import subprocess
import sys
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class RestartHandler(FileSystemEventHandler):
    def __init__(self, command, watch_root):
        self.command = command
        self.watch_root = Path(watch_root)
        self.process = None
        self.restart()

    def restart(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = subprocess.Popen(self.command, cwd=self.watch_root)
        print(f"Started process with PID {self.process.pid}", flush=True)

    def on_any_event(self, event):
        if event.is_directory:
            return

        changed = Path(event.src_path)
        if changed.suffix not in {".py", ".txt", ".sh"}:
            return
        if "__pycache__" in changed.parts or ".git" in changed.parts:
            return

        print(f"Change detected in {changed}; restarting app", flush=True)
        self.restart()


def main():
    watch_root = Path(__file__).resolve().parent
    command = [sys.executable, "app.py"]
    handler = RestartHandler(command, watch_root)

    observer = Observer()
    observer.schedule(handler, str(watch_root), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        if handler.process and handler.process.poll() is None:
            handler.process.terminate()
    observer.join()


if __name__ == "__main__":
    main()
