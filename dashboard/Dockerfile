# ใช้ Python 3.10 หรือ 3.11 ที่เบาบาง
FROM python:3.11-slim

WORKDIR /app

# คัดลอกและติดตั้ง Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt boto3 flask gunicorn

# คัดลอกโค้ดทั้งหมด (รวมโฟลเดอร์ templates)
COPY . .

EXPOSE 5000

# ใช้ Gunicorn รันแอปพลิเคชัน (หรือใช้ python dashboard_app.py ก็ได้)
CMD ["gunicorn", "--config", "gunicorn.conf.py", "dashboard_app:app"]