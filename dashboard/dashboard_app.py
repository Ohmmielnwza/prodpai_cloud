"""
เว็บ dashboard ที่รันบน EC2 ใน private subnet

ตัวแอปไม่ได้คุย AWS API เองเลย (นอกจากปุ่ม force-refresh) หน้าที่กวาดข้อมูลเป็นของ
Lambda prod-pai-Infra-Aggregator ที่เขียน JSON ลง S3 เมื่อมีคนกดปุ่ม Sync AWS
(ไม่มี schedule หรือ EventBridge) แอปนี้แค่อ่านไฟล์นั้นผ่าน S3 Gateway Endpoint แล้วส่งต่อให้หน้าเว็บ

ทุก route อยู่ใต้ /dashboard เพราะ ALB ส่ง path มาตามที่ผู้ใช้พิมพ์ (rewrite ไม่ได้)
"""

from flask import Flask, jsonify, render_template, Response
from botocore.config import Config
import boto3
import json
import math
import os
import threading
import time

app = Flask(__name__)

S3_BUCKET = os.environ.get("INFRA_S3_BUCKET", "prod-pai-logs")
S3_KEY = os.environ.get("INFRA_S3_KEY", "dashboard/current_infra.json")
LAMBDA_NAME = os.environ.get("INFRA_LAMBDA_NAME", "prod-pai-Infra-Aggregator")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# ใช้เก็บ ETag เพื่อเช็คว่าไฟล์ใน S3 มีการอัปเดตหรือไม่
cache = {"etag": None, "data": None}

# ปุ่ม Sync: กดได้ทีละครั้ง และเว้นอย่างน้อยเท่านี้ระหว่างครั้ง
# lock อยู่ในหน่วยความจำ ใช้ได้เพราะ gunicorn รัน process เดียว (gunicorn.conf.py)
# และ dashboard มีเครื่องเดียว ถ้าเพิ่ม worker หรือเครื่องเมื่อไหร่ ต้องย้ายไปใช้ lock กลาง
# ข้อนี้ไม่ใช่การยืนยันตัวตน ใครเข้าหน้า dashboard ได้ก็ยังกดได้
SYNC_COOLDOWN = int(os.environ.get("SYNC_COOLDOWN_SECONDS", "30"))
sync_lock = threading.Lock()
sync_state = {"last_started": None}

# aggregator มี timeout 120 วินาที แต่ boto3 ตั้ง read timeout ไว้ 60 และ retry เองเมื่อหมดเวลา
# ถ้าไม่ปิด กดปุ่มครั้งเดียวจะยิง Lambda ซ้ำหลายรอบ
LAMBDA_CONFIG = Config(read_timeout=130, retries={"total_max_attempts": 1})

# สร้าง client ครั้งเดียวตอนโหลดแอป: client ใช้ข้ามหลาย thread ได้ แต่ boto3.client()
# ที่เรียกพร้อมกันจากหลาย thread (session เริ่มต้นตัวเดียวกัน) ไม่ thread-safe
s3 = boto3.client('s3', region_name=AWS_REGION)
lam = boto3.client('lambda', region_name=AWS_REGION, config=LAMBDA_CONFIG)

# SSE: ส่ง comment เปล่าทุกช่วงนี้ เพื่อให้รู้ตัวเมื่อเบราว์เซอร์ปิดแท็บไปแล้ว
# (ข้อมูลเปลี่ยนแค่ตอนกด Sync ถ้าไม่ส่งอะไรเลย thread ของแท็บที่ปิดไปจะวนค้างอยู่ตลอด
# จน thread 16 ตัวของ gunicorn หมด) และให้สั้นกว่า idle timeout 60 วินาทีของ ALB
SSE_HEARTBEAT = 15
# ตัด stream ทิ้งเป็นระยะ เบราว์เซอร์ต่อใหม่เองตาม retry ที่ส่งไป
SSE_MAX_SECONDS = 300


def fetch_s3_data():
    try:
        # เช็ค ETag ก่อนว่าไฟล์เปลี่ยนไหม จะได้ไม่ต้องโหลดไฟล์เต็มๆ ทุกรอบ
        head = s3.head_object(Bucket=S3_BUCKET, Key=S3_KEY)
        etag = head['ETag'].strip('"')

        if cache['etag'] != etag or cache['data'] is None:
            obj = s3.get_object(Bucket=S3_BUCKET, Key=S3_KEY)
            raw_data = obj['Body'].read().decode('utf-8')
            cache['data'] = json.loads(raw_data)
            cache['etag'] = etag

        return cache['data'], cache['etag']
    except Exception as e:
        app.logger.error(f"S3 Fetch Error: {e}")
        return None, "error"


