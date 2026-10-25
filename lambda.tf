# -----
# Defined Role
# -----
data "aws_iam_role" "lab_role" {
  name = "LabRole"
}

# Account ID
data "aws_caller_identity" "current" {}

# -----
# Archive a single file
# -----
data "archive_file" "quarantine_zip" {
  type        = "zip"
  source_file = "${path.module}/scripts/quarantine_instance.py"
  output_path = "${path.module}/scripts/quarantine_instance.zip"
}

# -----
# Archive a fetch metric to s3
# -----
data "archive_file" "aggregator_zip" {
  type        = "zip"
  source_file = "${path.module}/scripts/dashboard_aggregator.py"
  output_path = "${path.module}/scripts/dashboard_aggregator.zip"
}

# ------
# Function for instance that taken
# Change security group for create air gap this instance
# -----
resource "aws_lambda_function" "quarantine_function" {
  function_name = "quarantine_taken_instance"
  role          = data.aws_iam_role.lab_role.arn
  filename      = data.archive_file.quarantine_zip.output_path

  # python3.10 หมดระยะซัพพอร์ต upstream แล้ว (ต.ค. 2026)
  runtime = "python3.12"
  # <fileName>.<fuctionName>
  handler          = "quarantine_instance.lambda_handler"
  source_code_hash = data.archive_file.quarantine_zip.output_base64sha256

  # default 3 วินาทีไม่พอ: ขั้นตอนหนึ่งเหตุการณ์คือ describe + detach + snapshot ทุก volume
  timeout = 60

  environment {
    variables = {
      ISOLATED_SG_ID  = aws_security_group.falco_sg.id
      DATA_BUCKET     = var.data_bucket
      INCIDENT_PREFIX = "dashboard/incidents/"
    }
  }
}

# -----
# Log group for falcosidekick
# -----
resource "aws_cloudwatch_log_group" "falco_alert" {
  name = "/falco/alerts"
}

# -----
# Log steam sidekick
# -----
resource "aws_cloudwatch_log_stream" "event" {
  name           = "falco-events"
  log_group_name = "/falco/alerts"

  depends_on = [aws_cloudwatch_log_group.falco_alert]
}

# -----
# lambda Trigger
# -----
# Falco เรียงระดับ Emergency > Alert > Critical > Error > ... เดิมขาด Alert ซึ่งร้ายแรงกว่า Critical
# ให้ตรงกับ critical_priorities ใน quarantine_instance.py ที่ตรวจซ้ำอีกชั้น
resource "aws_cloudwatch_log_subscription_filter" "falco_trigger" {
  name            = "falco-events"
  log_group_name  = "/falco/alerts"
  filter_pattern  = "{ $.priority = \"Emergency\" || $.priority = \"Alert\" || $.priority = \"Critical\" }"
  destination_arn = aws_lambda_function.quarantine_function.arn

  depends_on = [aws_lambda_permission.allow_falco_cloudwatch, aws_cloudwatch_log_stream.event]
}

# -----
# Allow cloudwatch for wake lambda
# -----
resource "aws_lambda_permission" "allow_falco_cloudwatch" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.quarantine_function.function_name
  # "events for eventbridge, logs for cloudwatch"
  principal  = "logs.amazonaws.com"
  source_arn = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/falco/alerts:*"
}


# -----
#
# -----
resource "aws_lambda_function" "infra_aggregator" {
  function_name = "prod-pai-Infra-Aggregator"

  role = data.aws_iam_role.lab_role.arn

  filename         = data.archive_file.aggregator_zip.output_path
  runtime          = "python3.12"
  handler          = "dashboard_aggregator.lambda_handler"
  source_code_hash = data.archive_file.aggregator_zip.output_base64sha256

  # กวาดทั้ง region (EC2/VPC/subnet/SG/IGW/NAT/ALB+target health/ASG/WAF/EBS) + CloudWatch
  # + Cost Explorer ใช้เวลามากกว่าเดิม 30 วินาทีจึงไม่พอ
  timeout     = 120
  memory_size = 512

  environment {
    variables = {
      S3_BUCKET   = var.data_bucket
      S3_KEY      = var.data_key
      ASG_NAME    = aws_autoscaling_group.asg.name
      APP_TG_ARN  = aws_lb_target_group.lb_target.arn
      DASH_TG_ARN = aws_lb_target_group.dashboard_tg.arn
      # Cost Explorer คิด $0.01 ต่อ request และตัวเลขขยับแค่วันละไม่กี่ครั้ง
      # จึงเก็บผลไว้ใน S3 แล้วถามใหม่เมื่อครบเวลานี้ ไม่ว่า aggregator จะถูกยิงถี่แค่ไหน
      COST_CACHE_TTL_SECONDS = "21600"
    }
  }
}

# -----
# aggregator ไม่มี EventBridge ผูกไว้เลย ตั้งใจให้ dashboard แยกขาดจาก infra หลัก
# ข้อมูลบน S3 ขยับเฉพาะตอนมีคนกดปุ่ม Sync AWS บนหน้าเว็บ (dashboard_app.py
# เรียก lambda.invoke ตรง) Lambda quarantine ไม่เรียก aggregator
# -----

# -----
# endpoint for ec2 to s3
# -----
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
}
