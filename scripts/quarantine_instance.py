import json
import base64
import gzip
import boto3
import os
from datetime import datetime, timezone

# Connect AWS EC2 API
ec2 = boto3.client('ec2')
asg = boto3.client('autoscaling')  # เพิ่ม Client สำหรับ Auto Scaling
s3 = boto3.client('s3')

# Take Security Group' ID ( quarantine_security)
ISOLATED_SG_ID = os.environ.get('ISOLATED_SG_ID')

# ที่เก็บบันทึกเหตุการณ์ ให้ dashboard เอาไปแสดง timeline ได้
DATA_BUCKET = os.environ.get('DATA_BUCKET')
INCIDENT_PREFIX = os.environ.get('INCIDENT_PREFIX', 'dashboard/incidents/')


def already_quarantined(instance_data):
    """
    กัน Falco ยิงซ้ำ: ถ้าเครื่องถูกจับกักไปแล้วจะไม่ snapshot / detach ซ้ำอีกรอบ
    (เหตุการณ์เดียวมักยิง alert ออกมาหลายสิบบรรทัด ถ้าไม่กันไว้จะได้ snapshot เป็นสิบก้อน)
    """
    tags = {t['Key']: t['Value'] for t in instance_data.get('Tags', [])}
    return tags.get('SecurityStatus') == 'Compromised'


def detach_from_asg(instance_id, instance_data):
    """
    ถอดเครื่องออกจาก ASG ก่อนเป็นอย่างแรก

    เหตุผล: ถ้าปล่อยไว้ พอตัดเน็ตแล้ว ELB health check จะ fail แล้ว ASG จะ "ลบ" เครื่อง
    ที่เป็นหลักฐานทิ้งเอง ส่วน ShouldDecrementDesiredCapacity=False ทำให้ ASG
    ปั้นเครื่องใหม่ขึ้นมาแทนทันที ผู้ใช้ปลายทางจึงไม่รู้สึกว่าบริการหาย
    detach ยังถอดเครื่องออกจาก target group ของ ALB ให้ด้วยในตัว
    """
    tags = instance_data.get('Tags', [])
    asg_name = next((t['Value'] for t in tags if t['Key'] == 'aws:autoscaling:groupName'), None)

    if not asg_name:
        print(f"ℹ️ [INFO] {instance_id} ไม่ได้อยู่ใต้ ASG (เช่นเครื่อง dashboard) ข้ามขั้นตอน detach")
        return None

    print(f"🔧 Detaching {instance_id} from ASG: {asg_name}...")
    asg.detach_instances(
        InstanceIds=[instance_id],
        AutoScalingGroupName=asg_name,
        ShouldDecrementDesiredCapacity=False  # สำคัญ: สั่งให้ ASG สร้างเครื่องใหม่มาแทน
    )
    print("✅ Successfully detached from ASG.")
    return asg_name


def freeze_ebs(instance_id, instance_data, rule):
    """
    "แช่แข็ง" EBS ไว้เป็นหลักฐาน 2 ชั้น
      1. create_snapshot  = ก๊อปปี้ ณ วินาทีที่ถูกยึด เอาไป mount อ่านทีหลังได้
      2. DeleteOnTermination = false = ต่อให้เครื่องถูก terminate ทีหลัง ตัว volume ก็ยังอยู่

    ไม่สั่ง stop เครื่อง เพราะอยากให้ process / RAM ยังอยู่ให้สืบต่อ ตัดแค่เน็ตพอ
    """
    snapshot_ids = []
    block_devices = instance_data.get('BlockDeviceMappings', [])

    for device in block_devices:
        volume_id = device['Ebs']['VolumeId']
        device_name = device['DeviceName']
        print(f"📸 Starting Snapshot for Volume {volume_id}...")

        snap = ec2.create_snapshot(
            VolumeId=volume_id,
            Description=f"Forensic Snapshot for Compromised Instance {instance_id} ({rule})",
            TagSpecifications=[{
                'ResourceType': 'snapshot',
                'Tags': [
                    {'Key': 'Name', 'Value': f"forensic-{instance_id}-{device_name.replace('/', '_')}"},
                    {'Key': 'Case', 'Value': 'IncidentResponse'},
                    {'Key': 'SecurityStatus', 'Value': 'Compromised'},
                    {'Key': 'CompromisedInstanceId', 'Value': instance_id},
                    {'Key': 'FalcoRule', 'Value': rule[:255]}
                ]
            }]
        )
        snapshot_ids.append(snap['SnapshotId'])
        print(f"✅ Created Snapshot {snap['SnapshotId']} for {volume_id} successfully.")

        # กัน volume หายตามเครื่องถ้ามีคนสั่ง terminate ทีหลัง
        try:
            ec2.modify_instance_attribute(
                InstanceId=instance_id,
                BlockDeviceMappings=[{
                    'DeviceName': device_name,
                    'Ebs': {'DeleteOnTermination': False}
                }]
            )
            print(f"🔒 {device_name} จะไม่ถูกลบตามเครื่องแล้ว (DeleteOnTermination=false)")
        except Exception as err:
            print(f"⚠️ [WARNING] ตั้ง DeleteOnTermination ของ {device_name} ไม่สำเร็จ: {err}")

    return snapshot_ids


