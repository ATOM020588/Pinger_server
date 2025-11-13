import sys
import json
import os
import subprocess
import zipfile
import threading
import time
import glob
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSpinBox, QTextEdit, QMessageBox
)
from PyQt6.QtCore import QTimer, QThread, pyqtSignal, Qt


# ========================================
# 1. ПИНГ-РАБОТНИК (фоновый цикл)
# ========================================
class PingWorker(threading.Thread):
    def __init__(self, gui, interval=30):
        super().__init__(daemon=True)
        self.gui = gui
        self.interval = interval
        self.stop_event = threading.Event()

    def log(self, msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        self.gui.log_signal.emit(line)

    def status(self, msg):
        self.gui.status_signal.emit(msg)

    def ping_ip(self, ip):
        try:
            cfg = self.gui.get_config()
            cmd = [
                'ping', '-n', str(cfg['packet_count']),
                '-w', str(cfg['ping_timeout_ms']), ip
            ]
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT,
                                          timeout=cfg['ping_timeout_ms'] / 1000 + 2,
                                          universal_newlines=True)
            return "TTL=" in out
        except Exception:
            return False

    def update_map(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            self.log(f"Ошибка чтения {path}: {e}")
            return

        ips = {}
        for typ in ("switches", "plan_switches"):
            for dev in data.get(typ, []):
                ip = dev.get("ip")
                if ip and ip != "—":
                    ips[ip] = (typ, dev)

        for ip, (typ, dev) in ips.items():
            if self.stop_event.is_set():
                return
            self.status(f"Ping: {ip}")
            ok = self.ping_ip(ip)
            dev["pingok"] = ok
            self.status(f"Ping: {ip} – {'OK' if ok else 'FAIL'}")
            self.log(f"Ping {ip} → {'OK' if ok else 'FAIL'}")

        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.log(f"Ошибка записи {path}: {e}")

    def run(self):
        while not self.stop_event.is_set():
            start = time.time()
            maps = glob.glob("data/maps/*.json")
            self.log(f"Найдено карт: {len(maps)}")
            for m in maps:
                if self.stop_event.is_set():
                    break
                self.update_map(m)
            elapsed = time.time() - start
            sleep_time = max(0, self.interval - elapsed)
            self.log(f"Цикл завершён за {elapsed:.1f}с, спим {sleep_time:.1f}с")
            self.status("Idle")
            self.stop_event.wait(sleep_time)

    def stop(self):
        self.stop_event.set()
        self.log("PingWorker остановлен")


# ========================================
# 2. ПОТОК ДЛЯ СЕРВЕРА
# ========================================
class ServerThread(QThread):
    log = pyqtSignal(str)
    process_started = pyqtSignal(object)

    def run(self):
        self.process = subprocess.Popen(
            ['python', 'server_ws.py'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        self.process_started.emit(self.process)
        for line in self.process.stdout:
            self.log.emit(line.strip())


# ========================================
# 3. ОСНОВНОЙ GUI
# ========================================
class ServerGUI(QMainWindow):
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("NMS Server")
        self.setFixedSize(800, 600)
        self.process = None
        self.ping_worker = None
        self.shutdown_timer = QTimer()
        self.shutdown_timer.setSingleShot(True)
        self.shutdown_timer.timeout.connect(self.final_shutdown)
        self.setup_ui()

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Статус
        self.status = QLabel("Сервер: ОСТАНОВЛЕН")
        self.status.setContentsMargins(0, 5, 0, 5)
        layout.addWidget(self.status, alignment=Qt.AlignmentFlag.AlignCenter)

        # Настройки пинга
        ping_settings_layout = QVBoxLayout()

        # Таймаут в миллисекундах
        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel("Ping timeout (мс):"))
        self.spin_timeout = QSpinBox()
        self.spin_timeout.setFixedSize(100, 30)
        self.spin_timeout.setRange(100, 10000)
        self.spin_timeout.setValue(5000)
        self.spin_timeout.setSingleStep(100)
        self.spin_timeout.valueChanged.connect(self.save_config)
        self.spin_timeout.setAlignment(Qt.AlignmentFlag.AlignRight)
        timeout_layout.addWidget(self.spin_timeout, alignment=Qt.AlignmentFlag.AlignLeft)
        timeout_layout.addStretch()
        ping_settings_layout.addLayout(timeout_layout)

        # Количество пакетов
        packets_layout = QHBoxLayout()
        packets_layout.addWidget(QLabel("Количество пакетов:"))
        self.spin_packets = QSpinBox()
        self.spin_packets.setFixedSize(100, 30)
        self.spin_packets.setRange(1, 10)
        self.spin_packets.setValue(3)
        self.spin_packets.valueChanged.connect(self.save_config)
        self.spin_packets.setAlignment(Qt.AlignmentFlag.AlignRight)
        packets_layout.addWidget(self.spin_packets, alignment=Qt.AlignmentFlag.AlignLeft)
        packets_layout.addStretch()
        ping_settings_layout.addLayout(packets_layout)

        # Интервал между пакетами (мс)
        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel("Интервал между пакетами (мс):"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setFixedSize(100, 30)
        self.spin_interval.setRange(100, 5000)
        self.spin_interval.setValue(1000)
        self.spin_interval.valueChanged.connect(self.save_config)
        self.spin_interval.setAlignment(Qt.AlignmentFlag.AlignRight)
        interval_layout.addWidget(self.spin_interval, alignment=Qt.AlignmentFlag.AlignLeft)
        interval_layout.addStretch()
        ping_settings_layout.addLayout(interval_layout)

        # Интервал сканирования карт
        scan_layout = QHBoxLayout()
        scan_layout.addWidget(QLabel("Интервал сканирования (сек):"))
        self.spin_scan = QSpinBox()
        self.spin_scan.setFixedSize(100, 30)
        self.spin_scan.setRange(10, 300)
        self.spin_scan.setValue(30)
        self.spin_scan.valueChanged.connect(self.save_config)
        self.spin_scan.setAlignment(Qt.AlignmentFlag.AlignRight)
        scan_layout.addWidget(self.spin_scan, alignment=Qt.AlignmentFlag.AlignLeft)
        scan_layout.addStretch()
        ping_settings_layout.addLayout(scan_layout)

        layout.addLayout(ping_settings_layout)

        # Кнопки
        btns = QHBoxLayout()
        self.start_btn = QPushButton("Запустить сервер")
        self.stop_btn = QPushButton("Выключить")
        self.emergency_btn = QPushButton("АВАРИЙНОЕ ВЫКЛЮЧЕНИЕ")
        self.backup_btn = QPushButton("Ручной backup")

        self.start_btn.clicked.connect(self.start_server)
        self.stop_btn.clicked.connect(self.stop_server)
        self.emergency_btn.clicked.connect(self.emergency_stop)
        self.backup_btn.clicked.connect(self.backup)

        for btn in [self.start_btn, self.stop_btn, self.emergency_btn, self.backup_btn]:
            btns.addWidget(btn)
        layout.addLayout(btns)

        # Логи
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        layout.addWidget(self.logs)

        # === СТАТУС-БАР ===
        self.status_bar = self.statusBar()
        self.status_label = QLabel("Idle")
        self.status_bar.addPermanentWidget(self.status_label)

        # Подключение сигналов
        self.log_signal.connect(self.append_log)
        self.status_signal.connect(self.status_label.setText)

        # === СТИЛИ ===
        self.setStyleSheet("""
            QMainWindow { background-color: #333; color: #FFC107; border: 1px solid #FFC107; }
            QLabel { color: #FFC107; font-size: 14px; }
            QPushButton { 
                background-color: #444; color: #FFC107; border: none; border-radius: 4px; padding: 8px 16px;
            }
            QPushButton:hover { background-color: #555; }
            QSpinBox { 
                background-color: #444; color: #FFC107; border: 1px solid #555; border-radius: 4px; padding: 4px;
            }
            QTextEdit { background-color: #444; color: #FFC107; border: 1px solid #555; border-radius: 4px; }
            QStatusBar { background-color: #333; color: #FFC107; }
        """)
        self.status.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.logs.setStyleSheet("font-family: Consolas; font-size: 12px;")

        self.load_config()

    def get_config(self):
        return {
            'ping_timeout_ms': self.spin_timeout.value(),
            'packet_count': self.spin_packets.value(),
            'packet_interval': self.spin_interval.value(),
            'scan_interval': self.spin_scan.value()
        }

    def load_config(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                self.spin_timeout.setValue(cfg.get('ping_timeout_ms', 5000))
                self.spin_packets.setValue(cfg.get('packet_count', 3))
                self.spin_interval.setValue(cfg.get('packet_interval', 1000))
                self.spin_scan.setValue(cfg.get('scan_interval', 30))
        except FileNotFoundError:
            self.save_config()

    def save_config(self):
        cfg = self.get_config()
        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=4)
        self.append_log("Конфиг сохранён")

    def start_server(self):
        if self.process:
            return
        self.thread = ServerThread()
        self.thread.log.connect(self.log_signal.emit)
        self.thread.process_started.connect(self.on_process_started)
        self.thread.start()
        self.status.setText("Сервер: запускается...")
        self.status_signal.emit("Запуск сервера...")
        self.append_log("Запуск сервера...")

    def on_process_started(self, process):
        self.process = process
        self.status.setText("Сервер: РАБОТАЕТ")
        self.status_signal.emit("Сервер запущен")
        self.append_log("Сервер запущен")

        # === ЗАПУСК ПИНГ-РАБОТНИКА ===
        interval = self.spin_scan.value()
        self.ping_worker = PingWorker(gui=self, interval=interval)
        self.ping_worker.start()
        self.append_log(f"PingWorker запущен (интервал {interval}с)")

    def stop_server(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except:
                pass
            self.process = None
            self.status.setText("Сервер: ОСТАНОВЛЕН")
            self.status_signal.emit("Сервер остановлен")
            self.append_log("Сервер остановлен")

        if self.ping_worker:
            self.ping_worker.stop()
            self.ping_worker = None
            self.append_log("PingWorker остановлен")

    def emergency_stop(self):
        if not self.process:
            return

        # Отправляем сообщение
        self.status_signal.emit("АВАРИЙНОЕ ВЫКЛЮЧЕНИЕ: сервер остановится через 5 сек...")
        self.append_log("АВАРИЙНОЕ ВЫКЛЮЧЕНИЕ: сервер остановится через 5 сек...")

        # Запускаем таймер на 5 секунд
        self.shutdown_timer.start(5000)

    def final_shutdown(self):
        if self.process:
            self.process.kill()
            self.process = None
            self.status.setText("Сервер: АВАРИЙНО ОСТАНОВЛЕН")
            self.status_signal.emit("АВАРИЙНОЕ ВЫКЛЮЧЕНИЕ!")
            self.append_log("АВАРИЙНОЕ ВЫКЛЮЧЕНИЕ!")

        if self.ping_worker:
            self.ping_worker.stop()
            self.ping_worker = None

    def backup(self):
        try:
            name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
            with zipfile.ZipFile(name, 'w') as zf:
                for root, _, files in os.walk('data'):
                    for f in files:
                        path = os.path.join(root, f)
                        arcname = os.path.relpath(path, 'data')
                        zf.write(path, f"data/{arcname}")
            self.append_log(f"Backup: {name}")
            QMessageBox.information(self, "Backup", f"Создан: {name}")
        except Exception as e:
            self.append_log(f"Ошибка backup: {e}")

    def append_log(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {text}")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = ServerGUI()
    win.show()
    sys.exit(app.exec())
    
    