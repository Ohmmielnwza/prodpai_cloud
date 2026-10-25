# -----
# ค่าที่ workflow และคนใช้งานต้องใช้ต่อ
#
# หมายเหตุ: ข้อมูลที่หน้า dashboard แสดง "ไม่ได้" มาจาก output ชุดนี้แล้ว
# แต่มาจาก Lambda prod-pai-Infra-Aggregator ที่กวาดของจริงจาก AWS API แล้วเขียนลง S3
# output ที่นี่จึงเหลือเฉพาะ URL และชื่อ resource ที่ launch_image.yaml ต้องอ้างถึง
# -----

output "app_url" {
  description = "หน้าเว็บแอป todo-list ผ่าน ALB"
  value       = "http://${aws_lb.lb.dns_name}/"
}

output "dashboard_url" {
  description = "หน้า dashboard ผ่าน ALB (listener rule /dashboard*)"
  value       = "http://${aws_lb.lb.dns_name}/dashboard"
}

# ชื่อเดิม ใช้ใน workflow/สคริปต์ที่มีอยู่ จึงคงไว้
output "lb_domain_name_http" {
  value = "http://${aws_lb.lb.dns_name}/"
}

output "asg_name" {
  description = "ชื่อ ASG ที่ launch_image.yaml ต้องติด tag เพื่อจุด instance refresh"
  value       = aws_autoscaling_group.asg.name
}

output "launch_template_id" {
  description = "launch template ที่ launch_image.yaml สร้าง version ใหม่ให้"
  value       = aws_launch_template.prodpai_web_template.id
}

output "app_ecr_url" {
  description = "ECR ที่ CI ของ prod-pai-todolist ดัน image ขึ้นมา"
  value       = data.aws_ecr_repository.app_repo.repository_url
}

output "dashboard_data_location" {
  description = "ที่อยู่ของ snapshot ที่ aggregator เขียนและ dashboard อ่าน"
  value       = "s3://${var.data_bucket}/${var.data_key}"
}
