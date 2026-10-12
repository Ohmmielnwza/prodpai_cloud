# -----
# Create Load balancer
# -----
resource "aws_lb" "lb" {
  name               = "${local.name_prefix}-lb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.lb_sg.id]
  subnets            = aws_subnet.public_zone[*].id

  enable_deletion_protection = false

  #   access_logs {
  #     bucket  = aws_s3_bucket.lb_logs.bucket
  #     prefix  = "test-lb"
  #     enabled = true
  #   }

  depends_on = [aws_internet_gateway.igw]

  tags = {
    Environment = "${local.name_prefix}-lb"
  }
}

# Target instance created by ASG
resource "aws_lb_target_group" "lb_target" {
  name     = "${local.name_prefix}-lb-target"
  port     = 80
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  health_check {
    path = "/health"
  }

}

# Defined Load balancer listen Target
resource "aws_lb_listener" "lb-listener" {
  load_balancer_arn = aws_lb.lb.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.lb_target.arn
  }
}
