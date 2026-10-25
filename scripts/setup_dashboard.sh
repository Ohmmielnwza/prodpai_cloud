#!/bin/bash
sleep 15

# 1. ติดตั้ง Docker และ AWS CLI
apt-get update -y && apt-get install -y docker.io unzip curl
systemctl start docker && systemctl enable docker

curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/aws.zip" && unzip -q /tmp/aws.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws*

# 2. ตั้งชื่อ Hostname
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
hostnamectl set-hostname "dashboard-$INSTANCE_ID"

# 3. Login เข้า ECR
ECR_REGISTRY=$(echo "${ECR_URL}" | cut -d'/' -f1)
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin $ECR_REGISTRY

# 4. Pull Image และรัน Container ของ Dashboard
docker pull ${ECR_URL}:${IMAGE_TAG}
docker run -d --name dashboard \
  --restart always \
  -p 5000:5000 \
  -e INFRA_S3_BUCKET="${S3_BUCKET}" \
  -e INFRA_S3_KEY="${S3_KEY}" \
  -e INFRA_LAMBDA_NAME="${LAMBDA_NAME}" \
  -e AWS_DEFAULT_REGION="${AWS_REGION}" \
  ${ECR_URL}:${IMAGE_TAG}