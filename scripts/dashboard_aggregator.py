"""
กวาดสถานะจริงของ AWS ทั้ง region แล้วเขียนเป็น JSON ก้อนเดียวลง S3 ให้ dashboard อ่าน

สิ่งที่ไฟล์นี้ออกแบบไว้ให้ต่างจากเวอร์ชันแรก
  1. ไม่เอาเครื่องที่ terminated แล้วมาแสดง
  2. จัดข้อมูลเป็นชั้น region -> vpc -> az -> public/private -> subnet -> node
     แทน list แบนๆ หน้าเว็บจะได้วาดกรอบซ้อนกันได้
  3. public/private ดูจาก route table ที่ subnet ผูกอยู่จริง ไม่ใช่เดาจากชื่อ
  4. เก็บ Security Group / IGW / NAT / WAF เพิ่ม พร้อมบอกว่าผูกกับอะไร
  5. tag SecurityStatus=Compromised บังคับให้ health เป็น error เสมอ
  6. "healthy" นับเฉพาะสมาชิกของ target group (เครื่องจาก ASG + dashboard) เท่านั้น
  7. ประเมินค่าใช้จ่าย ทั้งที่ใช้ไปแล้วเดือนนี้และที่จะเป็นในอีก 1 เดือน
"""

import json
import boto3
import os
from datetime import datetime, timedelta, timezone

# ==========================================
# ตั้งค่า AWS Clients
# ==========================================
region = os.environ.get('AWS_REGION', 'us-east-1')
ec2 = boto3.client('ec2', region_name=region)
cw = boto3.client('cloudwatch', region_name=region)
s3 = boto3.client('s3', region_name=region)
elbv2 = boto3.client('elbv2', region_name=region)
autoscaling = boto3.client('autoscaling', region_name=region)
wafv2 = boto3.client('wafv2', region_name=region)
ce = boto3.client('ce', region_name='us-east-1')  # Cost Explorer อยู่ us-east-1 เสมอ

S3_BUCKET = os.environ.get('S3_BUCKET')
S3_KEY = os.environ.get('S3_KEY', 'dashboard/current_infra.json')
INCIDENT_PREFIX = os.environ.get('INCIDENT_PREFIX', 'dashboard/incidents/')

# ARN ของ target group ที่ "นับ" เข้าสูตร healthy (ส่งมาจาก Terraform)
COUNTED_TG_ARNS = [a for a in [os.environ.get('APP_TG_ARN'), os.environ.get('DASH_TG_ARN')] if a]

# ==========================================
# ราคาโดยประมาณของ us-east-1 (USD) ใช้ประเมินตอน Cost Explorer เรียกไม่ได้
# ==========================================
HOURS_PER_MONTH = 730
EC2_HOURLY = {
    "t2.micro": 0.0116, "t2.small": 0.023, "t2.medium": 0.0464,
    "t3.nano": 0.0052, "t3.micro": 0.0104, "t3.small": 0.0208,
    "t3.medium": 0.0416, "t3.large": 0.0832,
    "m5.large": 0.096, "c5.large": 0.085,
}
EBS_GB_MONTHLY = 0.08        # gp3
SNAPSHOT_GB_MONTHLY = 0.05
ALB_HOURLY = 0.0225
ALB_LCU_MONTHLY = 5.8        # ประมาณการใช้งานระดับเดโม
NAT_HOURLY = 0.045
NAT_GB = 0.045
NAT_GB_MONTHLY_EST = 10      # เดา traffic ผ่าน NAT ต่อเดือน
EIP_HOURLY = 0.005           # EIP ที่ผูกกับ NAT คิดเงินแยก
WAF_ACL_MONTHLY = 5.0
WAF_RULE_MONTHLY = 1.0
S3_GB_MONTHLY = 0.023
LAMBDA_MONTHLY_EST = 0.20    # ยิงทุก 2 นาที ยังอยู่ในโควต้าฟรีแทบทั้งหมด


# ==========================================
# ตัวช่วยเล็กๆ
# ==========================================
def tag_map(obj, key='Tags'):
    return {t['Key']: t['Value'] for t in (obj.get(key) or [])}


def name_of(tags, fallback):
    return tags.get('Name') or fallback


