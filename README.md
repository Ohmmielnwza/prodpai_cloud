# Prod-Pai Cloud — Infrastructure + Dashboard

Terraform + GitHub Actions สำหรับวาง topology ทั้งชุดบน AWS Academy พร้อมหน้า dashboard
ที่แสดงของจริงบน AWS แบบ near-realtime และระบบกักเครื่องอัตโนมัติเมื่อ Falco จับได้ว่าโดนเจาะ

ถ้าจะย้ายไปรันในบัญชี AWS / GitHub ของตัวเอง อ่าน [`HANDOFF.md`](HANDOFF.md) ควบคู่

---

## ภาพรวมระบบ

แยกเป็น 2 ฝั่ง คนละ repo คนละสิทธิ์:

**ฝั่งแอป (`plodphai-todo-list`) — CI เท่านั้น**

```
repo แอป ── CI: test → Semgrep/Trivy/Gitleaks/hadolint → build → smoke → scan image → SBOM
                 └─ push ──▶ ECR (tag: commit SHA + latest)
                 └─ rollout: launch template version ใหม่ → ติด tag ที่ ASG → ทยอยเปลี่ยนเครื่อง
                    (เฉพาะ main เท่านั้น)
```

CI **ไม่แตะรูปร่างของ infrastructure** — ขั้น rollout แค่เปลี่ยนว่าเครื่องรัน image ไหน
ไม่มีการสร้างหรือลบ resource ใดๆ การวาง topology ทั้งชุดยังเป็นของฝั่ง infra อย่างเดียว

**ฝั่ง infra (repo นี้) — Infra Setup / Launch Image / Destroy**

```
Actions → Infra Setup (กดเอง ครั้งแรกครั้งเดียว)
   ├─ เช็คว่า image tag ของแอปอยู่ใน ECR จริง
   ├─ build + push image ของ dashboard ขึ้น ECR
   └─ terraform apply → VPC / ALB / ASG / Lambda / dashboard EC2

Actions → Launch Image (กดเอง เมื่อต้องการย้อน tag เก่าหรือปล่อย tag ที่เลือกเอง)
   ├─ create-launch-template-version (เปลี่ยนเฉพาะ IMAGE_TAG ใน user data)
   └─ ติด tag ที่ ASG → instance_refresh แบบ Rolling ทยอยเปลี่ยนเครื่องเอง
```

> ปกติไม่ต้องกด Launch Image แล้ว เพราะ CI ฝั่งแอปทำขั้นนี้ให้เองหลัง push ขึ้น ECR
> มันมีไว้สำหรับ rollback และกรณีที่อยากเลือก tag เอง (ตรรกะเดียวกันเป๊ะ)

```
Internet ──▶ WAF ──▶ ALB :80 ─┬─ "/"           ──▶ TG :80   ──▶ ASG 2–3 เครื่อง (private a/b)
                              │                                  └─ docker run -p 80:8080
                              └─ "/dashboard*" ──▶ TG :5000 ──▶ dashboard EC2 (private b)

Lambda prod-pai-Infra-Aggregator ──(ทุก 2 นาที)──▶ กวาด AWS API ──▶ s3://prod-pai-logs/dashboard/current_infra.json
                                                                         ▲
                              dashboard EC2 อ่านผ่าน S3 Gateway Endpoint ─┘  (ไม่ออกอินเทอร์เน็ต)

Falco บนทุกเครื่อง ── Critical ──▶ CloudWatch /falco/alerts ──▶ Lambda quarantine
                                     ├─ detach ออกจาก ASG (ASG ปั้นเครื่องใหม่แทน เครื่องเดิมไม่ถูกลบ)
                                     ├─ snapshot EBS ทุกก้อน + DeleteOnTermination=false
                                     ├─ ย้ายเข้า SG air-gap
                                     └─ ติด tag SecurityStatus=Compromised + เขียน incident ลง S3
```

