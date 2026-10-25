# output "ssh_command" {
#   value       = "ssh -i  ubuntu@${aws_instance.test_falco.public_ip}"
#   description = "Command for SSH into ubuntu"
# }

# output "public_ip" {
#   value = aws_instance.web.public_ip
# }

# output "web_url" {
#   value = "http://${aws_instance.web.public_dns}"
# }

# -----
# Live data for the dashboard
# อ่านจาก AWS ตอน apply (ไม่ใช่ค่าใน config) เพื่อให้สถานะตรงกับของจริง
# -----
locals {
  # แยก data source ตามสถานะ เพราะ aws_instances ไม่บอกสถานะรายเครื่อง
  instance_states = toset(["pending", "running", "shutting-down", "stopping", "stopped"])
}

# EC2 ที่ ASG สร้างจริง (ไม่ใช่เครื่อง image builder)
data "aws_instances" "asg" {
  for_each = local.instance_states

  instance_tags = {
    "aws:autoscaling:groupName" = aws_autoscaling_group.asg.name
  }
  instance_state_names = [each.key]

  depends_on = [aws_autoscaling_group.asg]
}

# ค่า capacity จริงของ ASG (resource ตั้ง ignore_changes ไว้ ค่าใน state จึงอาจไม่ตรง)
data "aws_autoscaling_group" "asg" {
  name = aws_autoscaling_group.asg.name

  depends_on = [aws_autoscaling_group.asg]
}

# เครื่อง builder ถูก terminate ด้วย AWS CLI ใน null_resource ซึ่ง Terraform ไม่รู้
# จึงถาม AWS ตรงๆ ว่ายังอยู่ในสถานะไหน ถ้าไม่เจอในสถานะใดเลยแปลว่า terminated แล้ว
data "aws_instances" "builder" {
  for_each = local.instance_states

  filter {
    name   = "instance-id"
    values = [aws_instance.prodpai_instance.id]
  }
  instance_state_names = [each.key]

  depends_on = [null_resource.terminate_builder]
}

output "lb_domain_name_http" {
  value = "http://${aws_lb.lb.dns_name}/"
}