def get_cpu_utilization(instance_id):
    """
    ฟังก์ชันสำหรับดึงค่า CPU เฉลี่ยย้อนหลัง 5 นาทีจาก CloudWatch
    """
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=10)
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
        datapoints = sorted(response.get('Datapoints', []), key=lambda d: d['Timestamp'])
        if datapoints:
            return round(datapoints[-1]['Average'], 2)
    except Exception as e:
        print(f"Failed to fetch CPU for {instance_id}: {e}")
    return 0.0


# ==========================================
# 1. โครงข่าย: VPC / subnet / route table / IGW / NAT
# ==========================================
def collect_network():
    """
    คืน dict ของข้อมูลเครือข่ายทั้ง region
    จุดสำคัญคือ subnet_zone_type: ดูจาก route table ที่ subnet นั้นผูกอยู่จริง
      - มี route ออก IGW   -> public
      - มี route ออก NAT   -> private
      - ไม่มีทางออกเลย     -> isolated
    ถ้าอ่าน route ไม่ได้ค่อยถอยไปดู tag Tier แล้วค่อยดูชื่อเป็นทางสุดท้าย
    """
    vpcs = ec2.describe_vpcs().get('Vpcs', [])
    subnets = ec2.describe_subnets().get('Subnets', [])
    route_tables = ec2.describe_route_tables().get('RouteTables', [])
    igws = ec2.describe_internet_gateways().get('InternetGateways', [])
    nats = ec2.describe_nat_gateways().get('NatGateways', [])

    # subnet_id -> route table ที่ผูกไว้ / vpc_id -> route table ที่เป็น main
    rt_by_subnet = {}
    main_rt_by_vpc = {}
    for rt in route_tables:
        for assoc in rt.get('Associations', []):
            if assoc.get('Main'):
                main_rt_by_vpc[rt['VpcId']] = rt
            elif assoc.get('SubnetId'):
                rt_by_subnet[assoc['SubnetId']] = rt

    def zone_type_of(subnet):
        rt = rt_by_subnet.get(subnet['SubnetId']) or main_rt_by_vpc.get(subnet['VpcId'])
        if rt:
            for route in rt.get('Routes', []):
                if route.get('GatewayId', '').startswith('igw-'):
                    return 'public', rt['RouteTableId']
                if route.get('NatGatewayId'):
                    return 'private', rt['RouteTableId']
            return 'isolated', rt['RouteTableId']

        tags = tag_map(subnet)
        tier = (tags.get('Tier') or tags.get('Name') or '').lower()
        if 'public' in tier:
            return 'public', None
        if 'private' in tier:
            return 'private', None
        return 'unknown', None

    enriched_subnets = []
    for sn in subnets:
        tags = tag_map(sn)
        zone_type, rt_id = zone_type_of(sn)
        enriched_subnets.append({
            'id': sn['SubnetId'],
            'name': name_of(tags, sn['SubnetId']),
            'vpc_id': sn['VpcId'],
            'az': sn['AvailabilityZone'],
            'cidr': sn['CidrBlock'],
            'state': sn['State'],
            'zone_type': zone_type,
            'route_table_id': rt_id,
            'available_ips': sn.get('AvailableIpAddressCount'),
            'tags': tags,
        })

    return {
        'vpcs': vpcs,
        'subnets': enriched_subnets,
        'igws': igws,
        'nats': nats,
        'route_tables': route_tables,
    }


