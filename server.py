import http.server
import socketserver
import json
import os
import subprocess
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import time

class PingServer(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.config = self.load_config()
        super().__init__(*args, directory='data', **kwargs)

    def load_config(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {"ping_timeout": 5, "packet_count": 3, "packet_interval": 1000}

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == '/ping':
            ip = query.get('ip', [''])[0]
            timeout = int(query.get('timeout', [self.config.get('ping_timeout', 5)])[0])
            packet_count = int(query.get('packet_count', [self.config.get('packet_count', 3)])[0])
            packet_interval = int(query.get('packet_interval', [self.config.get('packet_interval', 1000)])[0])
            result = self.ping_device(ip, timeout, packet_count, packet_interval)
            self.send_json(result)

        elif parsed.path.startswith('/maps') or parsed.path.startswith('/images'):
            super().do_GET()

        else:
            self.send_response(404)
            self.end_headers()

    def ping_device(self, ip, timeout, packet_count, packet_interval):
        success = False
        for i in range(packet_count):
            try:
                cmd = ['ping', '-n', '1', '-w', str(timeout * 1000), ip]
                output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=timeout+2)
                if b'TTL=' in output:
                    success = True
                    break
            except:
                pass
            if i < packet_count - 1:
                time.sleep(packet_interval / 1000)
        return {"success": success, "ip": ip}

    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        msg = f"{datetime.now().strftime('%H:%M:%S')} - {format % args}\n"
        with open('logs/server.log', 'a', encoding='utf-8') as f:
            f.write(msg)
        print(msg.strip())

if __name__ == '__main__':
    os.makedirs('logs', exist_ok=True)
    PORT = 8081

    server = socketserver.TCPServer(("", PORT), PingServer)
    server.allow_reuse_address = True

    with server as httpd:
        print(f"Сервер запущен: http://localhost:{PORT}")
        print("Пинг: http://localhost:8081/ping?ip=8.8.8.8")
        httpd.serve_forever()