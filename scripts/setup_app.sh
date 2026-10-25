#!/bin/bash
sleep 10

# 1. อัปเดต OS และติดตั้ง Python Libraries ที่จำเป็น
# (บน Ubuntu 24.04 การใช้ apt-get ติดตั้งไลบรารีของ Python จะปลอดภัยที่สุด ไม่ติด Error PEP 668)
apt-get update -y
apt-get install -y python3-pip python3-flask python3-boto3 curl

# 2. ตั้งค่า Hostname ให้เป็น Instance ID (เผื่อใช้ดู Log ง่ายๆ)
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
hostnamectl set-hostname "dashboard-$INSTANCE_ID"

# 3. สร้างโฟลเดอร์สำหรับแอปพลิเคชัน
mkdir -p /home/ubuntu/dashboard/templates

# 4. แปลงโค้ด Base64 จาก Terraform กลับมาเป็นไฟล์ Python และ HTML
# (Terraform จะส่งไฟล์โค้ดที่เราเขียนไว้ มายัดใส่ตัวแปรพวกนี้ให้อัตโนมัติ)
echo "${app_py_b64}" | base64 -d > /home/ubuntu/dashboard/dashboard.py
echo "${index_html_b64}" | base64 -d > /home/ubuntu/dashboard/templates/index.html
chown -R ubuntu:ubuntu /home/ubuntu/dashboard

# 5. สร้าง Systemd Service เพื่อให้ Dashboard รันอยู่เบื้องหลังเสมอ และเปิดทำงานอัตโนมัติเมื่อเครื่องรีบูต
cat << 'EOF' > /etc/systemd/system/dashboard.service
[Unit]
Description=DevSecOps Dashboard Flask App
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/dashboard

# รับค่า Environment Variables จาก Terraform
Environment="INFRA_S3_BUCKET=${s3_bucket}"
Environment="INFRA_LAMBDA_NAME=${lambda_name}"
Environment="AWS_DEFAULT_REGION=${region}"
Environment="PORT=5000"
Environment="HOST=0.0.0.0"

ExecStart=/usr/bin/python3 dashboard.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# 6. รีโหลด Systemd และสั่งเริ่มการทำงานของ Dashboard
systemctl daemon-reload
systemctl enable dashboard
systemctl start dashboard