def record_incident(record):
    """เขียนบันทึกเหตุการณ์ลง S3 ให้หน้า dashboard อ่านไปแสดงเป็น timeline"""
    if not DATA_BUCKET:
        return
    key = f"{INCIDENT_PREFIX}{record['instance_id']}-{record['detected_at'].replace(':', '')}.json"
    try:
        s3.put_object(
            Bucket=DATA_BUCKET,
            Key=key,
            Body=json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8'),
            ContentType='application/json'
        )
        print(f"🧾 บันทึกเหตุการณ์ไว้ที่ s3://{DATA_BUCKET}/{key}")
    except Exception as err:
        print(f"⚠️ [WARNING] เขียนบันทึกเหตุการณ์ลง S3 ไม่สำเร็จ: {err}")


def lambda_handler(event, context):
    # 1. ถอดรหัสและขยายไฟล์ Log ที่ CloudWatch บีบอัดส่งมาให้
    cw_data = event['awslogs']['data']
    compressed_payload = base64.b64decode(cw_data)
    uncompressed_payload = gzip.decompress(compressed_payload)
    log_payload = json.loads(uncompressed_payload)

    # 2. วนลูปอ่านทุกเหตุการณ์ (Log Events) ที่ส่งมาในรอบนี้
    for log_event in log_payload.get('logEvents', []):
        try:
            # แปลงข้อความ Log (ของ Falco) ให้เป็น JSON Dictionary
            falco_alert = json.loads(log_event['message'])

            # ดึงข้อมูลสำคัญจาก Falco
            priority = falco_alert.get('priority', 'Unknown')
            rule = falco_alert.get('rule', 'Unknown')
            output = falco_alert.get('output', 'No details provided')
            hostname = falco_alert.get('hostname', '')

            # ==========================================
            # 3. ตั้งเงื่อนไข "การถูกยึดเครื่อง (Compromised)"
            # ==========================================
            critical_priorities = ["Critical", "Emergency", "Alert"]
            compromised_rules = [
                "Terminal shell in container",
                "Run shell untrusted",
                "Launch Privileged Container",
                "Write below etc"
            ]
            is_compromised = (priority in critical_priorities) or (rule in compromised_rules)

            if not is_compromised:
                print(f"ℹ️ [INFO] Alert skipped (Priority: {priority}, Rule: {rule}).")
                continue

            # สมมติว่าตั้งชื่อ Hostname เป็น Instance ID ไว้ (เช่น i-0abcd1234...)
            instance_id = hostname
            if not instance_id.startswith('i-'):
                print(f"⚠️ [WARNING] Hostname '{hostname}' is not a valid AWS Instance ID.")
                continue

            print("=" * 60)
            print("🚨 [THREAT DETECTED] EC2 Instance Compromised!")
            print(f"   📌 Instance ID : {instance_id}")
            print(f"   🔥 Severity    : {priority}")
            print(f"   📜 Rule Broken : {rule}")
            print(f"   📝 Details     : {output}")
            print("=" * 60)

            # ขอข้อมูล Instance แค่ครั้งเดียว เพื่อนำไปใช้ทั้งหา ASG และ EBS
            instance_info = ec2.describe_instances(InstanceIds=[instance_id])
            instance_data = instance_info['Reservations'][0]['Instances'][0]

            if already_quarantined(instance_data):
                print(f"⏭️ [SKIP] {instance_id} ถูกจับกักไปแล้วก่อนหน้านี้ ไม่ทำซ้ำ")
                continue

            detected_at = datetime.now(timezone.utc).isoformat()
            asg_name = None
            snapshot_ids = []

            # 4-5. ถอดออกจาก ASG แล้วแช่แข็ง EBS
            try:
                asg_name = detach_from_asg(instance_id, instance_data)
                snapshot_ids = freeze_ebs(instance_id, instance_data, rule)
            except Exception as inner_err:
                print(f"⚠️ [WARNING] Failed during ASG Detach or Snapshot: {inner_err}")

            # ==========================================
            # 6. สั่งตัดเน็ต (ย้ายเข้า Air-Gap SG)
            # ==========================================
            print(f"🛡️ Isolating instance to Air-Gap SG ({ISOLATED_SG_ID})...")
            ec2.modify_instance_attribute(
                InstanceId=instance_id,
                Groups=[ISOLATED_SG_ID]
            )
            print(f"✅ [SUCCESS] Instance {instance_id} has been successfully air-gapped.")

            # 7. ติดป้ายให้ dashboard รู้ว่าเครื่องนี้โดนยึด (aggregator อ่าน tag นี้)
            ec2.create_tags(
                Resources=[instance_id],
                Tags=[
                    {'Key': 'SecurityStatus', 'Value': 'Compromised'},
                    {'Key': 'QuarantinedAt', 'Value': detected_at},
                    {'Key': 'QuarantineRule', 'Value': rule[:255]}
                ]
            )
            print(f"Isolated and tagged instance {instance_id}")

            record_incident({
                'instance_id': instance_id,
                'detected_at': detected_at,
                'priority': priority,
                'rule': rule,
                'output': output,
                'detached_from_asg': asg_name,
                'snapshots': snapshot_ids,
                'isolated_sg': ISOLATED_SG_ID
            })

        except Exception as e:
            print(f"❌ [ERROR] Failed to process log event: {e}")

    return {
        "statusCode": 200,
        "body": "Log processing complete"
    }
