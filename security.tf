# -----
# security group for accesss web Instance
# -----
resource "aws_security_group" "allow_web" {
  name        = "allow-instance-sg"
  description = "Allow inbound traffic"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "ssh from anywhere"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.cidr_allow_all
  }

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
  name        = "sg-ec2-compromise-isolatation"
  description = "Deny any port any IP for isolation instance"
  vpc_id      = aws_vpc.main.id

  ingress = []
  egress  = []
}
