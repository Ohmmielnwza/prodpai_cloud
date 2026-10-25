import json
import base64
import gzip
import ipaddress
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

# เลข rule ของ NACL ที่ใช้กักเครื่อง ต้องน้อยกว่า 100 (rule allow-all ของ default NACL)
# NACL ตรวจ rule จากเลขน้อยไปมาก deny ที่เลขน้อยกว่าจึงชนะ
NACL_RULE_MIN = int(os.environ.get('NACL_RULE_MIN', '1'))
NACL_RULE_MAX = int(os.environ.get('NACL_RULE_MAX', '99'))


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


def freeze_ebs(instance_id, instance_data, rule, failures):
    """
    "แช่แข็ง" EBS ไว้เป็นหลักฐาน 2 ชั้น
      1. create_snapshot  = ก๊อปปี้ ณ วินาทีที่ถูกยึด เอาไป mount อ่านทีหลังได้
      2. DeleteOnTermination = false = ต่อให้เครื่องถูก terminate ทีหลัง ตัว volume ก็ยังอยู่

    ไม่สั่ง stop เครื่อง เพราะอยากให้ process / RAM ยังอยู่ให้สืบต่อ ตัดแค่เน็ตพอ
    volume ไหนพังจะจดลง failures แล้วทำ volume ถัดไปต่อ ไม่ทิ้ง snapshot ที่ได้แล้ว
    """
    snapshot_ids = []
    block_devices = instance_data.get('BlockDeviceMappings', [])

    for device in block_devices:
        volume_id = device['Ebs']['VolumeId']
        device_name = device['DeviceName']
        print(f"📸 Starting Snapshot for Volume {volume_id}...")

        try:
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
        except Exception as err:
            failures.append(f"snapshot {volume_id}: {err}")
            print(f"❌ [ERROR] Snapshot ของ {volume_id} ไม่สำเร็จ: {err}")

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
            failures.append(f"DeleteOnTermination {device_name}: {err}")
            print(f"⚠️ [WARNING] ตั้ง DeleteOnTermination ของ {device_name} ไม่สำเร็จ: {err}")

    return snapshot_ids


def nacl_verdict(entries, ip, egress):
    """
    rule แรกที่ NACL จะใช้กับ IP นี้ในทิศนั้นคืออะไร (NACL ใช้ rule เลขน้อยสุดที่ CIDR ครอบ IP)
    คืน (rule_number, action) หรือ (None, None) ถ้าไม่มี rule ไหนครอบเลย
    """
    addr = ipaddress.ip_address(ip)
    matches = [e for e in entries
               if e.get('Egress') == egress and e.get('CidrBlock')
               and addr in ipaddress.ip_network(e['CidrBlock'])]
    if not matches:
        return None, None
    first = min(matches, key=lambda e: e['RuleNumber'])
    return first['RuleNumber'], first['RuleAction']


def instance_private_ips(instance_data):
    """
    private IPv4 ทุกตัวของทุก ENI ที่เกาะเครื่องนี้ ไม่ใช่แค่ IP หลัก
    ถ้ากันแค่ IP หลัก เครื่องที่มี ENI หรือ secondary IP เพิ่มยังคุยออกไปทางนั้นได้
    """
    ips = []
    for eni in instance_data.get('NetworkInterfaces', []):
        for addr in eni.get('PrivateIpAddresses', []):
            ip = addr.get('PrivateIpAddress')
            if ip and ip not in ips:
                ips.append(ip)
    primary = instance_data.get('PrivateIpAddress')
    if primary and primary not in ips:
        ips.insert(0, primary)
    return ips


