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

# default ว่างไว้ให้ terraform destroy (destroy.yml) ไม่ต้องส่งค่านี้
# ส่วน plan / apply ทุกครั้ง precondition ใน ec2.tf บังคับให้ส่ง (infra_setup.yaml ส่ง IP ของ runner)
variable "builder_ssh_cidrs" {
  type        = list(string)
  default     = []
  description = "CIDRs allowed to SSH into the AMI builder (the machine running Terraform)"

  validation {
    condition     = !contains(var.builder_ssh_cidrs, "0.0.0.0/0")
    error_message = "Do not open SSH on the builder to 0.0.0.0/0; pass the runner IP as /32."
  }
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
#
# ไม่มี default: ชื่อ bucket / ECR มาจาก .github/config.env ที่เดียว (workflow ส่งเป็น TF_VAR_*)
# ถ้าลืมส่ง Terraform จะถามหาค่า แทนที่จะแอบไปใช้ชื่อของ account คนอื่น
variable "data_bucket" {
  type        = string
  description = "Existing S3 bucket the aggregator writes to and the dashboard reads from (DATA_BUCKET in .github/config.env)"
}

variable "app_ecr_repository" {
  type        = string
  description = "ECR repository of the todo-list app (APP_ECR_REPOSITORY in .github/config.env)"
}

variable "dashboard_ecr_repository" {
  type        = string
  description = "ECR repository of the dashboard image (DASHBOARD_ECR_REPOSITORY in .github/config.env)"
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
