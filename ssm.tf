# -----
# ป้ายประกาศว่า "ตอนนี้ต้องรัน image tag ไหน"
#
# เครื่องที่ ASG ปั้นขึ้นมาจะอ่านค่านี้ตอนบูต (scripts/userdata.sh) แทนการฝัง tag
# ไว้ใน user data ของ launch template การ deploy เวอร์ชันใหม่จึงเหลือ 2 คำสั่ง:
#   aws ssm put-parameter --name <ชื่อนี้> --value <tag> --overwrite
#   aws autoscaling start-instance-refresh --auto-scaling-group-name <asg>
#
# ignore_changes ที่ value เพราะฝั่ง CD เป็นเจ้าของค่านี้หลังสร้างเสร็จแล้ว
# ถ้าไม่ใส่ terraform apply รอบถัดไปจะดึงกลับไปเป็น var.image_tag ของตอน apply
# -----
resource "aws_ssm_parameter" "app_image_tag" {
  name        = "/${var.project_name}/app/image_tag"
  description = "Image tag ของแอปที่เครื่องใน ASG ต้องรัน"
  type        = "String"
  value       = var.image_tag

  tags = local.common_tag

  lifecycle {
    ignore_changes = [value]
  }
}
