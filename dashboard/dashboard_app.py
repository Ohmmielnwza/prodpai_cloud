"""
เว็บ dashboard ที่รันบน EC2 ใน private subnet

ตัวแอปไม่ได้คุย AWS API เองเลย (นอกจากปุ่ม force-refresh) หน้าที่กวาดข้อมูลเป็นของ
Lambda prod-pai-Infra-Aggregator ที่เขียน JSON ลง S3 ทุก 2 นาที แอปนี้แค่
อ่านไฟล์นั้นผ่าน S3 Gateway Endpoint แล้วส่งต่อให้หน้าเว็บ

ทุก route อยู่ใต้ /dashboard เพราะ ALB ส่ง path มาตามที่ผู้ใช้พิมพ์ (rewrite ไม่ได้)
"""

from flask import Flask, jsonify, render_template, Response
import boto3
import json
import os
import time

app = Flask(__name__)

S3_BUCKET = os.environ.get("INFRA_S3_BUCKET", "prod-pai-logs")
S3_KEY = os.environ.get("INFRA_S3_KEY", "dashboard/current_infra.json")
LAMBDA_NAME = os.environ.get("INFRA_LAMBDA_NAME", "prod-pai-Infra-Aggregator")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# ใช้เก็บ ETag เพื่อเช็คว่าไฟล์ใน S3 มีการอัปเดตหรือไม่
cache = {"etag": None, "data": None}


def fetch_s3_data():
    s3 = boto3.client('s3', region_name=AWS_REGION)
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
            "detail": f"รอ Lambda {LAMBDA_NAME} เขียน s3://{S3_BUCKET}/{S3_KEY} รอบแรก",
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
    try:
        lam = boto3.client('lambda', region_name=AWS_REGION)
        lam.invoke(FunctionName=LAMBDA_NAME, InvocationType='RequestResponse')
        # ล้าง Cache เพื่อบังคับดึงใหม่
        cache['etag'] = None
        return jsonify({"status": "success", "message": "AWS Infrastructure Synced!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/dashboard/api/events', methods=['GET'])
def events():
    def stream():
        last_etag = cache['etag']
        yield "retry: 3000\n: connected\n\n"
        while True:
            time.sleep(2)
            _, current_etag = fetch_s3_data()
            if current_etag != "error" and current_etag != last_etag:
                last_etag = current_etag
                yield f"event: change\ndata: {current_etag}\n\n"

    return Response(stream(), mimetype="text/event-stream")


@app.route('/dashboard')
@app.route('/dashboard/')
def dashboard():
    return render_template('index.html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)  # nosemgrep
