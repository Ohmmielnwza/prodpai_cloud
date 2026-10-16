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

output "infrastructure_summary" {
  description = "Detailed summary of all provisioned resources for the Web Dashboard"
  value = {
    project     = var.project_name
    environment = var.environment
    region      = var.aws_region

    vpc = {
      id         = aws_vpc.main.id
      name       = try(aws_vpc.main.tags["Name"], "prodpai-prod-vpc")
      cidr_block = aws_vpc.main.cidr_block
    }

    subnets = concat(
      [for s in aws_subnet.public_zone : {
        id                = s.id
        name              = try(s.tags["Name"], s.id)
        cidr_block        = s.cidr_block
        availability_zone = s.availability_zone
        tags              = merge(s.tags, { Tier = "public" })
      }],
      [for s in aws_subnet.private_zone : {
        id                = s.id
        name              = try(s.tags["Name"], s.id)
        cidr_block        = s.cidr_block
        availability_zone = s.availability_zone
        tags              = merge(s.tags, { Tier = "private" })
      }]
    )

    internet_gateway = {
      id   = aws_internet_gateway.igw.id
      name = try(aws_internet_gateway.igw.tags["Name"], aws_internet_gateway.igw.id)
    }

    nat_gateways = [{
      id         = aws_nat_gateway.nat.id
      name       = try(aws_nat_gateway.nat.tags["Name"], aws_nat_gateway.nat.id)
      public_ip  = aws_nat_gateway.nat.public_ip
      private_ip = aws_nat_gateway.nat.private_ip
      subnet_id  = aws_nat_gateway.nat.subnet_id
    }]

    # aws_lb ไม่มี attribute สถานะ จึงไม่ใส่ state ปลอม ให้ dashboard แสดงว่าไม่ทราบสถานะจริง
    load_balancer = {
      id                 = aws_lb.lb.id
      name               = aws_lb.lb.name
      dns_name           = aws_lb.lb.dns_name
      load_balancer_type = aws_lb.lb.load_balancer_type
      scheme             = aws_lb.lb.internal ? "internal" : "internet-facing"
    }

    waf = {
      id         = aws_wafv2_web_acl.web_rate_limit.id
      name       = aws_wafv2_web_acl.web_rate_limit.name
      rule_count = length(aws_wafv2_web_acl.web_rate_limit.rule)
    }

    auto_scaling = {
      name             = aws_autoscaling_group.asg.name
      desired_capacity = data.aws_autoscaling_group.asg.desired_capacity
      min_size         = data.aws_autoscaling_group.asg.min_size
      max_size         = data.aws_autoscaling_group.asg.max_size
      instance_count   = sum([for d in data.aws_instances.asg : length(d.ids)])
    }

    target_group = {
      name                 = aws_lb_target_group.lb_target.name
      port                 = aws_lb_target_group.lb_target.port
      health_check_path    = aws_lb_target_group.lb_target.health_check[0].path
      health_check_matcher = aws_lb_target_group.lb_target.health_check[0].matcher
    }

    launch_template = {
      id   = aws_launch_template.prodpai_web_template.id
      name = aws_launch_template.prodpai_web_template.name
    }

    ec2_instances = flatten([
      for state, d in data.aws_instances.asg : [
        for i, id in d.ids : {
          id            = id
          name          = "${local.name_prefix}-instance"
          role          = "asg-member"
          status        = state
          instance_type = var.instance_type
          private_ip    = try(d.private_ips[i], null)
        }
      ]
    ])

    image_builder = {
      ami_id        = aws_ami_from_instance.web_ami.id
      instance_id   = aws_instance.prodpai_instance.id
      instance_type = try(aws_instance.prodpai_instance.instance_type, "unknown")
      role          = "ami-builder"
      status        = try([for state, d in data.aws_instances.builder : state if length(d.ids) > 0][0], "terminated")
      tags          = try(aws_instance.prodpai_instance.tags, { Role = "ami-builder" })
    }

    lambda_functions = [{
      id          = aws_lambda_function.quarantine_function.arn
      name        = aws_lambda_function.quarantine_function.function_name
      runtime     = aws_lambda_function.quarantine_function.runtime
      memory_size = aws_lambda_function.quarantine_function.memory_size
      trigger     = aws_cloudwatch_log_group.falco_alert.name
    }]

    log_groups = [{
      id   = aws_cloudwatch_log_group.falco_alert.arn
      name = aws_cloudwatch_log_group.falco_alert.name
    }]

    security_groups = {
      alb        = aws_security_group.lb_sg.id
      instance   = aws_security_group.allow_web.id
      quarantine = aws_security_group.falco_sg.id
    }

    # เวลาที่ terraform apply สร้าง output นี้ (UTC) ส่วนเวลาที่ dashboard ได้รับข้อมูลจะเก็บแยกเป็น received_at
    update_time = timestamp()
  }
}
