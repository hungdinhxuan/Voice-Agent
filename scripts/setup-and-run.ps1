# Dựng và chạy toàn bộ voice agent bằng một lệnh.
#
# Kiểm tra từng thứ pipeline cần, chỉ tải cái nào còn thiếu, rồi bật server.
# Chạy lại nhiều lần vô hại: mọi bước đều kiểm tra trước khi làm.
#
#   .\scripts\setup-and-run.ps1              đầy đủ
#   .\scripts\setup-and-run.ps1 -SkipSetup   bỏ kiểm tra, bật server luôn
#   .\scripts\setup-and-run.ps1 -NoXiaozhi   không bật adapter cho thiết bị ESP32
#
# Hoặc nháy đúp run.cmd ở thư mục gốc.

[CmdletBinding()]
param(
    [switch]$SkipSetup,
    [switch]$NoXiaozhi,
    [string]$ConfigPath = 'config.local.yaml'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text)  { Write-Host "`n== $text" -ForegroundColor Cyan }
function Ok($text)    { Write-Host "   $text" -ForegroundColor Green }
function Warn($text)  { Write-Host "   $text" -ForegroundColor Yellow }
function Die($text)   { Write-Host "`n!! $text" -ForegroundColor Red; exit 1 }

$OLLAMA_URL = 'http://127.0.0.1:11434'
$LLM_MODEL  = 'qwen3.5:4b'

# Hai model ASR, tên thư mục đúng như Hugging Face cache đặt.
$ASR_MODELS = @(
    @{ repo = 'nvidia/parakeet-ctc-0.6b-Vietnamese'; file = 'parakeet-ctc-0.6b-vi.nemo' },
    @{ repo = 'nvidia/parakeet-tdt-0.6b-v3';         file = 'parakeet-tdt-0.6b-v3.nemo' }
)

function Test-HfCached($repo) {
    $home_ = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:USERPROFILE '.cache\huggingface' }
    $slug = 'models--' + ($repo -replace '/', '--')
    $dir = Join-Path $home_ "hub\$slug\snapshots"
    return (Test-Path $dir) -and (Get-ChildItem $dir -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0
}

# ---------------------------------------------------------------- chuẩn bị

if (-not $SkipSetup) {

    Step 'Kiểm tra uv'
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Die @'
Chưa có uv. Cài bằng:
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
rồi mở lại cửa sổ terminal.
'@
    }
    Ok ((uv --version) -join ' ')

    Step 'Đồng bộ thư viện Python'
    uv sync
    if ($LASTEXITCODE -ne 0) { Die 'uv sync thất bại.' }
    Ok 'xong'

    Step 'Kiểm tra Ollama'
    $up = $false
    try { Invoke-WebRequest "$OLLAMA_URL/api/tags" -TimeoutSec 3 -UseBasicParsing | Out-Null; $up = $true } catch { }
    if (-not $up) {
        if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
            Die 'Chưa có Ollama. Tải ở https://ollama.com/download rồi chạy lại.'
        }
        Warn 'Ollama chưa chạy, đang bật nền...'
        Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden
        foreach ($i in 1..20) {
            Start-Sleep -Seconds 1
            try { Invoke-WebRequest "$OLLAMA_URL/api/tags" -TimeoutSec 2 -UseBasicParsing | Out-Null; $up = $true; break } catch { }
        }
        if (-not $up) { Die 'Ollama không lên sau 20 giây. Thử chạy tay: ollama serve' }
    }
    Ok "đang chạy tại $OLLAMA_URL"

    Step "Kiểm tra model LLM ($LLM_MODEL)"
    $tags = (Invoke-WebRequest "$OLLAMA_URL/api/tags" -UseBasicParsing).Content | ConvertFrom-Json
    if ($tags.models.name -contains $LLM_MODEL) {
        Ok 'đã có'
    } else {
        Warn "chưa có, đang tải (khoảng 3.4 GB)..."
        ollama pull $LLM_MODEL
        if ($LASTEXITCODE -ne 0) { Die "Tải $LLM_MODEL thất bại." }
        Ok 'xong'
    }

    Step 'Kiểm tra model nhận dạng giọng nói'
    foreach ($m in $ASR_MODELS) {
        if (Test-HfCached $m.repo) {
            Ok ("{0} đã có" -f $m.repo)
        } else {
            Warn ("{0} chưa có, đang tải..." -f $m.repo)
            uv run hf download $m.repo $m.file
            if ($LASTEXITCODE -ne 0) { Die ("Tải {0} thất bại." -f $m.repo) }
            Ok 'xong'
        }
    }
    Warn 'Model TTS tải ở lần phát tiếng đầu tiên, không cần làm gì.'
}