| Repo | หน้าที่ | ใครรัน |
|---|---|---|
| `prodpai_cloud` (repo นี้) | Terraform, workflow `infra_setup` / `launch_image` / `destroy`, Lambda, โค้ด dashboard | เรา (เจ้าของบัญชี AWS) |
| `plodphai-todo-list` | โค้ดแอป + `ci.yml` ที่ push image ขึ้น ECR แล้ว rollout เข้า ASG ให้เอง | ฝั่งแอป |

- ALB เสิร์ฟได้ **ทีละ 1 app** ขึ้นกับชื่อ ECR ใน `ec2.tf:14` ต้องตรงกับ `ECR_REPOSITORY` ในทั้งสอง workflow
- เครื่องดึง image **ตอนบูตเท่านั้น** การเปลี่ยน image จึงต้องผ่าน instance refresh เสมอ
  CI ฝั่งแอปสั่งให้เองหลัง push หรือกด **Launch Image** เอง — ไม่ใช่กด Infra Setup ซ้ำ

---

## ข้อห้าม (อ่านก่อน)

1. **ห้ามรัน Terraform จาก PowerShell/CMD บน Windows** — `null_resource.terminate_builder` ใช้ `/bin/sh` จะพังด้วย `exec: "/bin/sh": executable file not found` ใช้ **WSL Ubuntu** หรือ Linux/macOS
2. **ห้ามให้ไฟล์ `.sh` เป็น CRLF** — clone บน Windows ด้วย `core.autocrlf=true` จะเจอ `remote-exec provisioner error ... exit status 127` ตั้ง `git config --global core.autocrlf input` **ก่อน clone**
3. **`terraform apply` ครั้งเดียวให้จบ แล้วอย่า apply ซ้ำ** — apply รอบสองจะสร้าง builder ใหม่และ **ลบ AMI ตัวที่ launch template ใช้อยู่** (`ignore_changes = [image_id]`) ทำให้ ASG สร้างเครื่องใหม่ไม่ได้อีก
   workflow `Infra Setup` กันไว้ให้แล้ว ถ้าเจอ ASG อยู่จะหยุดทันที เว้นแต่ติ๊ก `allow_reapply` เอง
   **อยากเปลี่ยนเป็น image ใหม่ → ปล่อยให้ CI ฝั่งแอปทำ หรือกด `Launch Image`**
4. **ห้าม image ที่รันด้วย user `nginx`/`www-data`/`apache` มี `HEALTHCHECK` แบบ shell form** — Falco จะจับ `sh -c ...` เป็น "Shell Spawned by Web User" แล้วกักเครื่องวนไม่จบ Todo List เขียนเป็น exec form (`CMD ["wget", ...]`) จึงไม่โดน เอาแอปอื่นมาใส่ต้องเช็คจุดนี้

---

## 0. เตรียมเครื่องมือ (ใน WSL Ubuntu)

```bash
# AWS CLI v2
curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip -q awscliv2.zip && sudo ./aws/install

# Terraform 1.9.8 (เวอร์ชันที่ทีมใช้)
wget -q https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip
unzip -q terraform_1.9.8_linux_amd64.zip && sudo mv terraform /usr/local/bin/
```

## 1. AWS credentials (AWS Academy)

AWS Academy → AWS Details → AWS CLI: Show แล้ววางลง `~/.aws/credentials`
คีย์หมดอายุทุก ~4 ชั่วโมง หมดแล้วต้องวางใหม่ทั้ง 3 ค่า (รวม `aws_session_token`) และอัปเดต GitHub Secrets ด้วย

```bash
aws sts get-caller-identity    # ต้องเห็น ARN ของ voclabs
```

## 2. สร้างของที่ Terraform ไม่ได้สร้างให้ (ทำครั้งเดียวต่อ account)

```bash
# S3 bucket เก็บ tfstate (ชื่อต้องไม่ซ้ำทั้งโลก)
aws s3 mb s3://prodpai-tfstate-<ชื่อตัวเอง> --region us-east-1

# S3 bucket เก็บข้อมูล dashboard — ตั้งใจไม่ให้ Terraform สร้าง
# จะได้ไม่โดนลบตอน destroy แล้ว snapshot/incident ย้อนหลังยังอยู่
aws s3 mb s3://prod-pai-logs --region us-east-1

# ECR repo ของแอป (ชื่อต้องตรงกับ ec2.tf และทั้งสอง workflow)
aws ecr create-repository --repository-name plodphai-todo-list --region us-east-1

# ECR repo ของ dashboard (workflow สร้างให้เองถ้ายังไม่มี)
aws ecr create-repository --repository-name prodpai_dashboard --region us-east-1
```

