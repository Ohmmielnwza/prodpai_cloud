# -----
# bucket for collect logs/metric
# -----
resource "aws_s3_bucket" "dashboard_data" {
  bucket = "prod-pai-logs-${data.aws_caller_identity.current.account_id}"
}
