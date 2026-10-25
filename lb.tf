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

# =======================================================
# 4. สร้าง Target Group สำหรับพอร์ต 5000 (ที่บอกว่าจำเป็นต้องมี)
# =======================================================
resource "aws_lb_target_group" "dashboard_tg" {
  name     = "dashboard-tg-5000"
  port     = 5000
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  # ALB ส่ง path มาตามที่ผู้ใช้พิมพ์ (rewrite ไม่ได้) แอป Flask จึง mount ทุก route ไว้ใต้
  # /dashboard health check จึงต้องยิง /dashboard/health ไม่ใช่ /api/data ที่ไม่มีอยู่จริง
  health_check {
    path                = "/dashboard/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

# =======================================================
# 5. ผูก EC2 Ubuntu เข้ากับ Target Group
# =======================================================
resource "aws_lb_target_group_attachment" "dashboard_attach" {
  target_group_arn = aws_lb_target_group.dashboard_tg.arn
  target_id        = aws_instance.dashboard.id
  port             = 5000
}

resource "aws_lb_listener_rule" "dashboard_rule" {
  listener_arn = aws_lb_listener.lb-listener.arn # เอาไปเกาะกับ Listener พอร์ต 80 ด้านบน
  priority     = 100                             # ลำดับความสำคัญ (เลขน้อย = ตรวจสอบก่อน)

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.dashboard_tg.arn # TG 2: ชี้ไปที่ Target Group ของ Dashboard
  }

  condition {
    path_pattern {
      values = ["/dashboard*"] # ถ้าผู้ใช้พิมพ์ /dashboard ให้เข้าเงื่อนไขนี้
    }
  }
}
