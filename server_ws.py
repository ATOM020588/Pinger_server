import asyncio
import websockets
import json
import os
import subprocess
import csv
from datetime import datetime
import pickle

# === КОНФИГ ===
CONFIG = {
    "ping_timeout_ms": 3000,
    "packet_count": 1,
    "packet_interval": 1000,
    "scan_interval": 240
}

# === ПУТИ ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MAPS_DIR = os.path.join(DATA_DIR, "maps")
OPERATORS_DIR = os.path.join(DATA_DIR, "operators")
GLOBALS_DIR = os.path.join(DATA_DIR, "globals")

os.makedirs(MAPS_DIR, exist_ok=True)
os.makedirs(OPERATORS_DIR, exist_ok=True)
os.makedirs(GLOBALS_DIR, exist_ok=True)


# === ЛОГИРОВАНИЕ ===
def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S')} - {msg}"
    print(line)
    log_path = os.path.join("../logs", "server.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# === ПИНГ УСТРОЙСТВА ===
def ping_device(ip, timeout_ms):
    try:
        cmd = ['ping', '-n', '1', '-w', str(timeout_ms), ip]
        output = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, timeout=timeout_ms/1000 + 2
        )
        return {"success": b'TTL=' in output, "ip": ip}
    except Exception:
        return {"success": False, "ip": ip}

# === ПОЛНЫЙ ПУТЬ К ФАЙЛУ ===
def get_full_path(path):
    """Безопасно строит путь внутри DATA_DIR"""
    full_path = os.path.normpath(os.path.join(DATA_DIR, path))
    if not full_path.startswith(os.path.abspath(DATA_DIR)):
        raise ValueError("Invalid path: outside DATA_DIR")
    return full_path