# ==========================================
# 2. EC2 (ตัด terminated ทิ้ง) + EBS
# ==========================================
def collect_instances(asg_members):
    instances = []
    volumes_by_instance = {}

    for reservation in ec2.describe_instances().get('Reservations', []):
        for inst in reservation.get('Instances', []):
            state_name = inst['State']['Name']

            # ❌ เครื่องที่ตายไปแล้วไม่ต้องเอามาแสดงบน topology
            if state_name == 'terminated':
                continue

            instance_id = inst['InstanceId']
            tags = tag_map(inst)
            is_compromised = tags.get('SecurityStatus') == 'Compromised'

            if is_compromised:
                # 🚨 ไม่ว่าสถานะ AWS จะบอกว่าอะไร เครื่องที่โดนยึดคือ error เสมอ
                health = 'error'
                status = 'COMPROMISED (ISOLATED)'
            elif state_name == 'running':
                health = 'healthy'
                status = state_name
            elif state_name in ('stopped', 'stopping'):
                health = 'inactive'
                status = state_name
            else:
                health = 'warning'
                status = state_name

            cpu = get_cpu_utilization(instance_id) if state_name == 'running' else None

            vols = []
            for bd in inst.get('BlockDeviceMappings', []):
                if 'Ebs' in bd:
                    vols.append({'device': bd['DeviceName'], 'volume_id': bd['Ebs']['VolumeId']})
            volumes_by_instance[instance_id] = vols

            role = tags.get('Role')
            if not role:
                if 'aws:autoscaling:groupName' in tags or instance_id in asg_members:
                    role = 'asg-member'
                elif 'image-builder' in tags.get('Name', ''):
                    role = 'ami-builder'
                else:
                    role = 'instance'

            instances.append({
                'id': instance_id,
                'name': name_of(tags, instance_id),
                'type': 'aws_instance',
                'category': 'Compute',
                'status': status,
                'state': state_name,
                'health': health,
                'compromised': is_compromised,
                'role': role,
                'vpc_id': inst.get('VpcId'),
                'subnet_id': inst.get('SubnetId'),
                'az': inst.get('Placement', {}).get('AvailabilityZone', ''),
                'private_ip': inst.get('PrivateIpAddress', ''),
                'public_ip': inst.get('PublicIpAddress', ''),
                'instance_type': inst.get('InstanceType'),
                'cpu': cpu,
                'spec': f"{inst.get('InstanceType')}" + (f" | CPU: {cpu}%" if cpu is not None else ''),
                'security_groups': [g['GroupId'] for g in inst.get('SecurityGroups', [])],
                'volumes': vols,
                'launch_time': inst.get('LaunchTime').isoformat() if inst.get('LaunchTime') else None,
                'tags': tags,
            })

    return instances, volumes_by_instance


def collect_volumes():
    out = []
    try:
        for v in ec2.describe_volumes().get('Volumes', []):
            tags = tag_map(v)
            out.append({
                'id': v['VolumeId'],
                'name': name_of(tags, v['VolumeId']),
                'type': 'aws_ebs_volume',
                'category': 'Storage',
                'status': v['State'],
                'health': 'healthy' if v['State'] in ('in-use', 'available') else 'warning',
                'size_gb': v['Size'],
                'az': v['AvailabilityZone'],
                'attached_to': [a['InstanceId'] for a in v.get('Attachments', [])],
                'spec': f"{v['Size']} GB {v['VolumeType']}",
                'tags': tags,
            })
    except Exception as e:
        print(f"Error fetching volumes: {e}")
    return out


def collect_forensic_snapshots():
    """snapshot ที่ Lambda quarantine สร้างไว้เป็นหลักฐาน"""
    out = []
    try:
        snaps = ec2.describe_snapshots(
            OwnerIds=['self'],
            Filters=[{'Name': 'tag:Case', 'Values': ['IncidentResponse']}]
        ).get('Snapshots', [])
        for s in snaps:
            tags = tag_map(s)
            out.append({
                'id': s['SnapshotId'],
                'name': name_of(tags, s['SnapshotId']),
                'type': 'aws_ebs_snapshot',
                'category': 'Security',
                'status': s['State'],
                'health': 'healthy' if s['State'] == 'completed' else 'warning',
                'size_gb': s['VolumeSize'],
                'source_instance': tags.get('CompromisedInstanceId'),
                'started_at': s['StartTime'].isoformat() if s.get('StartTime') else None,
                'spec': f"{s['VolumeSize']} GB forensic snapshot",
                'tags': tags,
            })
    except Exception as e:
        print(f"Error fetching snapshots: {e}")
    return out


