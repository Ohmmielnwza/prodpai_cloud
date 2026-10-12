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

aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $ECR_REGISTRY

docker pull ${ECR_URL}:${IMAGE_TAG}
docker run -d --restart always -p 80:8080 ${ECR_URL}:${IMAGE_TAG}