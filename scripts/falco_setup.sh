#!/bin/bash
set -e

echo "Waiting for cloud-init / OS boot to complete..."
cloud-init status --wait

# -----
# install falco
# -----
curl -s https://falco.org/repo/falcosecurity-packages.asc | sudo gpg --dearmor -o /usr/share/keyrings/falco-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/falco-archive-keyring.gpg] https://download.falco.org/packages/deb stable main" | sudo tee /etc/apt/sources.list.d/falcosecurity.list

sudo apt-get update
sudo apt-get install -y linux-headers-$(uname -r) falco


# -----
# change argument value
# json_output: true
# -----
sudo sed -i 's/json_output: false/json_output: true/' /etc/falco/falco.yaml
sudo sed -i 's/json_include_output_property: false/json_include_output_property: true/' /etc/falco/falco.yaml


# -----
# set falcos for sidekick
# ----- 
sudo sed -i '/^http_output:/,+5 s/enabled: false/enabled: true/' /etc/falco/falco.yaml
sudo sed -i '/^http_output:/,+5 s|url: ""|url: "http://localhost:2801/"|' /etc/falco/falco.yaml

# -----
# install falcosidekick
# -----
VERSION=$(curl -sI https://github.com/falcosecurity/falcosidekick/releases/latest | grep -i "^location:" | awk -F'/' '{print $NF}' | tr -d '\r')
echo "Latest Falcosidekick version is: $VERSION"

curl -L -o falcosidekick.tar.gz "https://github.com/falcosecurity/falcosidekick/releases/download/${VERSION}/falcosidekick_${VERSION#v}_linux_amd64.tar.gz"
tar -xzf falcosidekick.tar.gz
sudo mv falcosidekick /usr/local/bin/
sudo chmod +x /usr/local/bin/falcosidekick
rm -f falcosidekick.tar.gz

sudo mkdir -p /etc/falcosidekick

# -----
# set env
# fill cred after launch instance
# -----

# -----
# set loggroup/logstream
# -----
sudo bash -c 'cat << EOF > /etc/falcosidekick/config.yaml
aws:
  region: "us-east-1"
  cloudwatchlogs:
    loggroup: "/falco/alerts"
    logstream: "falco-events"
EOF'

# -----
# set rules
# -----
sudo bash -c "cat << 'EOF' > /etc/falco/falco_rules.local.yaml
# 1. ตรวจจับการวางหรือแก้ไขไฟล์ Executable ใต้ /bin, /sbin, /usr/bin บน Host
- rule: Host System Binary Tampering
  desc: Detect write attempt to system binary directories on host
  condition: >
    (evt.type in (open, openat) and evt.is_open_write=true and fd.typechar='f')
    and fd.directory in (/bin, /sbin, /usr/bin, /usr/sbin)
    and not proc.name in (apt, dpkg, yum, rpm)
  output: >
    Critical: File created or modified in binary directory (user=%user.name command=%proc.cmdline file=%fd.name)
  priority: Critical
  tags: [host, filesystem, binary_tampering]

# 2. ตรวจจับการสร้างไฟล์หรือ Backdoor ในโฟลเดอร์ Web Root (/var/www)
- rule: Unauthorized Web Root Modification
  desc: Detect changes to web application files
  condition: >
    (evt.type in (open, openat) and evt.is_open_write=true and fd.typechar='f')
    and fd.directory startswith /var/www
    and not proc.name in (git, composer, npm)
  output: >
    Critical: Modification inside web root directory (user=%user.name command=%proc.cmdline file=%fd.name)
  priority: Critical
  tags: [host, web, file_integrity]

# 3. ตรวจจับเมื่อ Web Service มีการสั่งรัน Shell (จำลอง RCE / Web Shell)
- rule: Shell Spawned by Web User
  desc: Detect shell process spawned under web server user
  condition: >
    evt.type in (execve, execveat) and evt.dir=<
    and proc.name in (bash, sh, dash, zsh)
    and user.name in (www-data, nginx, apache)
  output: >
    Critical: Shell spawned under web service user (user=%user.name command=%proc.cmdline parent=%proc.pname)
  priority: Critical
  tags: [host, rce, web_shell]
EOF"

# -----
# setup falcosidekick service
# -----
sudo bash -c 'cat << EOF > /etc/systemd/system/falcosidekick.service
[Unit]
Description=Falcosidekick Service
After=network.target

[Service]
ExecStart=/usr/local/bin/falcosidekick -c /etc/falcosidekick/config.yaml
Restart=always
User=root
Environment="AWS_REGION=us-east-1"
Environment="AWS_DEFAULT_REGION=us-east-1"

[Install]
WantedBy=multi-user.target
EOF'

sudo systemctl daemon-reload
sudo systemctl enable falco-modern-bpf
sudo systemctl restart falco-modern-bpf
sudo systemctl enable falcosidekick.service
sudo systemctl restart falcosidekick

sudo chown www-data:www-data /var/www/html/index.html
sudo chmod 644 /var/www/html/index.html
sudo systemctl enable nginx
sudo systemctl restart nginx
sudo systemctl status nginx --no-pager