# ==========================================
# 3. Security Group
# ==========================================
def collect_security_groups(instances):
    out = []
    used_by = {}
    for inst in instances:
        for sg_id in inst['security_groups']:
            used_by.setdefault(sg_id, []).append(inst['id'])

    try:
        for sg in ec2.describe_security_groups().get('SecurityGroups', []):
            tags = tag_map(sg)
            ingress = sg.get('IpPermissions', [])
            egress = sg.get('IpPermissionsEgress', [])
            open_to_world = any(
                r.get('CidrIp') == '0.0.0.0/0'
                for perm in ingress for r in perm.get('IpRanges', [])
            )
            out.append({
                'id': sg['GroupId'],
                'name': name_of(tags, sg['GroupName']),
                'group_name': sg['GroupName'],
                'type': 'aws_security_group',
                'category': 'Security',
                'status': 'available',
                # SG ที่เปิด 0.0.0.0/0 ขึ้นเหลืองไว้ให้สังเกตเห็น ไม่ใช่ error เพราะ ALB ต้องเปิดจริง
                'health': 'warning' if open_to_world else 'healthy',
                'vpc_id': sg.get('VpcId'),
                'open_to_world': open_to_world,
                'ingress_rules': len(ingress),
                'egress_rules': len(egress),
                'attached_to': used_by.get(sg['GroupId'], []),
                'spec': f"{len(ingress)} in / {len(egress)} out",
                'description': sg.get('Description', ''),
                'tags': tags,
            })
    except Exception as e:
        print(f"Error fetching security groups: {e}")
    return out


# ==========================================
# 4. ALB + target group + สุขภาพของ target
# ==========================================
def collect_load_balancing():
    lbs, target_groups, target_health = [], [], {}

    try:
        for lb in elbv2.describe_load_balancers().get('LoadBalancers', []):
            lbs.append({
                'id': lb['LoadBalancerArn'],
                'arn': lb['LoadBalancerArn'],
                'name': lb['LoadBalancerName'],
                'type': 'aws_lb',
                'category': 'Network',
                'status': lb['State']['Code'],
                'health': 'healthy' if lb['State']['Code'] == 'active' else 'warning',
                'endpoint': lb['DNSName'],
                'scheme': lb.get('Scheme'),
                'vpc_id': lb.get('VpcId'),
                'az': None,
                'subnet_ids': [z['SubnetId'] for z in lb.get('AvailabilityZones', [])],
                'security_groups': lb.get('SecurityGroups', []),
                'spec': lb['Type'],
                'tags': {},
            })
    except Exception as e:
        print(f"Error fetching ELBs: {e}")

    try:
        for tg in elbv2.describe_target_groups().get('TargetGroups', []):
            arn = tg['TargetGroupArn']
            members = []
            try:
                for th in elbv2.describe_target_health(TargetGroupArn=arn).get('TargetHealthDescriptions', []):
                    members.append({
                        'target_id': th['Target']['Id'],
                        'port': th['Target'].get('Port'),
                        'state': th['TargetHealth']['State'],
                        'reason': th['TargetHealth'].get('Reason'),
                        'description': th['TargetHealth'].get('Description'),
                    })
            except Exception as e:
                print(f"Error fetching target health for {arn}: {e}")
            target_health[arn] = members

            healthy = sum(1 for m in members if m['state'] == 'healthy')
            target_groups.append({
                'id': arn,
                'arn': arn,
                'name': tg['TargetGroupName'],
                'type': 'aws_lb_target_group',
                'category': 'Network',
                'status': f"{healthy}/{len(members)} healthy",
                'health': 'healthy' if members and healthy == len(members) else ('error' if members else 'warning'),
                'vpc_id': tg.get('VpcId'),
                'port': tg.get('Port'),
                'protocol': tg.get('Protocol'),
                'health_check_path': tg.get('HealthCheckPath'),
                'counted_for_health': arn in COUNTED_TG_ARNS,
                'members': members,
                'spec': f"port {tg.get('Port')} | {tg.get('HealthCheckPath')}",
                'tags': {},
            })
    except Exception as e:
        print(f"Error fetching target groups: {e}")

    return lbs, target_groups, target_health


# ==========================================
# 5. ASG / WAF
# ==========================================
def collect_asg():
    groups, members = [], set()
    try:
        for g in autoscaling.describe_auto_scaling_groups().get('AutoScalingGroups', []):
            ids = [i['InstanceId'] for i in g.get('Instances', [])]
            members.update(ids)
            in_service = sum(1 for i in g.get('Instances', []) if i['LifecycleState'] == 'InService')
            groups.append({
                'id': g['AutoScalingGroupName'],
                'name': g['AutoScalingGroupName'],
                'type': 'aws_autoscaling_group',
                'category': 'Compute',
                'status': f"{in_service}/{g['DesiredCapacity']} in service",
                'health': 'healthy' if in_service >= g['MinSize'] else 'error',
                'vpc_id': None,
                'min_size': g['MinSize'],
                'max_size': g['MaxSize'],
                'desired_capacity': g['DesiredCapacity'],
                'instance_ids': ids,
                'subnet_ids': (g.get('VPCZoneIdentifier') or '').split(','),
                'spec': f"min {g['MinSize']} / desired {g['DesiredCapacity']} / max {g['MaxSize']}",
                'tags': {t['Key']: t['Value'] for t in g.get('Tags', [])},
            })
    except Exception as e:
        print(f"Error fetching ASG: {e}")
    return groups, members