> `Infra Setup` จะสร้าง `prod-pai-logs` ให้เองถ้ายังไม่มี แต่สร้างเองไว้ก่อนก็ได้

## 3. เอา image แอปขึ้น ECR (ต้องมี `:latest` ก่อน apply)

push โค้ดเข้า `main` ของ repo แอป → รอ CI ผ่านทุก job → ดู tag จาก step summary ของ run นั้น

```bash
aws ecr describe-images --repository-name plodphai-todo-list \
  --query 'imageDetails[].imageTags' --output text
```

## 4. Deploy

### ผ่าน GitHub Actions (วิธีหลัก)

Actions → **Infra Setup** → Run workflow → ใส่ `image_tag` (SHA จาก CI หรือ `latest`)

workflow จะ: เช็ค image → กันการ apply ทับ → สร้าง bucket ถ้ายังไม่มี → build+push+scan image dashboard
→ `fmt`/`validate`/`plan`/`apply` → รอ target group ทุกตัวเขียว → พิมพ์ URL ลง step summary

### จากเครื่องตัวเอง (WSL)

```bash
terraform init -reconfigure -backend-config="bucket=prodpai-tfstate-<ชื่อตัวเอง>"
terraform apply -var="image_tag=latest"
```

## 5. เช็คว่าใช้ได้

```bash
curl -s "$(terraform output -raw app_url)health"        # ต้องได้ 200
curl -s "$(terraform output -raw dashboard_url)/health" # ต้องได้ {"status":"ok"}
```

| ที่ | ต้องเห็น |
|---|---|
| EC2 → Target Groups → `prod-pai-env-lb-target` | 2 เครื่อง `healthy` (ช่วงแรก `initial` 1–2 นาทีปกติ) |
| EC2 → Target Groups → `dashboard-tg-5000` | 1 เครื่อง `healthy` (health check = `/dashboard/health`) |
| EC2 → Instances | `prod-pai-env-instance` 2 เครื่อง + `prod-pai-env-dashboard` 1 เครื่อง |
| S3 → `prod-pai-logs/dashboard/current_infra.json` | มีไฟล์ และ `update_time` ขยับทุก ~2 นาที |
| เบราว์เซอร์ | `http://<alb>/` เห็นแอป · `http://<alb>/dashboard` เห็นแผนผัง |

> builder (`prod-pai-env-image-builder`) ถูก terminate อัตโนมัติท้าย apply เห็นเป็น `terminated` ปกติ
> และ aggregator ตั้งใจ **ไม่** เอาเครื่องที่ terminated มาแสดงบนแผนผัง

## 6. อัปเดตแอปหลัง deploy ไปแล้ว

**ไม่ต้องทำอะไรเลย** — ฝั่งแอป push เข้า `main` → CI ทดสอบ สแกน build push ขึ้น ECR
แล้วสั่ง rollout ต่อให้ในรันเดียวกัน ใช้เวลารวม ~10–15 นาที

ขั้น rollout สร้าง launch template version ใหม่ (เปลี่ยนแค่ tag ของ image, AMI เดิม)
แล้วติด tag ที่ ASG ให้ `instance_refresh` (Rolling, min healthy 50%, warmup 180s) ทยอยเปลี่ยนเครื่องเอง
ไม่มีการลบเครื่องแล้วสร้างใหม่ทั้งชุด และถ้ามีรอบ refresh ค้างอยู่มันจะรอให้จบก่อน

**ย้อนกลับ (rollback)** — Actions ของ repo นี้ → **Launch Image** → ใส่ tag เก่าที่อยากกลับไป

## 7. ลบทั้งหมด

Actions → **Destroy** → พิมพ์ `destroy` ยืนยัน
(bucket `prod-pai-logs`, bucket ของ tfstate และ image ใน ECR ไม่ถูกลบ)

