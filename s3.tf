# -----
# ถังเก็บข้อมูลของ dashboard
#
# ตั้งใจ "สร้างเอง" นอก Terraform แล้วอ้างถึงด้วย data source เพราะ bucket นี้ต้องอยู่รอด
# จาก terraform destroy (เก็บ snapshot ของ topology + หลักฐาน incident ย้อนหลัง)
# ถ้ายังไม่มี ให้สร้างก่อน:  aws s3 mb s3://prod-pai-logs --region us-east-1
# -----
data "aws_s3_bucket" "dashboard_data" {
  bucket = var.data_bucket
}