# ---------------------------------------------------------------- cấu hình
#
# Dùng config riêng thay vì sửa config.yaml: bản trong repo là thứ được commit,
# còn cái này là của máy bạn. Trước đây hai file cấu hình lệch nhau đã làm
# /xiaozhi trả 404 mà không ai hiểu vì sao.

Step "Cấu hình ($ConfigPath)"
if (-not (Test-Path $ConfigPath)) {
    Copy-Item 'config.yaml' $ConfigPath
    Ok "vừa tạo từ config.yaml"
} else {
    Ok 'đã có, giữ nguyên các sửa đổi của bạn'
}

if (-not $NoXiaozhi) {
    $text = [System.IO.File]::ReadAllText((Resolve-Path $ConfigPath), [System.Text.Encoding]::UTF8)
    if ($text -match '(?m)^(xiaozhi:(?:\r?\n(?:[ \t].*)?)*?\r?\n[ \t]+enabled:[ \t]*)false') {
        # Set-Content cua PowerShell 5.1 ghi bang ANSI, lam hong dau tieng Viet
        # trong config va app doc khong ra. UTF-8 khong BOM: co BOM thi YAML
        # parser lai nghen ngay ky tu dau tien.
        $utf8 = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText((Resolve-Path $ConfigPath), ($text -replace '(?m)^(xiaozhi:(?:\r?\n(?:[ \t].*)?)*?\r?\n[ \t]+enabled:[ \t]*)false', '${1}true'), $utf8)
        Ok 'đã bật xiaozhi.enabled cho thiết bị ESP32'
    } else {
        Ok 'xiaozhi đã bật'
    }
}

# ---------------------------------------------------------------- chạy

$cfg = [System.IO.File]::ReadAllText((Resolve-Path $ConfigPath), [System.Text.Encoding]::UTF8)
$hostMatch = [regex]::Match($cfg, '(?m)^web:\s*\r?\n(?:[ \t]+.*\r?\n)*?[ \t]+host:[ \t]*(\S+)')
$portMatch = [regex]::Match($cfg, '(?m)^web:\s*\r?\n(?:[ \t]+.*\r?\n)*?[ \t]+port:[ \t]*(\d+)')
$webHost = if ($hostMatch.Success) { $hostMatch.Groups[1].Value } else { '127.0.0.1' }
$webPort = if ($portMatch.Success) { $portMatch.Groups[1].Value } else { '8080' }
$shown = if ($webHost -eq '0.0.0.0') { '127.0.0.1' } else { $webHost }

Step 'Bật voice agent'
Write-Host "   Trình duyệt   : http://${shown}:$webPort/" -ForegroundColor Green
if (-not $NoXiaozhi) {
    Write-Host "   Console thiết bị: http://${shown}:$webPort/xiaozhi" -ForegroundColor Green
    Write-Host "   WebSocket ESP32 : ws://${shown}:$webPort/xiaozhi/v1/" -ForegroundColor Green
}
Write-Host "   Ctrl+C de dung.`n" -ForegroundColor DarkGray
Write-Host "   Lan dau mat khoang 30 giay de nap VAD, ASR va TTS.`n" -ForegroundColor DarkGray

uv run python main.py --web --config $ConfigPath
