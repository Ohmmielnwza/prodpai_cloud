import json
import boto3
import os
from datetime import datetime, timedelta

# ==========================================
# ตั้งค่า AWS Clients
# ==========================================
region = os.environ.get('AWS_REGION', 'us-east-1')
ec2 = boto3.client('ec2', region_name=region)
cw = boto3.client('cloudwatch', region_name=region)
s3 = boto3.client('s3', region_name=region)
elbv2 = boto3.client('elbv2', region_name=region)

S3_BUCKET = os.environ.get('S3_BUCKET')
S3_KEY = os.environ.get('S3_KEY', 'dashboard/current_infra.json')

def get_cpu_utilization(instance_id):
    """
    ฟังก์ชันสำหรับดึงค่า CPU เฉลี่ยย้อนหลัง 5 นาทีจาก CloudWatch
    """
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(minutes=5)
    try:
        response = cw.get_metric_statistics(
            Namespace='AWS/EC2',
            MetricName='CPUUtilization',
            Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=300,
            Statistics=['Average']
        )
        datapoints = response.get('Datapoints', [])
        if datapoints:
            return round(datapoints[0]['Average'], 2)
    except Exception as e:
        print(f"Failed to fetch CPU for {instance_id}: {e}")
    return 0.0

def lambda_handler(event, context):
    resources = []
    
    # ==========================================
    # 1. ดึงข้อมูล EC2 Instances และตรวจสอบ Compromise
    # ==========================================
    try:
        instances = ec2.describe_instances()
        for reservation in instances.get('Reservations', []):
            for inst in reservation.get('Instances', []):
                # แปลง Tags เป็น Dictionary
                tags = {t['Key']: t['Value'] for t in inst.get('Tags', [])}
                name = tags.get('Name', inst['InstanceId'])
                
                state_name = inst['State']['Name']
                
                # 🚨 ตรวจสอบสถานะ Compromised จาก Tag
                is_compromised = tags.get('SecurityStatus') == 'Compromised'
                
                if is_compromised:
                    health = "error"
                    status = "COMPROMISED (ISOLATED)"
                else:
                    health = "healthy" if state_name == 'running' else "warning"
                    if state_name in ['stopped', 'terminated']:
                        health = "inactive"
                    status = state_name

                # ดึง CPU Metric เฉพาะเครื่องที่ทำงานอยู่
                cpu_metric = ""
                if state_name == 'running':
                    cpu = get_cpu_utilization(inst['InstanceId'])
                    cpu_metric = f" | CPU: {cpu}%"

                resources.append({
                    "id": inst['InstanceId'],
                    "name": name,
                    "type": "aws_instance",
                    "category": "Compute",
                    "status": status,
                    "health": health,
                    "private_ip": inst.get('PrivateIpAddress', ''),
                    "public_ip": inst.get('PublicIpAddress', ''),
                    "az": inst.get('Placement', {}).get('AvailabilityZone', ''),
                    "spec": f"{inst['InstanceType']}{cpu_metric}",
                    "tags": tags,
                    "role": tags.get('Role', 'instance')
                })
    except Exception as e:
        print(f"Error fetching EC2: {e}")

    # ==========================================
    # 2. ดึงข้อมูล VPCs
    # ==========================================
    try:
        vpcs = ec2.describe_vpcs()
        for vpc in vpcs.get('Vpcs', []):
            tags = {t['Key']: t['Value'] for t in vpc.get('Tags', [])}
            resources.append({
                "id": vpc['VpcId'],
                "name": tags.get('Name', vpc['VpcId']),
                "type": "aws_vpc",
                "category": "Network",
                "status": vpc.get('State', 'available'),
                "health": "healthy" if vpc.get('State') == 'available' else "warning",
                "spec": vpc.get('CidrBlock', ''),
                "tags": tags
            })
    except Exception as e:
        print(f"Error fetching VPCs: {e}")

    # ==========================================
    # 3. ดึงข้อมูล Subnets
    # ==========================================
    try:
        subnets = ec2.describe_subnets()
        for subnet in subnets.get('Subnets', []):
            tags = {t['Key']: t['Value'] for t in subnet.get('Tags', [])}
            resources.append({
                "id": subnet['SubnetId'],
                "name": tags.get('Name', subnet['SubnetId']),
                "type": "aws_subnet",
                "category": "Network",
                "status": subnet['State'],
                "health": "healthy" if subnet['State'] == 'available' else "warning",
                "az": subnet['AvailabilityZone'],
                "spec": subnet['CidrBlock'],
                "tags": tags
            })
    except Exception as e:
        print(f"Error fetching Subnets: {e}")

    # ==========================================
    # 4. ดึงข้อมูล Load Balancers (ALB)
    # ==========================================
    try:
        lbs = elbv2.describe_load_balancers()
        for lb in lbs.get('LoadBalancers', []):
            resources.append({
                "id": lb['LoadBalancerName'], 
                "name": lb['LoadBalancerName'],
                "type": "aws_lb",
                "category": "Network",
                "status": lb['State']['Code'],
                "health": "healthy" if lb['State']['Code'] == 'active' else "warning",
                "endpoint": lb['DNSName'],
                "spec": lb['Type'],
                "tags": {}
            })
    except Exception as e:
        print(f"Error fetching ELBs: {e}")

    # ==========================================
    # สร้าง Payload ให้ตรงตามรูปแบบที่เว็บคาดหวัง
    # ==========================================
    payload = {
        "project": "DevSecOps Cloud",
        "environment": "Production",
        "region": region,
        "update_time": datetime.utcnow().isoformat() + "Z",
        "source": "live",
        "resources": resources
    }

    # ==========================================
    # อัปโหลด JSON ลง S3
    # ==========================================
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=S3_KEY,
            Body=json.dumps(payload, ensure_ascii=False, indent=2),
            ContentType='application/json'
        )
        print(f"Successfully uploaded to s3://{S3_BUCKET}/{S3_KEY}")
    except Exception as e:
        print(f"Error uploading to S3: {e}")
        raise e

    # คืนค่ากลับไปให้คนที่เรียก (เช่น เว็บเรียกมา หรือ EventBridge เรียกมา)
    return {
        'statusCode': 200,
        'body': json.dumps('Dashboard Data Synced Successfully')
    }