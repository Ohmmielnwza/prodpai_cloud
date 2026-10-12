#!/bin/bash
sleep 15

apt-get update -y && apt-get install -y docker.io awscli jq curl

TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
sudo hostnamectl set-hostname "$INSTANCE_ID"

sudo systemctl restart falcosidekick || true

systemctl start docker
systemctl enable docker

ECR_REGISTRY=$(echo "${ECR_URL}" | cut -d'/' -f1)

aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $ECR_REGISTRY

docker pull ${ECR_URL}:${IMAGE_TAG}
docker run -d -p 80:80 ${ECR_URL}:${IMAGE_TAG}