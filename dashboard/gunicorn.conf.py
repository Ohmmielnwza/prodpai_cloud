# gunicorn อ่านไฟล์นี้อัตโนมัติเมื่อสั่ง `gunicorn dashboard_app:app` จากโฟลเดอร์ dashboard/
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
# /api/events เป็น stream ที่ค้างการเชื่อมต่อไว้ ถ้าใช้ sync worker จะยึด worker ทั้งตัวจน /api/data ค้าง
# จึงใช้ thread worker และ process เดียว (cache ข้อมูลล่าสุดอยู่ในหน่วยความจำของ process)
worker_class = "gthread"
workers = 1
threads = int(os.environ.get("GUNICORN_THREADS", "16"))
timeout = 120
