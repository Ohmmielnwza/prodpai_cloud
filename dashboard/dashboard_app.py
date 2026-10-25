from flask import Flask, jsonify, render_template, Response
import boto3
import json
import os
import time

app = Flask(__name__)

S3_BUCKET = os.environ.get("INFRA_S3_BUCKET", "my-bucket-name")
S3_KEY = os.environ.get("INFRA_S3_KEY", "dashboard/current_infra.json")
LAMBDA_NAME = os.environ.get("INFRA_LAMBDA_NAME", "DevSecOps-Infra-Aggregator")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1")

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

@app.route('/dashboard/api/data', methods=['GET'])
def get_data():
    data, etag = fetch_s3_data()
    if not data:
        return jsonify({"state": "waiting", "message": "No data in S3 yet", "resources": []})
        
    return jsonify({
        "state": "live",
        "source": "aws_api",
        "version": etag,
        "applied_at": data.get("update_time"),
        "summary": {
            "total": len(data.get("resources", [])),
            "health": {
                "healthy": sum(1 for r in data["resources"] if r.get("health") == "healthy"),
                "warning": sum(1 for r in data["resources"] if r.get("health") == "warning"),
                "error": sum(1 for r in data["resources"] if r.get("health") == "error")
            }
        },
        "resources": data.get("resources", [])
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
    app.run(host='0.0.0.0', port=5000, threaded=True)