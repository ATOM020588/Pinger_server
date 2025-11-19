import asyncio
import websockets
import json
import os
import subprocess
import csv
from datetime import datetime
import pickle
import base64

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
LISTS_DIR = os.path.join(DATA_DIR, "lists")
MODELS_DIR = os.path.join(DATA_DIR, "models")
IMAGES_DIR = os.path.join(DATA_DIR, "images")

os.makedirs(MAPS_DIR, exist_ok=True)
os.makedirs(OPERATORS_DIR, exist_ok=True)
os.makedirs(GLOBALS_DIR, exist_ok=True)
os.makedirs(LISTS_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)


# === ЛОГИРОВАНИЕ ===
def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S')} - {msg}"
    print(line)
    log_path = os.path.join(BASE_DIR, "logs", "server.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# === ПИНГ УСТРОЙСТВА ===
def ping_device(ip, timeout_ms):
    try:
        # Windows-style ping by default (change if needed for Linux)
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
    if not os.path.abspath(full_path).startswith(os.path.abspath(DATA_DIR)):
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
    client_ip = websocket.remote_address[0] if websocket.remote_address else "unknown"
    log(f"Client connected: {client_ip}")

    try:
        async for message in websocket:
            # Prepare a default response in case something goes wrong before we set it
            response = {"request_id": None, "success": False, "error": "Unknown action"}

            try:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    # Cannot parse JSON — respond with generic error (no request_id available)
                    await websocket.send(json.dumps({"request_id": None, "success": False, "error": "Invalid JSON"}, ensure_ascii=False))
                    continue

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
                        files = [f for f in os.listdir(MAPS_DIR) if f.endswith(".json")]
                        response = {"request_id": request_id, "success": True, "files": files}
                    except Exception as e:
                        response["error"] = str(e)

                # === ЧТЕНИЕ ФАЙЛА (универсально) ===
                elif action == "file_get":
                    path = data.get("path") or data.get("filename")
                    if not path:
                        response["error"] = "No path or filename"
                    else:
                        try:
                            file_path = get_full_path(path)
                        except Exception as e:
                            response["error"] = f"Invalid path: {e}"
                        else:
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
                        try:
                            file_path = get_full_path(path)
                        except Exception as e:
                            response["error"] = f"Invalid path: {e}"
                        else:
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
                                        if u.get("id") == user.get("id"):
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

                elif action == "save_operators":
                    users = data.get("operators")
                    users_path = os.path.join(OPERATORS_DIR, "users.json")
                    try:
                        with open(users_path, "w", encoding="utf-8") as f:
                            json.dump(users, f, ensure_ascii=False, indent=4)
                        response = {"request_id": request_id, "success": True}
                    except Exception as e:
                        response["error"] = f"Ошибка записи операторов: {e}"

                # === ГРУППЫ ===
                elif action == "list_groups":
                    groups_path = os.path.join(OPERATORS_DIR, "groups.json")
                    try:
                        if os.path.exists(groups_path):
                            with open(groups_path, "r", encoding="utf-8") as f:
                                groups = json.load(f)
                        else:
                            groups = []
                        response = {"request_id": request_id, "success": True, "groups": groups}
                    except Exception as e:
                        response["error"] = f"Ошибка чтения групп: {e}"

                elif action == "save_groups":
                    groups = data.get("groups")
                    groups_path = os.path.join(OPERATORS_DIR, "groups.json")
                    try:
                        with open(groups_path, "w", encoding="utf-8") as f:
                            json.dump(groups, f, ensure_ascii=False, indent=4)
                        response = {"request_id": request_id, "success": True}
                    except Exception as e:
                        response["error"] = f"Ошибка записи групп: {e}"

                # === ENGINEERS / MASTERS ===
                elif action == "list_engineers":
                    engineers_path = os.path.join(LISTS_DIR, "engineers.json")
                    try:
                        if os.path.exists(engineers_path):
                            with open(engineers_path, "r", encoding="utf-8") as f:
                                engineers = json.load(f)
                        else:
                            engineers = []
                        response = {"request_id": request_id, "success": True, "engineers": engineers}
                    except Exception as e:
                        response["error"] = f"Ошибка чтения инженеров: {e}"

                elif action == "save_engineers":
                    engineers = data.get("engineers", [])
                    engineers_path = os.path.join(LISTS_DIR, "engineers.json")
                    try:
                        with open(engineers_path, "w", encoding="utf-8") as f:
                            json.dump(engineers, f, ensure_ascii=False, indent=4)
                        response = {"request_id": request_id, "success": True}
                    except Exception as e:
                        response["error"] = f"Ошибка записи инженеров: {e}"

                elif action == "list_masters":
                    masters_path = os.path.join(LISTS_DIR, "masters.json")
                    try:
                        if os.path.exists(masters_path):
                            with open(masters_path, "r", encoding="utf-8") as f:
                                masters = json.load(f)
                        else:
                            masters = []
                        response = {"request_id": request_id, "success": True, "masters": masters}
                    except Exception as e:
                        response["error"] = f"Ошибка чтения мастеров: {e}"

                elif action == "save_masters":
                    masters = data.get("masters", [])
                    masters_path = os.path.join(LISTS_DIR, "masters.json")
                    try:
                        with open(masters_path, "w", encoding="utf-8") as f:
                            json.dump(masters, f, ensure_ascii=False, indent=4)
                        response = {"request_id": request_id, "success": True}
                    except Exception as e:
                        response["error"] = f"Ошибка записи мастеров: {e}"

                # === ПРОШИВКИ ===
                elif action == "list_firmwares":
                    fw_path = os.path.join(LISTS_DIR, "firmware.json")
                    try:
                        if os.path.exists(fw_path):
                            with open(fw_path, "r", encoding="utf-8") as f:
                                firmwares = json.load(f)
                        else:
                            firmwares = []
                        response = {"request_id": request_id, "success": True, "firmwares": firmwares}
                    except Exception as e:
                        response["error"] = f"Ошибка чтения прошивок: {e}"

                elif action == "save_firmwares":
                    firmwares = data.get("firmwares", [])
                    fw_path = os.path.join(LISTS_DIR, "firmware.json")
                    try:
                        with open(fw_path, "w", encoding="utf-8") as f:
                            json.dump(firmwares, f, ensure_ascii=False, indent=4)
                        response = {"request_id": request_id, "success": True}
                    except Exception as e:
                        response["error"] = f"Ошибка записи прошивок: {e}"

                # === MODELS ===
                elif action == "list_models":
                    path = os.path.join(MODELS_DIR, "models.json")
                    try:
                        if os.path.exists(path):
                            with open(path, "r", encoding="utf-8") as f:
                                models = json.load(f)
                        else:
                            models = []
                        response = {"request_id": request_id, "success": True, "models": models}
                    except Exception as e:
                        response["error"] = f"Ошибка чтения models.json: {e}"

                elif action == "load_model":
                    model_id = data.get("id")
                    path = os.path.join(MODELS_DIR, f"{model_id}.json")
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            model_data = json.load(f)
                        response = {"request_id": request_id, "success": True, "model": model_data}
                    except Exception as e:
                        response["error"] = f"Ошибка чтения модели: {e}"

                elif action == "save_model":
                    model_id = data.get("id")
                    model_data = data.get("model")
                    path = os.path.join(MODELS_DIR, f"{model_id}.json")

                    try:
                        # Обновляем список моделей (models.json)
                        models_list_path = os.path.join(MODELS_DIR, "models.json")
                        models = []
                        if os.path.exists(models_list_path):
                            with open(models_list_path, "r", encoding="utf-8") as f:
                                models = json.load(f)

                        models = [m for m in models if m.get("id") != model_id]
                        models.append({"id": model_id, "model_name": model_data.get("model_name", "")})

                        with open(models_list_path, "w", encoding="utf-8") as f:
                            json.dump(models, f, ensure_ascii=False, indent=4)

                        # Сохраняем тело модели
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump(model_data, f, ensure_ascii=False, indent=4)

                        response = {"request_id": request_id, "success": True}
                    except Exception as e:
                        response["error"] = f"Ошибка сохранения модели: {e}"

                elif action == "delete_model":
                    model_id = data.get("id")
                    models_list_path = os.path.join(MODELS_DIR, "models.json")
                    model_file_path = os.path.join(MODELS_DIR, f"{model_id}.json")

                    try:
                        if os.path.exists(model_file_path):
                            os.remove(model_file_path)

                        if os.path.exists(models_list_path):
                            with open(models_list_path, "r", encoding="utf-8") as f:
                                models = json.load(f)
                            models = [m for m in models if m.get("id") != model_id]
                            with open(models_list_path, "w", encoding="utf-8") as f:
                                json.dump(models, f, ensure_ascii=False, indent=4)

                        response = {"request_id": request_id, "success": True}
                    except Exception as e:
                        response["error"] = f"Ошибка удаления модели: {e}"

                # === IMAGES ===
                elif action == "upload_image":
                    filename = data.get("filename")
                    base64_data = data.get("image")

                    try:
                        if not filename or not base64_data:
                            raise ValueError("filename or image missing")

                        image_bytes = base64.b64decode(base64_data)
                        image_path = os.path.join(IMAGES_DIR, filename)

                        with open(image_path, "wb") as f:
                            f.write(image_bytes)

                        response = {"request_id": request_id, "success": True}

                    except Exception as e:
                        response["error"] = f"Ошибка загрузки изображения: {e}"

                elif action == "download_image":
                    filename = data.get("filename")
                    path = os.path.join(IMAGES_DIR, filename)
                    try:
                        if not os.path.exists(path):
                            raise FileNotFoundError("Image not found")
                        with open(path, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode()
                        response = {"request_id": request_id, "success": True, "image": b64}
                    except Exception as e:
                        response = {"request_id": request_id, "success": False, "image": None, "error": str(e)}

                # === MANAGEMENT VLAN ===
                elif action == "list_mngmt_vlan":
                    path = os.path.join(LISTS_DIR, "mngmtvlan.json")
                    try:
                        if os.path.exists(path):
                            with open(path, "r", encoding="utf-8") as f:
                                vlans = json.load(f)
                        else:
                            vlans = []
                        response = {"request_id": request_id, "success": True, "vlans": vlans}
                    except Exception as e:
                        response["error"] = f"Ошибка чтения VLAN: {e}"

                elif action == "save_mngmt_vlan":
                    vlans = data.get("vlans")
                    path = os.path.join(LISTS_DIR, "mngmtvlan.json")
                    try:
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump(vlans, f, ensure_ascii=False, indent=4)
                        response = {"request_id": request_id, "success": True}
                    except Exception as e:
                        response["error"] = f"Ошибка записи VLAN: {e}"

                # === MASS PING (последним) ===
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
                                idx = item.get("index")
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

                # === НЕИЗВЕСТНОЕ ДЕЙСТВИЕ ===
                else:
                    response = {"request_id": request_id, "success": False, "error": "Unknown action"}

                # === ОТПРАВКА ОТВЕТА ===
                await websocket.send(json.dumps(response, ensure_ascii=False))

            except Exception as e:
                log(f"Handler error: {e}")
                # Попробуем отправить ошибку клиенту (если есть request_id)
                try:
                    await websocket.send(json.dumps({"request_id": data.get("request_id") if 'data' in locals() and isinstance(data, dict) else None, "success": False, "error": str(e)}, ensure_ascii=False))
                except Exception:
                    # если отправка не удалась — просто логируем
                    log(f"Failed to send error to client: {e}")

    except websockets.ConnectionClosed:
        log(f"Client disconnected: {client_ip}")
    except Exception as e:
        log(f"Connection error: {e}")


# === ЗАПУСК СЕРВЕРА ===
async def main():
    host = "0.0.0.0"  # Слушаем на всех интерфейсах
    port = 8081
    log(f"WebSocket server STARTED → ws://{host}:{port}")
    async with websockets.serve(handler, host, port):
        await asyncio.Future()


def run_server():
    asyncio.run(main())


if __name__ == "__main__":
    run_server()
