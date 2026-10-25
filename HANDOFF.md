# ส่งต่อให้เพื่อนไปเทส — ต้องแก้จุดไหนบ้าง

เช็คลิสต์สั้นๆ ว่าถ้าจะย้ายไปรันใน **AWS account กับ GitHub ของตัวเอง** ต้องแก้ไฟล์ไหนบรรทัดไหน
ขั้นตอนเต็ม (ติดตั้งเครื่องมือ, deploy, เช็คผล, troubleshooting) อยู่ใน [`README.md`](README.md)

---

## 1. ต้องสร้างใน AWS ก่อน (Terraform ไม่สร้างให้)

ทำครั้งเดียวต่อ account ขาดอันไหน Infra Setup ตกตรงนั้นเลย

| ของ | คำสั่ง | ถ้าไม่มีจะเจอ |
|---|---|---|
| S3 bucket เก็บ tfstate | `aws s3 mb s3://prodpai-tfstate-<ชื่อตัวเอง> --region us-east-1` | `Backend initialization required` |
| S3 bucket เก็บข้อมูล dashboard | `aws s3 mb s3://prod-pai-logs --region us-east-1` | `data.aws_s3_bucket.dashboard_data` หา bucket ไม่เจอ (workflow สร้างให้เองถ้ารันผ่าน Actions) |
| ECR repo ของแอป | `aws ecr create-repository --repository-name plodphai-todo-list --region us-east-1` | `couldn't find resource` ที่ `data.aws_ecr_repository.app_repo` |
| ECR repo ของ dashboard | `aws ecr create-repository --repository-name prodpai_dashboard --region us-east-1` | `couldn't find resource` ที่ `data.aws_ecr_repository.dashboard_repo` |

**bucket ของ dashboard ตั้งใจไม่ให้ Terraform สร้าง** เพราะ `terraform destroy` จะลบ snapshot
และบันทึกเหตุการณ์ย้อนหลังไปด้วย — ส่วนเครื่อง dashboard ตอนนี้ **อยู่ใน Terraform แล้ว** (ต่างจากเวอร์ชันเก่า
ที่ต้องไปสร้างเองใน console)

---

## 2. ไฟล์ที่ต้องแก้ใน repo นี้ (`prodpai_cloud`)

| ไฟล์ | ค่าเดิม | แก้เป็น | จำเป็นแค่ไหน |
|---|---|---|---|
| `terraform.tf` backend | `bucket = "prodpai-tfstate-storage"` | bucket ของตัวเองจากข้อ 1 | **ต้องแก้** — workflow ไม่ส่ง `-backend-config` ให้ ไม่แก้แล้วมันจะไปเขียน state ใน account เจ้าของเดิม (403) |
| `ec2.tf` `app_repo` | `name = "plodphai-todo-list"` | ชื่อ ECR repo จากข้อ 1 | แก้เมื่อสร้าง ECR ด้วยชื่ออื่น |
| `.github/workflows/infra_setup.yaml` + `launch_image.yaml` | `ECR_REPOSITORY: plodphai-todo-list` | ชื่อเดียวกับใน `ec2.tf` | แก้คู่กันเสมอ ไม่ตรงกันจะไปเช็ค image ผิด repo |
| `variables.tf` `data_bucket` | `default = "prod-pai-logs"` | bucket จากข้อ 1 ถ้าใช้ชื่ออื่น | แก้คู่กับ `infra_setup.yaml` ที่ hardcode ชื่อไว้ในขั้นสร้าง bucket |
| `locals.tf` | `Owner = "nuntawatrdd"` | ชื่อตัวเอง | ไม่จำเป็น เป็นแค่ tag |
| `variables.tf` | `project_name = "prod-pai"`, `environment = "env"` | ตามใจ | ไม่จำเป็น แต่แก้แล้วชื่อ resource เปลี่ยนหมด รวมชื่อ ASG ที่ `launch_image.yaml` ค้นหาด้วย prefix `prod-pai-env-asg` |

แถวแรกคือที่ต้องแก้จริงๆ ที่เหลือเปิดดูแล้วผ่านได้

---

## 3. ไฟล์ที่ต้องแก้ใน repo แอป (`plodphai-todo-list`)

| ไฟล์ | ค่าเดิม | แก้เป็น |
|---|---|---|
| `.github/config.env` | `ECR_REPOSITORY=plodphai-todo-list` | ชื่อเดียวกับใน `ec2.tf` |
| `.github/workflows/ci.yml` | `AWS_REGION: us-east-1` | แก้เฉพาะถ้าย้าย region (ดูข้อ 5) |

ไฟล์แอปจริงๆ (`index.html`, `js/`, `css/`, `Dockerfile`, `nginx.conf`, `tests/`) ไม่ต้องแก้เลย
ECR registry URL ไม่มี hardcode เลข account ไว้ที่ไหน CI หาเองจาก `amazon-ecr-login` ตอน run