def collect_waf(lbs):
    out = []
    try:
        acls = wafv2.list_web_acls(Scope='REGIONAL').get('WebACLs', [])
        for acl in acls:
            detail, rule_count, associated = {}, 0, []
            try:
                detail = wafv2.get_web_acl(
                    Name=acl['Name'], Scope='REGIONAL', Id=acl['Id']
                ).get('WebACL', {})
                rule_count = len(detail.get('Rules', []))
            except Exception as e:
                print(f"Error reading WebACL {acl['Name']}: {e}")
            try:
                associated = wafv2.list_resources_for_web_acl(
                    WebACLArn=acl['ARN'], ResourceType='APPLICATION_LOAD_BALANCER'
                ).get('ResourceArns', [])
            except Exception as e:
                print(f"Error listing WAF associations: {e}")

            # ผูก WAF ไว้กับ VPC ของ ALB ที่มันป้องกันอยู่ หน้าเว็บจะได้วางไว้ที่ขอบ VPC นั้น
            vpc_id = next((lb['vpc_id'] for lb in lbs if lb['arn'] in associated), None)
            out.append({
                'id': acl['ARN'],
                'arn': acl['ARN'],
                'name': acl['Name'],
                'type': 'aws_wafv2_web_acl',
                'category': 'Security',
                'status': 'active' if associated else 'not associated',
                'health': 'healthy' if associated else 'warning',
                'vpc_id': vpc_id,
                'rule_count': rule_count,
                'protects': associated,
                'spec': f"{rule_count} rules",
                'tags': {},
            })
    except Exception as e:
        print(f"Error fetching WAF: {e}")
    return out


def collect_incidents():
    """อ่านบันทึกเหตุการณ์ที่ Lambda quarantine เขียนไว้ เอามาทำ timeline"""
    out = []
    if not S3_BUCKET:
        return out
    try:
        objs = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=INCIDENT_PREFIX).get('Contents', [])
        for obj in sorted(objs, key=lambda o: o['LastModified'], reverse=True)[:20]:
            body = s3.get_object(Bucket=S3_BUCKET, Key=obj['Key'])['Body'].read()
            out.append(json.loads(body))
    except Exception as e:
        print(f"Error reading incidents: {e}")
    return out


# ==========================================
# 6. ค่าใช้จ่าย
# ==========================================
def estimate_monthly_cost(instances, volumes, snapshots, lbs, nats, wafs):
    """
    ประเมินค่าใช้จ่ายต่อเดือนจากของที่มีอยู่จริงตอนนี้ (run-rate)
    ใช้เมื่อ Cost Explorer เรียกไม่ได้ หรือใช้คู่กันเพื่อเทียบ
    """
    breakdown = {}

    ec2_cost = 0.0
    for inst in instances:
        if inst['state'] != 'running':
            continue
        ec2_cost += EC2_HOURLY.get(inst.get('instance_type') or '', 0.0) * HOURS_PER_MONTH
    breakdown['EC2 instances'] = round(ec2_cost, 2)

    breakdown['EBS volumes'] = round(sum(v['size_gb'] for v in volumes) * EBS_GB_MONTHLY, 2)
    breakdown['Forensic snapshots'] = round(sum(s['size_gb'] for s in snapshots) * SNAPSHOT_GB_MONTHLY, 2)
    breakdown['Load balancer'] = round(len(lbs) * (ALB_HOURLY * HOURS_PER_MONTH + ALB_LCU_MONTHLY), 2)
    breakdown['NAT gateway'] = round(
        len(nats) * (NAT_HOURLY * HOURS_PER_MONTH + NAT_GB * NAT_GB_MONTHLY_EST + EIP_HOURLY * HOURS_PER_MONTH), 2)
    breakdown['WAF'] = round(sum(WAF_ACL_MONTHLY + WAF_RULE_MONTHLY * w['rule_count'] for w in wafs), 2)
    breakdown['Lambda + CloudWatch'] = LAMBDA_MONTHLY_EST
    breakdown['S3'] = round(S3_GB_MONTHLY * 1, 2)

    total = round(sum(breakdown.values()), 2)
    return {'breakdown': breakdown, 'total': total}


