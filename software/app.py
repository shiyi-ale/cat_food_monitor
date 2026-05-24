import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request
import paho.mqtt.client as mqtt


BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "cat_food_m2m.db"

MQTT_BROKER = "localhost"
MQTT_PORT = 1883

TOPIC_WEIGHT = "pet/weight/data"
TOPIC_STATUS = "pet/device/status"
TOPIC_CONTROL = "pet/device/control"

app = Flask(__name__)

db_lock = threading.Lock()
latest_lock = threading.Lock()

latest_data = {
    "device_id": "cat_bowl_01",
    "weight_kg": 0,
    "raw_adc": 0,
    "alarm_threshold_kg": 0.15,
    "status": "offline",
    "last_seen": None,
    "alarm": False,
}


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn():
    return sqlite3.connect(DB_FILE)


def init_db():
    with db_lock:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS weight_record (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                timestamp TEXT,
                weight_kg REAL,
                raw_adc REAL,
                alarm_threshold_kg REAL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS device_status (
                device_id TEXT PRIMARY KEY,
                status TEXT,
                timestamp TEXT,
                sample_interval_s INTEGER,
                alarm_threshold_kg REAL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS alarm_record (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                timestamp TEXT,
                weight_kg REAL,
                alarm_threshold_kg REAL,
                alarm_type TEXT
            )
            """
        )

        conn.commit()
        conn.close()


def save_weight(data):
    device_id = data.get("device_id", "unknown")
    weight_kg = float(data.get("weight_kg", 0))
    raw_adc = float(data.get("raw_adc", 0))
    threshold = float(data.get("alarm_threshold_kg", 0.15))
    timestamp = now_text()
    alarm = weight_kg < threshold

    with db_lock:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO weight_record
            (device_id, timestamp, weight_kg, raw_adc, alarm_threshold_kg)
            VALUES (?, ?, ?, ?, ?)
            """,
            (device_id, timestamp, weight_kg, raw_adc, threshold),
        )

        if alarm:
            cur.execute(
                """
                INSERT INTO alarm_record
                (device_id, timestamp, weight_kg, alarm_threshold_kg, alarm_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (device_id, timestamp, weight_kg, threshold, "LOW_FOOD"),
            )

        conn.commit()
        conn.close()

    with latest_lock:
        latest_data.update(
            {
                "device_id": device_id,
                "weight_kg": weight_kg,
                "raw_adc": raw_adc,
                "alarm_threshold_kg": threshold,
                "status": "online",
                "last_seen": timestamp,
                "alarm": alarm,
            }
        )


def save_status(data):
    device_id = data.get("device_id", "unknown")
    status = data.get("status", "online")
    sample_interval_s = int(data.get("sample_interval_s", 5))
    threshold = float(data.get("alarm_threshold_kg", 0.15))
    timestamp = now_text()

    with db_lock:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO device_status
            (device_id, status, timestamp, sample_interval_s, alarm_threshold_kg)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                status = excluded.status,
                timestamp = excluded.timestamp,
                sample_interval_s = excluded.sample_interval_s,
                alarm_threshold_kg = excluded.alarm_threshold_kg
            """,
            (device_id, status, timestamp, sample_interval_s, threshold),
        )

        conn.commit()
        conn.close()

    with latest_lock:
        latest_data.update(
            {
                "device_id": device_id,
                "status": status,
                "alarm_threshold_kg": threshold,
                "last_seen": timestamp,
            }
        )


mqtt_client = mqtt.Client(client_id="cat_food_pc_service")


def on_connect(client, userdata, flags, rc, properties=None):
    print("[MQTT] Connected, rc =", rc)
    if rc == 0:
        client.subscribe(TOPIC_WEIGHT)
        client.subscribe(TOPIC_STATUS)
        print("[MQTT] Subscribed:", TOPIC_WEIGHT, TOPIC_STATUS)


def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
        data = json.loads(payload)

        print("[MQTT]", msg.topic, data)

        if msg.topic == TOPIC_WEIGHT:
            save_weight(data)
        elif msg.topic == TOPIC_STATUS:
            save_status(data)

    except Exception as exc:
        print("[ERROR] MQTT message error:", exc)


def start_mqtt():
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/latest")
def api_latest():
    with latest_lock:
        return jsonify(dict(latest_data))


@app.route("/api/history")
def api_history():
    try:
        limit = int(request.args.get("limit", 30))
    except ValueError:
        limit = 30
    limit = max(1, min(limit, 200))

    with db_lock:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT timestamp, weight_kg, raw_adc, alarm_threshold_kg
            FROM weight_record
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )

        rows = cur.fetchall()
        conn.close()

    rows.reverse()

    return jsonify(
        [
            {
                "timestamp": row[0],
                "weight_kg": row[1],
                "raw_adc": row[2],
                "alarm_threshold_kg": row[3],
            }
            for row in rows
        ]
    )


@app.route("/api/alarms")
def api_alarms():
    with db_lock:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT timestamp, device_id, weight_kg, alarm_threshold_kg, alarm_type
            FROM alarm_record
            ORDER BY id DESC
            LIMIT 20
            """
        )

        rows = cur.fetchall()
        conn.close()

    return jsonify(
        [
            {
                "timestamp": row[0],
                "device_id": row[1],
                "weight_kg": row[2],
                "alarm_threshold_kg": row[3],
                "alarm_type": row[4],
            }
            for row in rows
        ]
    )


@app.route("/api/control", methods=["POST"])
def api_control():
    data = request.json or {}

    device_id = data.get("device_id", "cat_bowl_01")
    cmd = data.get("cmd", "set_config")

    payload = {
        "device_id": device_id,
        "cmd": cmd,
    }

    if "sample_interval_s" in data:
        payload["sample_interval_s"] = max(1, int(data["sample_interval_s"]))

    if "alarm_threshold_kg" in data:
        payload["alarm_threshold_kg"] = max(0, float(data["alarm_threshold_kg"]))

    mqtt_client.publish(TOPIC_CONTROL, json.dumps(payload), qos=0)

    return jsonify(
        {
            "success": True,
            "topic": TOPIC_CONTROL,
            "payload": payload,
        }
    )


if __name__ == "__main__":
    init_db()
    start_mqtt()
    print("[WEB] Open http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
