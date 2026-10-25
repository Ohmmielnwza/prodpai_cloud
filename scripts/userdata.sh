#!/bin/bash
sleep 15

apt-get update -y && apt-get install -y docker.io unzip curl
systemctl start docker && systemctl enable docker

curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/aws.zip" && unzip -q /tmp/aws.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws*

TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
hostnamectl set-hostname "$INSTANCE_ID"

systemctl restart falco || true
systemctl restart falcosidekick || true

ECR_REGISTRY=$(echo "${ECR_URL}" | cut -d'/' -f1)

aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin $ECR_REGISTRY

# เวอร์ชันที่ต้องรันอ่านจาก SSM Parameter Store ตอนบูต ไม่ได้ฝังมากับ launch template
#
# เหตุผล: การ deploy เวอร์ชันใหม่จึงเป็นแค่การเปลี่ยนค่าใน SSM แล้วสั่ง instance refresh
# ไม่ต้องสร้าง launch template version ใหม่ทุกครั้ง และฝั่ง CD ไม่ต้องมีสิทธิ์แก้ launch template
IMAGE_TAG=$(aws ssm get-parameter --name "${IMAGE_TAG_PARAM}" --region ${AWS_REGION} \
  --query 'Parameter.Value' --output text 2>/dev/null)

# อ่าน SSM ไม่ได้ (พารามิเตอร์หาย หรือสิทธิ์ไม่พอ) อย่าปล่อยให้เครื่องขึ้นมาแบบไม่มีแอป
# ปล่อยให้ ELB health check จับว่า unhealthy แล้ว ASG เปลี่ยนเครื่องให้ ดีกว่ารันเวอร์ชันผิด
if [ -z "$IMAGE_TAG" ] || [ "$IMAGE_TAG" = "None" ]; then
  echo "cannot read image tag from SSM parameter ${IMAGE_TAG_PARAM}" >&2
  exit 1
fi

echo "starting ${ECR_URL}:$IMAGE_TAG"
docker pull ${ECR_URL}:$IMAGE_TAG
docker run -d --restart always -p 80:8080 ${ECR_URL}:$IMAGE_TAG