def cost_explorer_actuals():
    """ของจริงจาก Cost Explorer ถ้าบัญชีเปิดสิทธิ์ให้ (บัญชี Academy มักปิดไว้)"""
    today = datetime.now(timezone.utc).date()
    start = today.replace(day=1)
    result = {'available': False}

    try:
        resp = ce.get_cost_and_usage(
            TimePeriod={'Start': str(start), 'End': str(today + timedelta(days=1))},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
        )
        by_service = {}
        for period in resp.get('ResultsByTime', []):
            for group in period.get('Groups', []):
                amount = float(group['Metrics']['UnblendedCost']['Amount'])
                if amount > 0:
                    by_service[group['Keys'][0]] = round(
                        by_service.get(group['Keys'][0], 0) + amount, 2)
        result.update({
            'available': True,
            'period_start': str(start),
            'period_end': str(today),
            'month_to_date': round(sum(by_service.values()), 2),
            'by_service': dict(sorted(by_service.items(), key=lambda kv: -kv[1])),
        })
    except Exception as e:
        print(f"Cost Explorer (actuals) unavailable: {e}")
        return result

    try:
        forecast = ce.get_cost_forecast(
            TimePeriod={'Start': str(today + timedelta(days=1)),
                        'End': str(today + timedelta(days=31))},
            Metric='UNBLENDED_COST',
            Granularity='MONTHLY',
        )
        result['forecast_30d'] = round(float(forecast['Total']['Amount']), 2)
    except Exception as e:
        print(f"Cost Explorer (forecast) unavailable: {e}")

    return result


def build_cost(instances, volumes, snapshots, lbs, nats, wafs):
    estimate = estimate_monthly_cost(instances, volumes, snapshots, lbs, nats, wafs)
    actual = cost_explorer_actuals()

    today = datetime.now(timezone.utc).date()
    days_in_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    days_left = days_in_month.day - today.day

    # ถ้ามีของจริง: ใช้ยอด MTD + run-rate ที่เหลือของเดือน ถ้าไม่มี: ใช้ค่าประเมินล้วน
    if actual.get('available'):
        daily_rate = estimate['total'] / 30.0
        end_of_month = round(actual['month_to_date'] + daily_rate * days_left, 2)
        source = 'cost_explorer'
    else:
        daily_rate = estimate['total'] / 30.0
        end_of_month = round(daily_rate * days_in_month.day, 2)
        source = 'estimated'

    return {
        'currency': 'USD',
        'source': source,
        'note': ('ตัวเลขที่ใช้ไปแล้วมาจาก Cost Explorer ส่วนที่คาดการณ์คำนวณจาก resource ที่มีอยู่จริง'
                 if source == 'cost_explorer'
                 else 'Cost Explorer เรียกไม่ได้ ตัวเลขทั้งหมดประเมินจาก resource ที่มีอยู่จริง x ราคา on-demand us-east-1'),
        'month_to_date': actual.get('month_to_date'),
        'by_service_actual': actual.get('by_service'),
        'run_rate_monthly': estimate['total'],
        'breakdown_monthly': estimate['breakdown'],
        'projected_end_of_month': end_of_month,
        'projected_next_30_days': actual.get('forecast_30d', estimate['total']),
        'days_left_in_month': days_left,
    }


