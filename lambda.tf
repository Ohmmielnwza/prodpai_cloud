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

  runtime = "python3.10"
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
      # การติด tag ไม่ทำให้เกิด event ใดๆ จึงต้องสะกิด aggregator เอง
      # ไม่งั้นกรอบแดงบนหน้า dashboard จะขึ้นช้าถึง 2 นาทีหลังเครื่องโดนยึด
      AGGREGATOR_NAME = aws_lambda_function.infra_aggregator.function_name
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
resource "aws_cloudwatch_log_subscription_filter" "falco_trigger" {
  name            = "falco-events"
  log_group_name  = "/falco/alerts"
  filter_pattern  = "{ $.priority = \"Critical\" || $.priority = \"Emergency\" }"
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
  runtime          = "python3.10"
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
    }
  }
}

# -----
# ยิง aggregator ทุก 2 นาที ไม่ต้องรอให้คนกด refresh หน้าเว็บ
# -----
resource "aws_cloudwatch_event_rule" "aggregator_schedule" {
  name                = "${local.name_prefix}-aggregator-schedule"
  description         = "Refresh the dashboard infrastructure snapshot in S3"
  schedule_expression = "rate(2 minutes)"

  tags = local.common_tag
}

resource "aws_cloudwatch_event_target" "aggregator_target" {
  rule      = aws_cloudwatch_event_rule.aggregator_schedule.name
  target_id = "infra-aggregator"
  arn       = aws_lambda_function.infra_aggregator.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.infra_aggregator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.aggregator_schedule.arn
}

# -----
# ยิง aggregator ทันทีตอนของเปลี่ยนจริง ไม่ต้องรอครบรอบ 2 นาที
#
# ตอน instance refresh เครื่องถูกสลับทีละตัว ถ้ารอ schedule อย่างเดียว หน้า dashboard
# จะตามไม่ทันและโชว์เครื่องที่หายไปแล้วค้างอยู่นานถึง 2 นาที กฎนี้จับทั้งจังหวะที่ refresh
# เริ่ม/ถึง checkpoint/จบ และจังหวะที่ ASG ปั้นหรือปลดเครื่องแต่ละตัว
# ส่วน schedule ด้านบนเหลือไว้เป็นตัวกันพลาด เผื่อ event หล่น
# -----
resource "aws_cloudwatch_event_rule" "aggregator_asg_events" {
  name        = "${local.name_prefix}-aggregator-asg-events"
  description = "Refresh the dashboard snapshot when the ASG replaces instances"

  event_pattern = jsonencode({
    source = ["aws.autoscaling"]
    detail-type = [
      "EC2 Instance Launch Successful",
      "EC2 Instance Terminate Successful",
      "EC2 Instance Launch Unsuccessful",
      "EC2 Instance Terminate Unsuccessful",
      "EC2 Auto Scaling Instance Refresh Started",
      "EC2 Auto Scaling Instance Refresh Succeeded",
      "EC2 Auto Scaling Instance Refresh Failed",
      "EC2 Auto Scaling Instance Refresh Cancelled",
      "EC2 Auto Scaling Instance Refresh Checkpoint Reached",
    ]
  })

  tags = local.common_tag
}

resource "aws_cloudwatch_event_target" "aggregator_asg_events" {
  rule      = aws_cloudwatch_event_rule.aggregator_asg_events.name
  target_id = "infra-aggregator-asg"
  arn       = aws_lambda_function.infra_aggregator.arn
}

resource "aws_lambda_permission" "allow_eventbridge_asg" {
  statement_id  = "AllowExecutionFromEventBridgeASG"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.infra_aggregator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.aggregator_asg_events.arn
}

# เครื่องที่ไม่ได้อยู่ใต้ ASG (เช่น dashboard หรือ builder) ไม่มี event จาก autoscaling
# จึงดักสถานะของ EC2 ตรงๆ อีกชั้น เพื่อให้เครื่องที่ terminated หายจากแผนผังทันที
resource "aws_cloudwatch_event_rule" "aggregator_instance_state" {
  name        = "${local.name_prefix}-aggregator-instance-state"
  description = "Refresh the dashboard snapshot when any EC2 instance changes state"

  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Instance State-change Notification"]
    detail = {
      state = ["running", "shutting-down", "stopped", "terminated"]
    }
  })

  tags = local.common_tag
}

resource "aws_cloudwatch_event_target" "aggregator_instance_state" {
  rule      = aws_cloudwatch_event_rule.aggregator_instance_state.name
  target_id = "infra-aggregator-instance-state"
  arn       = aws_lambda_function.infra_aggregator.arn
}

resource "aws_lambda_permission" "allow_eventbridge_instance_state" {
  statement_id  = "AllowExecutionFromEventBridgeInstanceState"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.infra_aggregator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.aggregator_instance_state.arn
}

# -----
# endpoint for ec2 to s3
# -----
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
}
