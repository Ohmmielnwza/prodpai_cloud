# -----
# ถังเก็บข้อมูลของ dashboard
#
# ตั้งใจ "สร้างเอง" นอก Terraform แล้วอ้างถึงด้วย data source เพราะ bucket นี้ต้องอยู่รอด
# จาก terraform destroy (เก็บ snapshot ของ topology + หลักฐาน incident ย้อนหลัง)
# ชื่อตั้งที่ DATA_BUCKET ใน .github/config.env (infra_setup สร้างให้ถ้ายังไม่มี)
# -----
data "aws_s3_bucket" "dashboard_data" {
  bucket = var.data_bucket
}
