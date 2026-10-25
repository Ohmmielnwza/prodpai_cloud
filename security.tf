# -----
# security group for accesss web Instance
# -----
resource "aws_security_group" "allow_web" {
  name        = "allow-instance-sg"
  description = "Allow inbound traffic"
  vpc_id      = aws_vpc.main.id

  # ไม่มี SSH แล้ว: เครื่องใน ASG อยู่ private subnet และไม่มีใครต้อง ssh เข้า
  # SSH มีไว้แค่ตอน Terraform ลง Falco บนเครื่อง builder ซึ่งใช้ builder_sg ด้านล่าง
  ingress {
    description     = "HTTP from ALB"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.lb_sg.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = var.cidr_allow_all
  }

  tags = merge(
    local.common_tag,
    {
      Name = "${local.name_prefix}-allow-instance"
    }
  )

  depends_on = [aws_security_group.lb_sg]

}

# -----
# security group ของเครื่อง builder (ใช้ปั้น AMI แล้ว terminate ทิ้ง)
#
# แยกจาก allow_web เพราะ builder ต้องเปิด SSH ให้ remote-exec ลง Falco
# ถ้าใช้ SG ร่วมกัน เครื่องใน ASG จะได้ SSH ติดไปด้วย
# SSH รับเฉพาะ IP ที่ส่งมาทาง var.builder_ssh_cidrs (infra_setup.yaml ส่ง IP ของ runner)
# -----
resource "aws_security_group" "builder_sg" {
  name        = "image-builder-sg"
  description = "SSH from the Terraform runner only, for baking the AMI"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "ssh for falco-script from the Terraform runner"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.builder_ssh_cidrs
  }

  # builder ต้องออกไป apt / download.falco.org / github ได้
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = var.cidr_allow_all
  }

  tags = merge(
    local.common_tag,
    {
      Name = "${local.name_prefix}-image-builder"
    }
  )
}

# -----
# security group for ALB
# -----
resource "aws_security_group" "lb_sg" {
  name        = "allow_http-alb"
  description = "Allow http inbound traffic"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "http from VPC"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.cidr_allow_all
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = var.cidr_allow_all
  }

  tags = merge(local.common_tag,
    {
      Name = "${local.name_prefix}-allow_alb"
    }
  )
}

# -----
# qaurantine security group
# -----
resource "aws_security_group" "falco_sg" {
  name        = "ec2-compromise-isolatation-sg"
  description = "Deny any port any IP for isolation instance"
  vpc_id      = aws_vpc.main.id

  ingress = []
  egress  = []
}

# -----
# acess dashboard
# -----
resource "aws_security_group" "dashboard_ec2_sg" {
  name   = "dashboard-ec2-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 5000
    to_port         = 5000
    protocol        = "tcp"
    security_groups = [aws_security_group.lb_sg.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
