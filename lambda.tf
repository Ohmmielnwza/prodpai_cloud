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

  environment {
    variables = {
      ISOLATED_SG_ID = aws_security_group.falco_sg.id
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
