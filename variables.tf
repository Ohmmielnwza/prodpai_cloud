variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region to deploy into"
}

variable "project_name" {
  type        = string
  default     = "prod-pai"
  description = "Name for prefix of resource tags"
}

variable "environment" {
  type        = string
  default     = "env"
  description = "Deployment environment"
}

variable "cidr_allow_all" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "Allow IP anywhere"
}

variable "instance_type" {
  type        = string
  default     = "t3.small"
  description = "EC2 instance size"

  validation {
    condition     = can(regex("^t[23]\\.", var.instance_type))
    error_message = "Only t2/3 family are permitted on prodpai cloud project"
  }
}

variable "instance_count" {
  type        = number
  default     = 2
  description = "How many Instance to create"
}

variable "extra_tag" {
  type        = map(string)
  default     = {}
  description = "For caller input new tag"
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Docker image tag to deploy"
}

variable "dashboard_instance_type" {
  type        = string
  default     = "t3.micro"
  description = "EC2 size for the dashboard instance in the private subnet"
}

# Bucket นี้ "สร้างเอง" นอก Terraform (ดู HANDOFF.md) เพราะเป็นที่เก็บ state ของ dashboard
# ที่ต้องรอดจาก terraform destroy ไม่ใช่ resource ที่ถูกสร้าง-ลบตามรอบ deploy
variable "data_bucket" {
  type        = string
  default     = "prod-pai-logs"
  description = "Existing S3 bucket the aggregator writes to and the dashboard reads from"
}

variable "data_key" {
  type        = string
  default     = "dashboard/current_infra.json"
  description = "Object key of the infrastructure snapshot inside data_bucket"
}

# แยกจาก image_tag เพราะคนละ repo คนละสาย: image_tag คือ commit ของแอป todo-list
# ส่วนตัวนี้คือ commit ของ repo นี้ที่ build หน้า dashboard ขึ้น ECR
variable "dashboard_image_tag" {
  type        = string
  default     = "latest"
  description = "Docker image tag of the dashboard image in ECR"
}
