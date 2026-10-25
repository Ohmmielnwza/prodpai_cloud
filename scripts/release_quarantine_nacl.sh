#!/bin/bash
# ปลด NACL deny ที่ Lambda quarantine ใส่ไว้ให้เครื่องที่ถูกกัก
#
# ใช้ก่อน terminate เครื่องที่ถูกกักด้วยมือ ถ้าไม่ปลด rule จะค้างอยู่ใน NACL
# แล้วเครื่องใหม่ที่ได้ IP เดิมจะถูกบล็อกไปด้วย (destroy ทั้ง VPC ไม่ต้องใช้ เพราะ NACL ถูกลบตาม)
#
#   scripts/release_quarantine_nacl.sh i-0123456789abcdef0          # ดูว่าจะลบอะไร
#   scripts/release_quarantine_nacl.sh i-0123456789abcdef0 --apply  # ลบจริง
#
# ลบเฉพาะ rule ที่ตรงทั้ง 3 อย่าง: เลขตาม tag QuarantineNaclRules, action deny และ CIDR เป็น
# private IP ตัวใดตัวหนึ่งของเครื่องนี้ (/32) ถ้าเลขนั้นถูกใช้กับอย่างอื่นไปแล้วจะไม่แตะ
# ต้องการสิทธิ์: ec2:DescribeInstances, ec2:DescribeNetworkAcls, ec2:DeleteNetworkAclEntry, ec2:CreateTags
set -euo pipefail

instance_id="${1:?usage: $0 <instance-id> [--apply]}"
apply="${2:-}"
region="${AWS_REGION:-us-east-1}"

vpc_id=$(aws ec2 describe-instances --region "$region" --instance-ids "$instance_id" \
  --query 'Reservations[0].Instances[0].VpcId' --output text)
# ทุก private IP ของทุก ENI ตรงกับที่ Lambda กักไว้ (instance_private_ips)
ips=$(aws ec2 describe-instances --region "$region" --instance-ids "$instance_id" \
  --query 'Reservations[0].Instances[0].NetworkInterfaces[].PrivateIpAddresses[].PrivateIpAddress' \
  --output text)
rules=$(aws ec2 describe-instances --region "$region" --instance-ids "$instance_id" \
  --query "Reservations[0].Instances[0].Tags[?Key=='QuarantineNaclRules'].Value | [0]" --output text)

if [ -z "$ips" ] || [ "$ips" = "None" ]; then
  echo "$instance_id has no private IP" >&2
  exit 1
fi
cidrs=" "
for ip in $ips; do cidrs="$cidrs$ip/32 "; done
echo "instance $instance_id  ips${cidrs} vpc $vpc_id"
echo "tag QuarantineNaclRules: $rules"

if [ "$rules" != "None" ] && [ -n "$rules" ]; then
  for item in ${rules//,/ }; do
    acl_id="${item%%:*}"
    number="${item##*:}"
    for egress in false true; do
      dir=$([ "$egress" = true ] && echo egress || echo ingress)
      cidr=$(aws ec2 describe-network-acls --region "$region" --network-acl-ids "$acl_id" \
        --query "NetworkAcls[0].Entries[?RuleNumber==\`$number\` && Egress==\`$egress\` && RuleAction=='deny'].CidrBlock | [0]" \
        --output text)
      if [[ "$cidrs" != *" $cidr "* ]]; then
        echo "  skip $acl_id rule $number $dir (not a deny for this instance: $cidr)"
        continue
      fi
      if [ "$apply" = "--apply" ]; then
        if [ "$egress" = true ]; then flag=--egress; else flag=--ingress; fi
        aws ec2 delete-network-acl-entry --region "$region" --network-acl-id "$acl_id" \
          --rule-number "$number" $flag
        echo "  deleted $acl_id rule $number $dir ($cidr)"
      else
        echo "  would delete $acl_id rule $number $dir ($cidr)"
      fi
    done
  done
fi

# ตรวจทั้ง VPC ว่ายังมี deny ของ IP เครื่องนี้เหลือไหม (รวมที่ไม่ได้อยู่ใน tag เช่นใส่ด้วยมือ)
left=0
for cidr in $cidrs; do
  n=$(aws ec2 describe-network-acls --region "$region" --filters "Name=vpc-id,Values=$vpc_id" \
    --query "NetworkAcls[].Entries[?RuleAction=='deny' && CidrBlock=='$cidr'][] | length(@)" --output text)
  left=$((left + n))
done

if [ "$apply" != "--apply" ]; then
  echo "dry run: $left deny rule(s) for this instance in $vpc_id. Re-run with --apply to delete."
  exit 0
fi
if [ "$left" != "0" ]; then
  echo "::error::$left deny rule(s) for this instance are still in $vpc_id, check them by hand" >&2
  exit 1
fi
aws ec2 create-tags --region "$region" --resources "$instance_id" \
  --tags Key=QuarantineStatus,Value=released
echo "released: no deny rule for this instance is left in $vpc_id. Safe to terminate $instance_id."