# ==========================================
# 7. ประกอบเป็นชั้น region -> vpc -> az -> public/private -> subnet
# ==========================================
def build_topology(net, instances, sgs, lbs, tgs, asgs, wafs):
    subnets = net['subnets']
    nodes_by_subnet = {}

    for inst in instances:
        nodes_by_subnet.setdefault(inst['subnet_id'], []).append(inst)

    # NAT อยู่ใน subnet จริงๆ จึงวางเป็น node ใน subnet นั้นเลย
    for nat in net['nats']:
        if nat['State'] == 'deleted':
            continue
        tags = tag_map(nat)
        addr = (nat.get('NatGatewayAddresses') or [{}])[0]
        nodes_by_subnet.setdefault(nat.get('SubnetId'), []).append({
            'id': nat['NatGatewayId'],
            'name': name_of(tags, nat['NatGatewayId']),
            'type': 'aws_nat_gateway',
            'category': 'Network',
            'status': nat['State'],
            'health': 'healthy' if nat['State'] == 'available' else 'warning',
            'role': 'infra',
            'vpc_id': nat['VpcId'],
            'subnet_id': nat.get('SubnetId'),
            'public_ip': addr.get('PublicIp', ''),
            'private_ip': addr.get('PrivateIp', ''),
            'spec': f"NAT {addr.get('PublicIp', '')}",
            'tags': tags,
        })

    vpc_nodes = []
    for vpc in net['vpcs']:
        vpc_id = vpc['VpcId']
        tags = tag_map(vpc)
        vpc_subnets = [s for s in subnets if s['vpc_id'] == vpc_id]

        igw = next((g for g in net['igws']
                    if any(a['VpcId'] == vpc_id for a in g.get('Attachments', []))), None)

        azs = {}
        for sn in sorted(vpc_subnets, key=lambda s: (s['az'], s['cidr'])):
            band = azs.setdefault(sn['az'], {'id': sn['az'], 'bands': {'public': [], 'private': [], 'isolated': [], 'unknown': []}})
            band['bands'][sn['zone_type']].append({
                'subnet': sn,
                'nodes': nodes_by_subnet.get(sn['id'], []),
            })

        vpc_nodes.append({
            'id': vpc_id,
            'name': name_of(tags, vpc_id),
            'cidr': vpc.get('CidrBlock'),
            'state': vpc.get('State'),
            'is_default': vpc.get('IsDefault', False),
            'tags': tags,
            'edge': {
                'internet_gateway': ({
                    'id': igw['InternetGatewayId'],
                    'name': name_of(tag_map(igw), igw['InternetGatewayId']),
                    'type': 'aws_internet_gateway',
                    'category': 'Network',
                    'status': 'attached',
                    'health': 'healthy',
                    'role': 'infra',
                    'spec': 'ทางออกอินเทอร์เน็ตของ VPC',
                } if igw else None),
                'load_balancers': [lb for lb in lbs if lb['vpc_id'] == vpc_id],
                'target_groups': [tg for tg in tgs if tg['vpc_id'] == vpc_id],
                'waf': [w for w in wafs if w['vpc_id'] == vpc_id],
            },
            'security_groups': [sg for sg in sgs if sg['vpc_id'] == vpc_id],
            'auto_scaling_groups': [
                g for g in asgs
                if any(sid in [s['id'] for s in vpc_subnets] for sid in g['subnet_ids'])
            ],
            'azs': [azs[k] for k in sorted(azs)],
        })

    # VPC ของโปรเจกต์ขึ้นก่อน default VPC ไปอยู่ท้ายสุด (หน้าเว็บพับเก็บไว้)
    vpc_nodes.sort(key=lambda v: (v['is_default'], v['name']))

    return [{
        'id': region,
        'name': region,
        'vpcs': vpc_nodes,
    }]


def build_health_summary(tgs, instances):
    """
    นับ healthy เฉพาะสมาชิกของ target group ที่ Terraform ชี้มาเท่านั้น
    (= เครื่องที่ ASG สร้าง + เครื่อง dashboard) ของ infra อย่าง NAT/IGW/SG ไม่นับ
    """
    counted = [tg for tg in tgs if tg['counted_for_health']] or tgs
    healthy = unhealthy = total = 0
    members = []

    for tg in counted:
        for m in tg['members']:
            total += 1
            members.append({'target_id': m['target_id'], 'tg': tg['name'], 'state': m['state']})
            if m['state'] == 'healthy':
                healthy += 1
            else:
                unhealthy += 1

    compromised = [i['id'] for i in instances if i['compromised']]

    return {
        'healthy': healthy,
        'unhealthy': unhealthy,
        'total_targets': total,
        'counted_from': [tg['name'] for tg in counted],
        'members': members,
        'compromised': compromised,
        'compromised_count': len(compromised),
    }