---

## 4. GitHub Secrets

Settings → Secrets and variables → Actions ของแต่ละ repo

| Repo | Secret | เอาค่าจากไหน |
|---|---|---|
| `plodphai-todo-list` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | AWS Academy → AWS Details → AWS CLI: Show |
| `prodpai_cloud` | 3 ตัวเดียวกัน | ชุดเดียวกัน |

- ไม่ต้องมี `DASHBOARD_TOKEN` อีกแล้ว — dashboard ไม่รับข้อมูลที่ POST เข้ามาแล้ว
  มันอ่านจาก S3 ที่ Lambda เขียนไว้ ไม่มี endpoint ให้ยิงข้อมูลเข้า
- คีย์ AWS Academy หมดอายุทุก ~4 ชม. หมดแล้วต้องวางใหม่ทั้ง 3 ตัว ในทั้งสอง repo และใน `~/.aws/credentials`
- **credentials ของ repo แอปต้องการมากกว่าแค่ push ECR แล้ว** เพราะ CI ทำ rollout เองหลัง push
  แต่ต้องการแค่ 4 อย่าง ไม่ต้องมีสิทธิ์แก้ launch template แล้ว:
  `ssm:PutParameter` (เฉพาะ `/prod-pai/app/image_tag`), `autoscaling:DescribeAutoScalingGroups`,
  `autoscaling:DescribeInstanceRefreshes`, `autoscaling:StartInstanceRefresh`
  ใน AWS Academy ใช้คีย์ชุดเดียวกันอยู่แล้วจึงผ่าน แต่ถ้าส่งต่อให้คนนอกจริงๆ นี่คือการให้สิทธิ์แตะของที่รันอยู่
  ถ้าไม่อยากให้ ให้ลบ job `rollout` ออกจาก `ci.yml` แล้วกลับไปกด **Launch Image** เองที่ repo นี้แทน

---

## 5. ถ้าจะย้าย region — แนะนำว่าอยู่ `us-east-1` ต่อ

`var.aws_region` มีอยู่ แต่มีหลายจุด hardcode `us-east-1` ไว้ไม่ผ่าน variable แก้ไม่ครบแล้ว apply พังแบบหาสาเหตุยาก

| ไฟล์ | เรื่อง |
|---|---|
| `locals.tf` | **อันนี้สำคัญสุด** — availability zone เขียนตรง `us-east-1a` / `us-east-1b` ไว้ใน private subnet ลืมแก้ = apply พัง |
| `terraform.tf` | region ของ S3 backend |
| `variables.tf` | `default` ของ `aws_region` |
| `ec2.tf` | `--region us-east-1` ในคำสั่ง terminate builder |
| `scripts/userdata.sh` | รับ region ผ่าน templatefile แล้ว ไม่มี hardcode เหลือ (ไม่ต้องแก้) |
| `scripts/falco_setup.sh` | region ของ falcosidekick และ systemd environment |
| `.github/workflows/*.yaml` / `destroy.yml` | `AWS_REGION` |
| `scripts/dashboard_aggregator.py` | client ของ Cost Explorer ตรึงไว้ที่ `us-east-1` (ถูกแล้ว — CE มีที่นั่นที่เดียว) |
| repo แอป `.github/workflows/ci.yml` | `AWS_REGION` |

AWS Academy ให้ `us-east-1` อยู่แล้ว

---

## 6. ของที่ไม่ต้องแก้ (หลายคนพลาดไปแก้)

- `ec2.tf` และ `lambda.tf` — `LabInstanceProfile` กับ `LabRole` มีเหมือนกันทุก AWS Academy account อย่าเปลี่ยนเป็น role ที่สร้างเอง
- `ec2.tf` — `owners = ["099720109477"]` เป็น account ของ Canonical (เจ้าของ Ubuntu AMI) ไม่ใช่ของเรา
- `lambda.tf` — ARN สร้างจาก `data.aws_caller_identity.current.account_id` อยู่แล้ว เปลี่ยน account เอง
- `dashboard/dashboard_app.py` — อ่าน bucket/key จาก env ที่ `setup_dashboard.sh` ส่งให้ ไม่มีค่าฝังในโค้ด

---

## 7. สิทธิ์ที่ Lambda ต้องมี

ทั้งสองตัวใช้ `LabRole` ซึ่งใน AWS Academy กว้างพออยู่แล้ว ถ้าย้ายไป account ที่คุม IAM เอง ต้องให้:

| Lambda | สิทธิ์ |
|---|---|
| `quarantine_taken_instance` | `ec2:DescribeInstances`, `ec2:ModifyInstanceAttribute`, `ec2:CreateSnapshot`, `ec2:CreateTags`, `autoscaling:DetachInstances`, `s3:PutObject` |
| `prod-pai-Infra-Aggregator` (ถูกเรียกจาก schedule, EventBridge และ Lambda quarantine) | `ec2:Describe*`, `elasticloadbalancing:Describe*`, `autoscaling:Describe*`, `wafv2:List*`/`wafv2:Get*`, `cloudwatch:GetMetricStatistics`, `s3:PutObject`/`GetObject`/`ListBucket`, และ `ce:GetCostAndUsage`/`ce:GetCostForecast` ถ้าอยากได้ค่าใช้จ่ายของจริง |

เครื่องใน ASG (`LabInstanceProfile`) ต้องมี `ssm:GetParameter` ด้วย เพราะ user data
อ่าน image tag จาก SSM ตอนบูต ถ้าอ่านไม่ได้สคริปต์จะจบด้วย error แล้วปล่อยให้ ELB health check
จับว่า unhealthy ซึ่งดีกว่าปล่อยเครื่องเปล่าขึ้นมารับงาน

ไม่มี `ce:*` ก็ยังใช้ได้ หน้าเว็บจะตกไปใช้ตัวเลขประเมินแทนแล้วบอกไว้ว่าเป็นค่าประเมิน

---

## 8. กับดักที่ต้องรู้ก่อนกด

- **ห้ามกด Infra Setup ซ้ำตอนของยังอยู่** = `terraform apply` รอบสอง จะสร้าง builder ใหม่แล้วลบ AMI ที่ launch template ใช้อยู่ (ข้อห้าม #3 ใน README) — workflow กันไว้ให้แล้ว แต่ติ๊ก `allow_reapply` ข้ามได้ อย่าติ๊กถ้าไม่เข้าใจผลที่ตามมา
- **เปลี่ยน image ของแอปต้องผ่าน instance refresh เท่านั้น** (CI ฝั่งแอปทำให้เอง หรือกด `Launch Image`)
  ห้ามลบ ASG หรือเครื่องทิ้งเอง
- **push เข้า `main` ของ repo แอป = ปล่อยขึ้น production ทันที** ไม่มีขั้นให้คนกดยืนยันอีกแล้ว
  งานที่ยังไม่พร้อมปล่อยให้อยู่บน branch อื่นแล้วเปิด PR (PR ทดสอบและสแกนให้ แต่ไม่ push ไม่ rollout)
- **CI ฝั่งแอปกด Run workflow จาก branch ไหนก็ push ขึ้น ECR** เพราะเงื่อนไขเป็น `if: github.event_name != 'pull_request'` ไม่ได้เช็ค branch — จะกันจริงต้องเปลี่ยนเป็น `github.ref == 'refs/heads/main'`
- **ไฟล์ `.sh` ต้องเป็น LF** ตั้ง `git config --global core.autocrlf input` ก่อน clone
- **ห้ามรัน Terraform จาก PowerShell/CMD** `null_resource.terminate_builder` ใช้ `/bin/sh` ให้ใช้ WSL
- **เครื่องที่โดน Falco กักจะไม่หายไปเอง** เพราะถูก detach ออกจาก ASG ไว้เป็นหลักฐาน ต้องลบเองเมื่อสืบเสร็จ และ EBS ตั้ง `DeleteOnTermination=false` ไว้ ลบเครื่องแล้ว volume ยังอยู่ ต้องลบแยก

---

## 9. ลำดับทำครั้งแรก

1. `git config --global core.autocrlf input` แล้ว fork + clone ทั้งสอง repo
2. วาง AWS credentials ใน WSL แล้ว `aws sts get-caller-identity` ต้องเห็น ARN ของ voclabs
3. สร้าง bucket + ECR repo (ข้อ 1)
4. แก้ backend bucket ใน `terraform.tf` และชื่อ ECR ถ้าใช้ชื่ออื่น (ข้อ 2)
5. ใส่ Secrets ทั้งสอง repo (ข้อ 4)
6. push repo แอปขึ้น `main` → รอ CI ผ่านครบทุก job → เช็คว่ามี `latest` ใน ECR
7. Actions ของ repo นี้ → **Infra Setup** → Run workflow (`image_tag` = `latest`)
8. เปิด `http://<alb>/` และ `http://<alb>/dashboard` จาก step summary
9. จากนี้ push เข้า `main` ของ repo แอปได้เลย CI จะ push ขึ้น ECR แล้ว rollout ให้เอง
   (อยากย้อน tag เก่า → **Launch Image** ที่ repo นี้ ไม่ใช่ Infra Setup ซ้ำ)
10. เลิกใช้แล้ว → **Destroy** → พิมพ์ `destroy` ยืนยัน