---

## หน้า Dashboard

เข้าที่ `http://<alb-dns>/dashboard`

- **แผนผังซ้อนชั้น** Region → VPC → Availability Zone → แถบ Public/Private → Subnet → resource
  VPC ที่ติดมากับบัญชี (default 172.31.0.0/16) ถูกพับเก็บไว้ ไม่ปนกับของโปรเจกต์
- **เครื่องที่โดนยึด** ขึ้นกรอบแดงหนา พื้นแดง ไฟกระพริบ พร้อมป้าย `ISOLATED`
- **Healthy** นับเฉพาะสมาชิกของ target group (เครื่องจาก ASG + dashboard) ของ infra อย่าง NAT/IGW/SG ไม่นับ
- **อธิบายแบบง่าย** ปุ่มบนหัวข้อ เปิดแล้วทุกกล่องมีคำอธิบายภาษาไทยหนึ่งบรรทัด
- **ค่าใช้จ่าย** ใช้ไปแล้วเดือนนี้ / อัตราต่อเดือนตอนนี้ / คาดการณ์สิ้นเดือน / อีก 30 วัน
  ถ้าบัญชีเปิด Cost Explorer ให้จะใช้ของจริง ถ้าไม่ให้จะประเมินจาก resource ที่มีอยู่คูณราคา on-demand
  (หน้าเว็บบอกที่มาของตัวเลขไว้ทุกครั้ง)
- **เหตุการณ์ด้านความปลอดภัย** timeline จาก `s3://prod-pai-logs/dashboard/incidents/`

---

## Troubleshooting

| อาการ | สาเหตุ | แก้ |
|---|---|---|
| `remote-exec provisioner error ... exit status 127` | `.sh` เป็น CRLF | `git config core.autocrlf input` แล้ว `rm scripts/*.sh && git checkout -- scripts/` |
| `exec: "/bin/sh": executable file not found in %PATH%` | รันบน Windows | รันใน WSL |
| `Backend initialization required` | bucket ใน config ไม่ตรงกับ cache | `terraform init -reconfigure -backend-config="bucket=..."` |
| `couldn't find resource` ที่ `data.aws_ecr_repository` | ยังไม่สร้าง ECR repo | หัวข้อ 2 |
| `Infra Setup` ตกที่ `มี stack อยู่แล้ว` | ของยังอยู่ กำลังจะ apply ทับ (ข้อห้าม #3) | ใช้ `Launch Image` แทน |
| `Infra Setup` ตกที่ `Check image exists in ECR` | ยังไม่มี tag นั้นใน ECR | รอ CI ฝั่งแอปให้ผ่าน แล้วเอา tag จาก step summary |
| `dashboard-tg-5000` ไม่เขียวสักที | คอนเทนเนอร์ dashboard ยังไม่ขึ้น หรือ image ใน ECR ยังไม่มี | ดู system log ของ `prod-pai-env-dashboard`, เช็คว่ามี `prodpai_dashboard:latest` ใน ECR |
| dashboard ขึ้นแต่ไม่มีข้อมูล | aggregator ยังไม่ยิงรอบแรก หรือเขียน S3 ไม่ได้ | CloudWatch → log ของ `prod-pai-Infra-Aggregator`, เช็คว่ามี bucket `prod-pai-logs` |
| เครื่อง unhealthy วนถูกแทนที่ทุก ~4 นาที | Falco กัก หรือ image ไม่มี `/health` ที่ 8080 | ดู `/falco/alerts` ว่าคำสั่งอะไรโดนจับ |
| ALB ตอบ 403 | WAF rate limit 100 GET/นาที/IP | รอ 1–2 นาที (ตั้งใจไว้) |
| เห็น EC2 3 เครื่อง | เครื่องเก่ากำลัง draining (สูงสุด 5 นาที) | ปกติ |
| เครื่องที่โดนยึดยังอยู่ไม่หายไป | ตั้งใจ — detach ออกจาก ASG แล้วเพื่อเก็บเป็นหลักฐาน | ลบเองเมื่อสืบเสร็จ (snapshot ยังอยู่) |