def block_in_nacls(instance_id, instance_data):
    """
    ตัด connection ที่เปิดค้างอยู่ด้วย NACL deny ของทุก private IP ของเครื่องนี้ (/32)
    ทั้งขาเข้าและขาออก

    เหตุผล: security group เป็น stateful การย้ายเข้า falco_sg กันได้แค่ connection ใหม่
    connection ที่ถูก track ไว้แล้ว (เช่น reverse shell ที่ผู้บุกรุกเปิดไว้) ยังวิ่งต่อได้
    AWS docs (Security group connection tracking) แนะนำให้ใช้ network ACL ซึ่ง stateless
    ถ้าต้องการให้ traffic หยุดทันที

    ใส่ทุก NACL ใน VPC ไม่ใช่แค่ของ subnet ตัวเอง เพราะ NACL ตรวจตอนข้ามขอบ subnet:
      - ingress deny source=IP  : กันเครื่องนี้ส่งของเข้า subnet อื่น (รวม NAT -> internet)
      - egress  deny dest=IP    : กัน subnet อื่น (รวมขากลับจาก NAT/ALB) ส่งของมาหาเครื่องนี้
    ข้อจำกัด: traffic ระหว่างเครื่องใน subnet เดียวกันไม่ผ่าน NACL ส่วนนั้นเหลือ falco_sg กันอยู่

    rule ค้างอยู่จนกว่าจะลบเอง ถ้า terminate เครื่องนี้แล้ว IP อาจถูกเครื่องใหม่ใช้ต่อ
    ต้องปลด rule ด้วย scripts/release_quarantine_nacl.sh ก่อน terminate
    (destroy ทั้ง VPC จะลบ NACL ไปเอง)

    คืน dict: rules = ["acl-id:number", ...], ips = IP ที่กัก, verified = True เมื่อทุก NACL
    ใน VPC ตัดสินทุก IP เป็น deny ทั้งสองทิศจริงหลังสร้าง rule, failures = รายการที่ไม่ผ่าน
    """
    result = {'rules': [], 'ips': [], 'verified': False, 'failures': []}
    ips = instance_private_ips(instance_data)
    vpc_id = instance_data.get('VpcId')
    if not ips or not vpc_id:
        result['failures'].append('NACL: instance has no private IP / VPC')
        return result
    result['ips'] = ips
    vpc_filter = [{'Name': 'vpc-id', 'Values': [vpc_id]}]

    for ip in ips:
        cidr = f"{ip}/32"
        # อ่านใหม่ทุก IP เพราะ IP ก่อนหน้าเพิ่งใช้เลข rule ไป
        for acl in ec2.describe_network_acls(Filters=vpc_filter)['NetworkAcls']:
            acl_id = acl['NetworkAclId']
            entries = acl.get('Entries', [])
            # ทิศไหนมี deny ของ IP นี้ "ตัวที่ชนะ" อยู่แล้ว ไม่สร้างซ้ำ
            missing = [eg for eg in (False, True) if nacl_verdict(entries, ip, eg)[1] != 'deny']
            if not missing:
                print(f"⏭️ {acl_id} บล็อก {cidr} อยู่แล้ว")
                continue

            # ใช้เลขเดียวกันทั้งขาเข้าและขาออก เวลาไล่ลบทีหลังจะได้ง่าย
            used = {e['RuleNumber'] for e in entries}
            for number in range(NACL_RULE_MIN, NACL_RULE_MAX + 1):
                if number in used:
                    continue
                try:
                    for egress in missing:
                        ec2.create_network_acl_entry(
                            NetworkAclId=acl_id, RuleNumber=number, Protocol='-1',
                            RuleAction='deny', Egress=egress, CidrBlock=cidr,
                        )
                except Exception as err:
                    code = getattr(err, 'response', {}).get('Error', {}).get('Code')
                    # Lambda อีกตัวที่กักเครื่องอื่นพร้อมกันอาจหยิบเลขเดียวกันไปก่อน ลองเลขถัดไป
                    if code == 'NetworkAclEntryAlreadyExists':
                        used.add(number)
                        continue
                    result['failures'].append(f"NACL {acl_id} {cidr}: {code or err}")
                    print(f"❌ [ERROR] {acl_id} สร้าง deny ของ {cidr} ไม่สำเร็จ: {err}")
                    break
                result['rules'].append(f"{acl_id}:{number}")
                print(f"🚫 {acl_id} rule {number}: deny all {cidr} ({len(missing)} ทิศ)")
                break
            else:
                result['failures'].append(
                    f"NACL {acl_id} {cidr}: no free rule number {NACL_RULE_MIN}-{NACL_RULE_MAX}")
                print(f"❌ [ERROR] {acl_id} ไม่มีเลข rule ว่างใน {NACL_RULE_MIN}-{NACL_RULE_MAX}")

    # อ่านกลับจาก AWS อีกรอบ ไม่เชื่อแค่ว่า API ไม่ error
    # ทุก IP ของเครื่อง x ทุก NACL ใน VPC x ทั้งสองทิศ ต้องตัดสินเป็น deny ถึงนับว่ากักครบ
    for acl in ec2.describe_network_acls(Filters=vpc_filter)['NetworkAcls']:
        for ip in ips:
            for egress in (False, True):
                number, action = nacl_verdict(acl.get('Entries', []), ip, egress)
                if action != 'deny':
                    direction = 'egress' if egress else 'ingress'
                    result['failures'].append(
                        f"NACL {acl['NetworkAclId']} {ip}/32 {direction}: "
                        f"first match is {action} (rule {number})")
    result['verified'] = not result['failures']
    return result


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
    # ทางเข้าที่ตั้งไว้มีทางเดียวคือ subscription filter (lambda.tf) ถ้ามีคนเรียกตรง
    # ด้วยรูปแบบอื่น ให้ปฏิเสธชัดๆ แทนที่จะพังกลางทาง
    if not isinstance(event, dict) or 'data' not in (event.get('awslogs') or {}):
        print("⚠️ [WARNING] Event is not a CloudWatch Logs subscription payload, ignored.")
        return {"statusCode": 400, "body": "Expected a CloudWatch Logs subscription event"}
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
            # subscription filter ใน lambda.tf กรองระดับ Emergency/Alert/Critical มาให้ก่อนแล้ว
            # แต่ยังตรวจซ้ำตรงนี้เป็นชั้นป้องกัน เผื่อ filter ถูกแก้หรือมีทางเรียกอื่นเพิ่มเข้ามา
            # ส่วนรายชื่อ rule ด้านล่างจะมีผลก็ต่อเมื่อขยาย filter ให้ส่งระดับที่ต่ำกว่านี้มาด้วย
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
            # ขั้นเก็บหลักฐาน (ไม่เกี่ยวกับการตัดเน็ต จึงไม่นับรวมใน containment)
            # แยก try กัน: detach พังแล้วต้องยัง snapshot ต่อ
            evidence_failures = []

            # 4. ถอดออกจาก ASG ไม่งั้น health check ที่ fail หลังตัดเน็ตจะทำให้ ASG ลบหลักฐานทิ้ง
            try:
                asg_name = detach_from_asg(instance_id, instance_data)
            except Exception as detach_err:
                evidence_failures.append(f"detach from ASG: {detach_err}")
                print(f"❌ [ERROR] Detach from ASG failed, the ASG may terminate this evidence: {detach_err}")

            # 5. แช่แข็ง EBS
            snapshot_ids = freeze_ebs(instance_id, instance_data, rule, evidence_failures)

            # ==========================================
            # 6. สั่งตัดเน็ต (ย้ายเข้า Air-Gap SG)
            # ==========================================
            # ถ้าขั้นไหนพัง ยังไปต่อจนติด tag และบันทึกเหตุการณ์ครบ (หลักฐานสำคัญกว่า)
            # แต่สถานะต้องบอกตามจริงว่ากักไม่ครบ ไม่ใช่รายงานว่าสำเร็จ
            failures = []
            print(f"🛡️ Isolating instance to Air-Gap SG ({ISOLATED_SG_ID})...")
            try:
                ec2.modify_instance_attribute(
                    InstanceId=instance_id,
                    Groups=[ISOLATED_SG_ID]
                )
                sg_isolated = True
                print(f"✅ Instance {instance_id} moved into the air-gap SG.")
            except Exception as sg_err:
                sg_isolated = False
                failures.append(f"security group: {sg_err}")
                print(f"❌ [ERROR] Failed to move {instance_id} into the air-gap SG: {sg_err}")

            # 6.1 SG กันได้แค่ connection ใหม่ ตัดของที่เปิดค้างอยู่ด้วย NACL (stateless)
            try:
                nacl = block_in_nacls(instance_id, instance_data)
            except Exception as nacl_err:
                nacl = {'rules': [], 'ips': [], 'verified': False, 'failures': [f"NACL: {nacl_err}"]}
            failures.extend(nacl['failures'])

            # contained = ทั้ง SG และ NACL ผ่านการตรวจครบ ถ้าไม่ครบคือ partial ต้องมีคนตามต่อ
            containment = 'contained' if sg_isolated and nacl['verified'] else 'partial'
            if containment == 'contained':
                print(f"✅ [SUCCESS] {instance_id} contained: air-gap SG + NACL deny verified.")
            else:
                print(f"🚨 [ERROR] {instance_id} is only PARTIALLY contained: {failures}")

            # 7. ติดป้ายให้ dashboard รู้ว่าเครื่องนี้โดนยึด (aggregator อ่าน tag นี้)
            tags = [
                {'Key': 'SecurityStatus', 'Value': 'Compromised'},
                {'Key': 'QuarantinedAt', 'Value': detected_at},
                {'Key': 'QuarantineRule', 'Value': rule[:255]},
                {'Key': 'QuarantineStatus', 'Value': containment}
            ]
            if nacl['rules']:
                # บอกว่าต้องไปลบ rule ไหนก่อน terminate เครื่องนี้ (กัน IP ซ้ำกับเครื่องใหม่)
                tags.append({'Key': 'QuarantineNaclRules', 'Value': ','.join(nacl['rules'])[:255]})
            ec2.create_tags(Resources=[instance_id], Tags=tags)
            print(f"Tagged instance {instance_id} (QuarantineStatus={containment})")

            record_incident({
                'instance_id': instance_id,
                'detected_at': detected_at,
                'priority': priority,
                'rule': rule,
                'output': output,
                'detached_from_asg': asg_name,
                'snapshots': snapshot_ids,
                'isolated_sg': ISOLATED_SG_ID,
                'containment': {
                    'status': containment,
                    'security_group': sg_isolated,
                    'nacl_verified': nacl['verified'],
                    'nacl_ips': nacl.get('ips', []),
                    'nacl_rules': nacl['rules'],
                    'failures': failures,
                    # contained หมายถึงตัดได้เฉพาะ traffic ที่ข้ามขอบ subnet (NACL) + SG ใหม่
                    # traffic ระหว่างเครื่องใน subnet เดียวกันไม่ผ่าน NACL เหลือแค่ falco_sg กัน
                    'same_subnet_traffic': 'security group only',
                },
                'evidence': {
                    'complete': not evidence_failures,
                    'failures': evidence_failures,
                },
            })

        except Exception as e:
            print(f"❌ [ERROR] Failed to process log event: {e}")

    return {
        "statusCode": 200,
        "body": "Log processing complete"
    }
