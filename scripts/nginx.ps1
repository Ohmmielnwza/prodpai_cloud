param (
    [Parameter(Mandatory=$true)]
    [string]$TargetIp
)

$url = "http://$TargetIp/ready.txt"
$maxRetries = 30
$count = 0

Write-Host "Verifying Nginx on $url ..."
do {
    $count++
    Write-Host "[$count/$maxRetries] Waiting for Nginx..."
    Start-Sleep -Seconds 10
    try {
        $res = Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        $status = $res.StatusCode
    } catch {
        $status = 0
    }
} while ($status -ne 200 -and $count -lt $maxRetries)

if ($status -eq 200) {
    Write-Host "Nginx is ready! Proceeding with AMI creation."
    exit 0
} else {
    Write-Error "Timeout waiting for Nginx on $url"
    exit 1
}