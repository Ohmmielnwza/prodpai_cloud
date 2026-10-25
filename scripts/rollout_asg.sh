#!/bin/bash
# ทยอยเปลี่ยนเครื่องใน ASG ให้ไปรัน image tag ใหม่ใน SSM โดยไม่ใช้ instance refresh
#
# สำเนาเดียวกับ .github/scripts/rollout.sh ใน repo plodphai-todo-list แก้ที่หนึ่งต้องแก้อีกที่ด้วย
#
# ทำไมไม่ใช้ start-instance-refresh: บัญชี AWS Academy มี SCP ที่ deny
# autoscaling:StartInstanceRefresh ไว้ จึงทำ rolling เองด้วยคำสั่งที่ยังใช้ได้
# (SetDesiredCapacity + TerminateInstanceInAutoScalingGroup)
#
# ขั้นตอน (เปิดเครื่องใหม่ก่อนปิดเครื่องเก่าเสมอ จำนวนเครื่อง healthy จึงไม่ลดลง):
#   1. ถ้ายังไม่เต็ม max ให้เพิ่ม desired +1 แล้วรอเครื่องใหม่ healthy ใน target group
#   2. ปิดเครื่องเก่าทีละตัว ASG ปั้นเครื่องใหม่มาแทน (อ่าน tag ใหม่จาก SSM ตอนบูต) รอ healthy ก่อนไปตัวถัดไป
#   3. ตัวเก่าตัวสุดท้ายปิดแบบลด desired กลับเท่าเดิม
# ถ้าเครื่องใหม่ไม่ healthy ภายในเวลา จะหยุดทันทีโดยไม่ปิดเครื่องเก่าตัวถัดไป เว็บจึงยังอยู่บนเวอร์ชันเดิม
#
#   scripts/rollout_asg.sh <asg-name>
# env: ROLLOUT_WAIT_TRIES (ค่าเริ่ม 45) x ROLLOUT_WAIT_SECONDS (ค่าเริ่ม 20) = เวลารอต่อเครื่อง
set -euo pipefail

asg="${1:?usage: $0 <asg-name>}"
tries="${ROLLOUT_WAIT_TRIES:-45}"
pause="${ROLLOUT_WAIT_SECONDS:-20}"

asg_field() {
  aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$asg" \
    --query "AutoScalingGroups[0].$1" --output text
}

desired=$(asg_field DesiredCapacity)
max=$(asg_field MaxSize)
tg=$(asg_field 'TargetGroupARNs[0]')
old=$(asg_field 'Instances[].InstanceId' | tr '\t' ' ')
count=$(echo "$old" | wc -w)

if [ "$tg" = "None" ] || [ -z "$tg" ]; then
  echo "::error::ASG $asg ไม่ได้ผูก target group ไว้ ตรวจ healthy ไม่ได้"
  exit 1
fi
if [ "$count" -eq 0 ]; then
  echo "ASG ไม่มีเครื่องอยู่ เครื่องที่ ASG ปั้นต่อจากนี้จะอ่าน tag ใหม่จาก SSM เอง"
  exit 0
fi
echo "ASG $asg: desired=$desired max=$max  old instances: $old"

# จำนวนเครื่องที่ healthy ใน target group และไม่ใช่เครื่องชุดเก่า
healthy_new() {
  aws elbv2 describe-target-health --target-group-arn "$tg" \
    --query "TargetHealthDescriptions[?TargetHealth.State=='healthy'].Target.Id" --output text \
    | tr '\t' '\n' | grep -v -x -F -e "${old// /$'\n'}" -e '' | grep -c . || true
}

wait_for_new() {
  local want=$1
  for _ in $(seq 1 "$tries"); do
    local n
    n=$(healthy_new)
    echo "  new healthy: $n/$want"
    if [ "$n" -ge "$want" ]; then
      return 0
    fi
    sleep "$pause"
  done
  echo "::error::เครื่องใหม่ healthy ไม่ครบ $want ภายในเวลา หยุดไว้ก่อน เครื่องเก่าที่เหลือยังรับงานอยู่"
  echo "::error::ดู System log ของเครื่องใหม่ใน EC2 console (มักเป็น image pull ไม่ขึ้น)"
  if [ "$stage" = surge ]; then
    # ยังไม่ได้ปิดเครื่องเก่าสักตัว: ถอดเครื่องที่เปิดเพิ่มออกแล้วคืน desired เดิม
    # ไม่งั้น ASG จะวนปั้นเครื่องที่พังใหม่ไปเรื่อยๆ (เลือกปิดเองตาม ID ไม่ปล่อยให้ ASG เลือก
    # เพราะ termination policy อาจไปปิดเครื่องเก่าที่ยังดีอยู่แทน)
    for id in $(asg_field 'Instances[].InstanceId'); do
      case " $old " in *" $id "*) continue ;; esac
      echo "  ถอดเครื่องที่เปิดเพิ่ม $id และคืน desired"
      aws autoscaling terminate-instance-in-auto-scaling-group --instance-id "$id" \
        --should-decrement-desired-capacity >/dev/null
    done
  fi
  exit 1
}

want=0
surged=no
stage=replace
if [ "$desired" -lt "$max" ]; then
  echo "1) desired $desired -> $((desired + 1)) เปิดเครื่องใหม่ก่อน"
  aws autoscaling set-desired-capacity --auto-scaling-group-name "$asg" \
    --desired-capacity $((desired + 1))
  surged=yes
  stage=surge
  want=1
  wait_for_new "$want"
  stage=replace
else
  echo "1) desired เต็ม max แล้ว ($max) ข้ามการเปิดเพิ่ม จะเปลี่ยนทีละเครื่องแทน (ระหว่างนั้นเหลือ $((count - 1)) เครื่อง)"
fi

i=0
for id in $old; do
  i=$((i + 1))
  if [ "$surged" = yes ] && [ "$i" -eq "$count" ]; then
    # ตัวสุดท้าย: ปิดแล้วลด desired กลับเท่าเดิม ไม่ต้องปั้นใหม่มาแทน
    echo "2) ปิด $id (ตัวสุดท้าย) แล้วลด desired กลับเป็น $desired"
    aws autoscaling terminate-instance-in-auto-scaling-group --instance-id "$id" \
      --should-decrement-desired-capacity >/dev/null
  else
    echo "2) ปิด $id ให้ ASG ปั้นเครื่องใหม่มาแทน"
    aws autoscaling terminate-instance-in-auto-scaling-group --instance-id "$id" \
      --no-should-decrement-desired-capacity >/dev/null
    want=$((want + 1))
    wait_for_new "$want"
  fi
done

echo "เสร็จ: เครื่องชุดเก่า $count ตัวถูกแทนด้วยเครื่องที่อ่าน tag ใหม่จาก SSM แล้ว"
