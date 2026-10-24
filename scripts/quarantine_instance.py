import json
import base64
import gzip
import boto3
import os

# Connect AWS APIs
ec2 = boto3.client('ec2')
asg = boto3.client('autoscaling')  # เพิ่ม Client สำหรับ Auto Scaling

# Take Security Group ID (quarantine_security)
ISOLATED_SG_ID = os.environ.get('ISOLATED_SG_ID')

def lambda_handler(event, context):
    # 1. ถอดรหัสและขยายไฟล์ Log ที่ CloudWatch บีบอัดส่งมาให้
    cw_data = event['awslogs']['data']
    compressed_payload = base64.b64decode(cw_data)
    uncompressed_payload = gzip.decompress(compressed_payload)
    log_payload = json.loads(uncompressed_payload)
    
    # 2. วนลูปอ่านทุกเหตุการณ์ (Log Events)
    for log_event in log_payload.get('logEvents', []):
        try:
            falco_alert = json.loads(log_event['message'])
            
            # ดึงข้อมูลสำคัญจาก Falco
            priority = falco_alert.get('priority', 'Unknown')
            rule     = falco_alert.get('rule', 'Unknown')
            output   = falco_alert.get('output', 'No details provided')
            hostname = falco_alert.get('hostname', '')
            
            # ==========================================
            # 3. เงื่อนไข "การถูกยึดเครื่อง (Compromised)"
            # ==========================================
            critical_priorities = ["Critical", "Emergency", "Alert"]
            compromised_rules = [
                "Terminal shell in container", 
                "Run shell untrusted",
                "Launch Privileged Container",
                "Write below etc"
            ]
            
            is_compromised = (priority in critical_priorities) or (rule in compromised_rules)
            
            if is_compromised:
                instance_id = hostname
                
                if instance_id.startswith('i-'):
                    print("="*60)
                    print(f"🚨 [THREAT DETECTED] EC2 Instance Compromised!")
                    print(f"   📌 Instance ID : {instance_id}")
                    print(f"   🔥 Severity    : {priority}")
                    print(f"   📜 Rule Broken : {rule}")
                    print("="*60)
                    
                    # ขอข้อมูล Instance แค่ครั้งเดียว เพื่อนำไปใช้ทั้งหา ASG และ EBS
                    try:
                        instance_info = ec2.describe_instances(InstanceIds=[instance_id])
                        instance_data = instance_info['Reservations'][0]['Instances'][0]
                        
                        # ==========================================
                        # [เพิ่มใหม่] 4. สั่ง Detach ออกจาก ASG
                        # ==========================================
                        tags = instance_data.get('Tags', [])
                        asg_name = next((tag['Value'] for tag in tags if tag['Key'] == 'aws:autoscaling:groupName'), None)
                        
                        if asg_name:
                            print(f"🔧 Detaching {instance_id} from ASG: {asg_name}...")
                            asg.detach_instances(
                                InstanceIds=[instance_id],
                                AutoScalingGroupName=asg_name,
                                ShouldDecrementDesiredCapacity=False # สำคัญ: สั่งให้ ASG สร้างเครื่องใหม่มาแทน
                            )
                            print("✅ Successfully detached from ASG.")
                            
                        # ==========================================
                        # [เพิ่มใหม่] 5. ทำ EBS Snapshot เก็บหลักฐาน
                        # ==========================================
                        block_devices = instance_data.get('BlockDeviceMappings', [])
                        for device in block_devices:
                            volume_id = device['Ebs']['VolumeId']
                            device_name = device['DeviceName']
                            print(f"📸 Starting Snapshot for Volume {volume_id}...")
                            
                            ec2.create_snapshot(
                                VolumeId=volume_id,
                                Description=f"Forensic Snapshot for Compromised Instance {instance_id}",
                                TagSpecifications=[{
                                    'ResourceType': 'snapshot',
                                    'Tags': [
                                        {'Key': 'Name', 'Value': f"forensic-{instance_id}-{device_name.replace('/', '_')}"},
                                        {'Key': 'Case', 'Value': 'IncidentResponse'},
                                        {'Key': 'CompromisedInstanceId', 'Value': instance_id}
                                    ]
                                }]
                            )
                            print(f"✅ Created Snapshot for {volume_id} successfully.")
                            
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
                else:
                    print(f"⚠️ [WARNING] Hostname '{hostname}' is not a valid AWS Instance ID.")
            else:
                print(f"ℹ️ [INFO] Alert skipped (Priority: {priority}, Rule: {rule}).")
                
        except Exception as e:
            print(f"❌ [ERROR] Failed to process log event: {e}")
            
    return {
        "statusCode": 200,
        "body": "Log processing complete"
    }