@app.route('/dashboard/health', methods=['GET'])
def health():
    """
    ALB ใช้ path นี้เช็คว่าเครื่องยังรับงานได้ไหม

    ตั้งใจให้ตอบ 200 เสมอโดยไม่แตะ S3 เพราะถ้าผูกสุขภาพของเว็บไว้กับการมีข้อมูลใน S3
    ช่วงที่ aggregator ยังไม่ได้ยิงรอบแรก ALB จะถอดเครื่องออกทั้งที่ตัวเว็บปกติดี
    """
    return jsonify({"status": "ok"}), 200


@app.route('/dashboard/api/data', methods=['GET'])
def get_data():
    data, etag = fetch_s3_data()
    if not data:
        return jsonify({
            "state": "waiting",
            "source": "waiting",
            "message": "ยังไม่มีข้อมูลใน S3",
            "detail": f"กด Sync AWS เพื่อให้ Lambda {LAMBDA_NAME} เขียน s3://{S3_BUCKET}/{S3_KEY} รอบแรก",
            "resources": [],
            "topology": {"regions": []},
            "health": {"healthy": 0, "unhealthy": 0, "total_targets": 0, "compromised": []},
        })

    resources = data.get("resources", [])
    return jsonify({
        "state": "live",
        "source": data.get("source", "aws_api"),
        "version": etag,
        "applied_at": data.get("update_time"),
        "region": data.get("region"),
        "project": data.get("project"),
        "environment": data.get("environment"),
        # ชั้นข้อมูลที่หน้า topology ใช้วาด region -> vpc -> az -> public/private
        "topology": data.get("topology", {"regions": []}),
        # healthy นับจากสมาชิก target group เท่านั้น (คำนวณมาจาก Lambda แล้ว)
        "health": data.get("health", {}),
        "incidents": data.get("incidents", []),
        "cost": data.get("cost", {}),
        "summary": {
            "total": len(resources),
            "health": {
                "healthy": sum(1 for r in resources if r.get("health") == "healthy"),
                "warning": sum(1 for r in resources if r.get("health") == "warning"),
                "error": sum(1 for r in resources if r.get("health") == "error"),
            },
        },
        "resources": resources,
    })


@app.route('/dashboard/api/force-refresh', methods=['POST'])
def force_refresh():
    if not sync_lock.acquire(blocking=False):
        return jsonify({"status": "busy", "message": "A sync is already running."}), 409
    try:
        last = sync_state["last_started"]
        wait = SYNC_COOLDOWN - (time.monotonic() - last) if last is not None else 0
        if wait > 0:
            retry_after = math.ceil(wait)
            resp = jsonify({"status": "cooldown", "retry_after": retry_after,
                            "message": f"Synced recently. Try again in {retry_after}s."})
            resp.headers["Retry-After"] = str(retry_after)
            return resp, 429
        sync_state["last_started"] = time.monotonic()

        result = lam.invoke(FunctionName=LAMBDA_NAME, InvocationType='RequestResponse')
        # invoke สำเร็จไม่ได้แปลว่า Lambda รันผ่าน ถ้าโค้ดข้างใน raise จะมาเป็น FunctionError
        if result.get("FunctionError"):
            app.logger.error(f"{LAMBDA_NAME} failed: {result['Payload'].read()[:500]}")
            return jsonify({"status": "error",
                            "message": f"{LAMBDA_NAME} failed. Check its CloudWatch logs."}), 502

        # ล้าง Cache เพื่อบังคับดึงใหม่
        cache['etag'] = None
        data, _ = fetch_s3_data()
        return jsonify({"status": "success", "message": "AWS Infrastructure Synced!",
                        "applied_at": (data or {}).get("update_time")})
    except Exception as e:
        # รายละเอียดเก็บไว้ใน log ไม่ส่งข้อความ error ของ AWS ออกไปให้หน้าเว็บ
        app.logger.error(f"force-refresh failed: {e}")
        return jsonify({"status": "error", "message": "Could not reach the aggregator Lambda."}), 500
    finally:
        sync_lock.release()


@app.route('/dashboard/api/events', methods=['GET'])
def events():
    def stream():
        last_etag = cache['etag']
        yield "retry: 3000\n: connected\n\n"
        started = last_sent = time.monotonic()
        while time.monotonic() - started < SSE_MAX_SECONDS:
            time.sleep(2)
            _, current_etag = fetch_s3_data()
            if current_etag != "error" and current_etag != last_etag:
                last_etag = current_etag
                yield f"event: change\ndata: {current_etag}\n\n"
                last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= SSE_HEARTBEAT:
                # ถ้าแท็บปิดไปแล้ว การเขียนตรงนี้จะ error แล้ว gunicorn ปิด generator คืน thread ให้
                yield ": ping\n\n"
                last_sent = time.monotonic()

    return Response(stream(), mimetype="text/event-stream")


@app.route('/dashboard')
@app.route('/dashboard/')
def dashboard():
    return render_template('index.html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)  # nosemgrep