# ==========================================
# Handler
# ==========================================
def lambda_handler(event, context):
    # ถูกเรียกได้จากหลายทาง: schedule ทุก 2 นาที, EventBridge ตอน ASG สลับเครื่องหรือ
    # instance refresh ขยับ, Lambda quarantine ตอนกักเครื่อง, และปุ่ม Sync บนหน้าเว็บ
    # log ไว้ว่ารอบนี้มาจากไหน เวลาไล่ปัญหาจะรู้ว่า event ทำงานจริงหรือหล่น
    trigger = (event or {}).get('detail-type') or (event or {}).get('source') or 'manual/schedule'
    print(f"triggered by: {trigger}")

    net = collect_network()
    asgs, asg_members = collect_asg()
    instances, _ = collect_instances(asg_members)
    volumes = collect_volumes()
    snapshots = collect_forensic_snapshots()
    sgs = collect_security_groups(instances)
    lbs, tgs, _ = collect_load_balancing()
    wafs = collect_waf(lbs)
    incidents = collect_incidents()

    live_nats = [n for n in net['nats'] if n['State'] != 'deleted']

    # list แบนๆ สำหรับตารางรายการ resource (หน้าเว็บส่วนเดิมยังใช้ก้อนนี้อยู่)
    resources = []
    resources.extend(instances)
    for vpc in net['vpcs']:
        tags = tag_map(vpc)
        resources.append({
            'id': vpc['VpcId'], 'name': name_of(tags, vpc['VpcId']), 'type': 'aws_vpc',
            'category': 'Network', 'status': vpc.get('State', 'available'),
            'health': 'healthy' if vpc.get('State') == 'available' else 'warning',
            'role': 'infra', 'is_default': vpc.get('IsDefault', False),
            'spec': vpc.get('CidrBlock', ''), 'tags': tags,
        })
    for sn in net['subnets']:
        resources.append({
            'id': sn['id'], 'name': sn['name'], 'type': 'aws_subnet', 'category': 'Network',
            'status': sn['state'], 'health': 'healthy' if sn['state'] == 'available' else 'warning',
            'role': 'infra', 'az': sn['az'], 'zone_type': sn['zone_type'],
            'vpc_id': sn['vpc_id'], 'spec': f"{sn['cidr']} ({sn['zone_type']})", 'tags': sn['tags'],
        })
    for g in net['igws']:
        tags = tag_map(g)
        attached = [a['VpcId'] for a in g.get('Attachments', [])]
        resources.append({
            'id': g['InternetGatewayId'], 'name': name_of(tags, g['InternetGatewayId']),
            'type': 'aws_internet_gateway', 'category': 'Network',
            'status': 'attached' if attached else 'detached',
            'health': 'healthy' if attached else 'warning', 'role': 'infra',
            'vpc_id': attached[0] if attached else None,
            'spec': ', '.join(attached) or 'not attached', 'tags': tags,
        })
    for n in live_nats:
        tags = tag_map(n)
        addr = (n.get('NatGatewayAddresses') or [{}])[0]
        resources.append({
            'id': n['NatGatewayId'], 'name': name_of(tags, n['NatGatewayId']),
            'type': 'aws_nat_gateway', 'category': 'Network', 'status': n['State'],
            'health': 'healthy' if n['State'] == 'available' else 'warning', 'role': 'infra',
            'vpc_id': n['VpcId'], 'az': None, 'spec': addr.get('PublicIp', ''), 'tags': tags,
        })
    resources.extend(sgs)
    resources.extend(lbs)
    resources.extend(tgs)
    resources.extend(asgs)
    resources.extend(wafs)
    resources.extend(volumes)
    resources.extend(snapshots)

    payload = {
        'project': 'Prod-Pai Cloud',
        'environment': 'Production',
        'region': region,
        'update_time': datetime.now(timezone.utc).isoformat(),
        'source': 'live',
        'topology': {'regions': build_topology(net, instances, sgs, lbs, tgs, asgs, wafs)},
        'health': build_health_summary(tgs, instances),
        'incidents': incidents,
        'cost': build_cost(instances, volumes, snapshots, lbs, live_nats, wafs),
        'resources': resources,
    }

    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=S3_KEY,
            Body=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            ContentType='application/json'
        )
        print(f"Successfully uploaded to s3://{S3_BUCKET}/{S3_KEY} "
              f"({len(resources)} resources, {payload['health']['healthy']} healthy targets)")
    except Exception as e:
        print(f"Error uploading to S3: {e}")
        raise e

    return {
        'statusCode': 200,
        'body': json.dumps('Dashboard Data Synced Successfully')
    }