# === РАБОТА С CSV ===
def read_csv(path):
    """Читает CSV файл и возвращает список словарей"""
    full_path = get_full_path(path)
    if not os.path.exists(full_path):
        return []
    
    try:
        with open(full_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            return list(reader)
    except Exception as e:
        log(f"Error reading CSV {path}: {e}")
        return []

def write_csv(path, data):
    """Записывает список словарей в CSV файл"""
    full_path = get_full_path(path)
    
    # Создаем директорию если не существует
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    
    if not data:
        # Если данных нет, создаем пустой файл с заголовками
        fieldnames = ["id", "date", "description", "tickets", "master", "executor", 
                     "created", "transferred", "callback", "work_start", "call_history",
                     "device_type", "device_id", "device_name", "device_ip"]
        with open(full_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
        return True
    
    try:
        fieldnames = list(data[0].keys())
        with open(full_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)
        return True
    except Exception as e:
        log(f"Error writing CSV {path}: {e}")
        return False

# === ОБРАБОТЧИК КЛИЕНТА ===
async def handler(websocket):
    client_ip = websocket.remote_address[0]
    log(f"Client connected: {client_ip}")

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                action = data.get("action")
                request_id = data.get("request_id")
                response = {"request_id": request_id, "success": False, "error": "Unknown action"}

                log(f"Action: {action} | Path: {data.get('path', data.get('filename', ''))} | Client: {client_ip}")

                # === ПИНГ ===
                if action == "ping":
                    ip = data.get("ip")
                    timeout = data.get("timeout", CONFIG["ping_timeout_ms"])
                    if ip:
                        result = ping_device(ip, timeout)
                        response = {"request_id": request_id, **result}
                    else:
                        response["error"] = "IP not provided"

                # === СПИСОК КАРТ ===
                elif action == "list_maps":
                    try:
                        files = [
                            f for f in os.listdir(MAPS_DIR)
                            if f.endswith(".json")
                        ]
                        response = {"request_id": request_id, "success": True, "files": files}
                    except Exception as e:
                        response["error"] = str(e)

                # === ЧТЕНИЕ ФАЙЛА (универсально) ===
                elif action == "file_get":
                    path = data.get("path") or data.get("filename")
                    if not path:
                        response["error"] = "No path or filename"
                    else:
                        file_path = get_full_path(path)
                        if os.path.exists(file_path) and file_path.endswith(".json"):
                            try:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    file_data = json.load(f)
                                response = {"request_id": request_id, "success": True, "data": file_data}
                            except Exception as e:
                                response["error"] = f"Read error: {e}"
                        else:
                            response["error"] = "File not found or not JSON"

                # === СОХРАНЕНИЕ ФАЙЛА (универсально) ===
                elif action == "file_put":
                    path = data.get("path") or data.get("filename")
                    file_data = data.get("data")
                    if not path or not isinstance(file_data, (dict, list)):
                        response["error"] = "Invalid path or data"
                    else:
                        file_path = get_full_path(path)
                        try:
                            os.makedirs(os.path.dirname(file_path), exist_ok=True)
                            with open(file_path, "w", encoding="utf-8") as f:
                                json.dump(file_data, f, ensure_ascii=False, indent=4)
                            response = {"request_id": request_id, "success": True}
                        except Exception as e:
                            response["error"] = f"Write error: {e}"

                # === ЧТЕНИЕ CSV ===
                elif action == "csv_read":
                    path = data.get("path")
                    if not path:
                        response["error"] = "No path provided"
                    else:
                        csv_data = read_csv(path)
                        response = {"request_id": request_id, "success": True, "data": csv_data}

                # === ЗАПИСЬ CSV ===
                elif action == "csv_write":
                    path = data.get("path")
                    csv_data = data.get("data")
                    if not path or not isinstance(csv_data, list):
                        response["error"] = "Invalid path or data"
                    else:
                        success = write_csv(path, csv_data)
                        if success:
                            response = {"request_id": request_id, "success": True}
                        else:
                            response["error"] = "Write error"

                # === АУТЕНТИФИКАЦИЯ ===
                elif action == "auth_login":
                    login = data.get("login")
                    password_hash = data.get("password_hash")
                    if not login or not password_hash:
                        response["error"] = "Логин и пароль обязательны"
                    else:
                        users_path = os.path.join(OPERATORS_DIR, "users.json")
                        if not os.path.exists(users_path):
                            response["error"] = "Пользователи не найдены"
                        else:
                            try:
                                with open(users_path, "r", encoding="utf-8") as f:
                                    users = json.load(f)
                                user = next((u for u in users if u.get("login") == login), None)
                                if user and user.get("password") == password_hash:
                                    # Убираем пароль из ответа
                                    safe_user = {k: v for k, v in user.items() if k != "password"}
                                    safe_user["last_activity"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    # Обновляем last_activity
                                    for u in users:
                                        if u["id"] == user["id"]:
                                            u["last_activity"] = safe_user["last_activity"]
                                    with open(users_path, "w", encoding="utf-8") as f:
                                        json.dump(users, f, ensure_ascii=False, indent=4)
                                    response = {"request_id": request_id, "success": True, "user": safe_user}
                                else:
                                    response["error"] = "Неверный логин или пароль"
                            except Exception as e:
                                response["error"] = f"Ошибка: {e}"
                                
                # === ОПЕРАТОРЫ ===               
                elif action == "list_operators":
                    users_path = os.path.join(OPERATORS_DIR, "users.json")
                    if not os.path.exists(users_path):
                        response["error"] = "Файл пользователей не найден"
                    else:
                        try:
                            with open(users_path, "r", encoding="utf-8") as f:
                                users = json.load(f)
                            # не возвращаем пароли
                            for u in users:
                                u.pop("password", None)
                            response = {"request_id": request_id, "success": True, "operators": users}
                        except Exception as e:
                            response["error"] = f"Ошибка чтения: {e}"
                
                # === ПРОВЕРКА ИЗМЕНЕНИЙ PINGOK НА КАРТЕ ===
                elif action == "check_ping_updates":
                    map_id = data.get("map_id")
                    client_hashes = data.get("hashes", {})  # {index: hash(pingok), ...}

                    if not map_id:
                        response["error"] = "map_id required"
                    else:
                        file_path = get_full_path(f"maps/map_{map_id}.json")
                        if not os.path.exists(file_path):
                            response["error"] = "Map not found"
                        else:
                            try:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    current_map = json.load(f)

                                switches = current_map.get("switches", [])
                                updates = []

                                for idx, switch in enumerate(switches):
                                    current_pingok = switch.get("pingok", False)
                                    # Приводим к строке для стабильного хеша
                                    current_hash = str(current_pingok).lower()

                                    client_hash = client_hashes.get(str(idx))

                                    if client_hash != current_hash:
                                        updates.append({
                                            "index": idx,
                                            "pingok": current_pingok
                                        })

                                response = {
                                    "request_id": request_id,
                                    "success": True,
                                    "updates": updates,
                                    "count": len(updates)
                                }

                                if updates:
                                    log(f"Ping updates sent for map {map_id}: {len(updates)} changes")

                            except Exception as e:
                                response["error"] = f"Read error: {e}"
                                
                # === МАССОВЫЙ ПИНГ УСТРОЙСТВ НА КАРТЕ ===
                elif action == "ping_switches":
                    ping_data = data.get("ping_data", [])  # [{ "index": int, "ip": str }, ...]
                    timeout_ms = data.get("timeout_ms", CONFIG["ping_timeout_ms"])

                    if not ping_data:
                        response["error"] = "No devices to ping"
                    else:
                        # Собираем только те, у кого есть IP
                        ips_to_ping = [item["ip"] for item in ping_data if item.get("ip")]
                        tasks = [asyncio.to_thread(ping_device, ip, timeout_ms) for ip in ips_to_ping]

                        try:
                            ping_results = await asyncio.gather(*tasks)

                            # Формируем ответ
                            results = []
                            ping_idx = 0
                            for item in ping_data:
                                idx = item["index"]
                                if item.get("ip"):
                                    res = ping_results[ping_idx]
                                    results.append({"index": idx, "success": res["success"]})
                                    ping_idx += 1
                                else:
                                    results.append({"index": idx, "success": False})

                            response = {
                                "request_id": request_id,
                                "success": True,
                                "results": results
                            }
                            log(f"Ping switches completed: {len(results)} devices (map from {client_ip})")
                        except Exception as e:
                            response["error"] = f"Ping error: {str(e)}"
                            log(f"Ping switches error: {e}")

                # === ОТПРАВКА ОТВЕТА ===
                await websocket.send(json.dumps(response))

            except json.JSONDecodeError:
                await websocket.send(json.dumps({"error": "Invalid JSON", "request_id": data.get("request_id")}))
            except Exception as e:
                log(f"Handler error: {e}")
                await websocket.send(json.dumps({"error": str(e), "request_id": data.get("request_id")}))
    except websockets.ConnectionClosed:
        log(f"Client disconnected: {client_ip}")
    except Exception as e:
        log(f"Connection error: {e}")

# === ЗАПУСК СЕРВЕРА ===
async def main():
    host = "192.168.0.56"
    port = 8081
    log(f"WebSocket server STARTED → ws://{host}:{port}")
    async with websockets.serve(handler, host, port):
        await asyncio.Future()

def run_server():
    asyncio.run(main())

if __name__ == "__main__":
    run_server()
