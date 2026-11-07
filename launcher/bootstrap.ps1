<#
  VNC Link — one-click bootstrap / self-repair launcher
  -----------------------------------------------------
  هیچ پیش‌نیازی روی سیستم مقصد لازم نیست: پایتون، cloudflared، noVNC و TightVNC
  همه داخل پوشهٔ _internal هستند. این اسکریپت فقط:
    ۱) سالم‌بودن اجزا را چک می‌کند
    ۲) اگر چیزی کم بود، بستهٔ کامل را از گیت‌هاب می‌گیرد و ترمیم می‌کند
    ۳) فایل‌ها را Unblock می‌کند (هشدار SmartScreen ناشی از دانلود)
    ۴) اینترنت و آزاد بودن پورت‌ها را بررسی می‌کند
    ۵) برنامه را اجرا می‌کند
#>
param(
    [switch]$CheckOnly,
    [switch]$Silent,
    [string]$RepairFrom
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$app    = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$exe    = Join-Path $app 'VNC-Link.exe'
$assets = Join-Path $app '_internal\assets'
$version = '1.2.0'
$zipUrl = "https://github.com/atomich000w/vnc-link/releases/download/v$version/VNC-Link-$version-portable.zip"

function Line($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Step($text) { Line "   ... $text" 'DarkGray' }
function Ok($text)   { Line "   OK  $text" 'Green' }
function Warn($text) { Line "   !   $text" 'Yellow' }
function Fail($text) { Line "   X   $text" 'Red' }

$parts = @(
    @{ Path = $exe;                                         Name = 'برنامهٔ اصلی (VNC-Link.exe)' },
    @{ Path = (Join-Path $assets 'cloudflared.exe');         Name = 'cloudflared (تونل کلادفلر)' },
    @{ Path = (Join-Path $assets 'novnc\index.html');        Name = 'صفحهٔ نمایش noVNC' },
    @{ Path = (Join-Path $assets 'novnc\core\rfb.js');       Name = 'موتور noVNC' },
    @{ Path = (Join-Path $assets 'tvnserver\tvnserver.exe'); Name = 'سرور TightVNC' }
)

Clear-Host
Line ''
Line "  ==================================================" 'Cyan'
Line "     VNC Link $version  -  نصب و اجرای تک‌کلیکی" 'Cyan'
Line "  ==================================================" 'Cyan'
Line ''

# ---------------------------------------------------------------- 1) windows
Line '  [1/5] بررسی ویندوز' 'White'
$os = [Environment]::OSVersion.Version
if ($os.Major -lt 10) {
    Fail "این نسخه از ویندوز پشتیبانی نمی‌شود (نیاز: ویندوز ۱۰ یا ۱۱). نسخهٔ فعلی: $os"
    if (-not $Silent) { Read-Host 'Enter برای خروج' | Out-Null }
    exit 1
}
if ([Environment]::Is64BitOperatingSystem) {
    Ok "ویندوز $($os.Major).$($os.Minor)  |  ۶۴ بیتی"
} else {
    Fail 'این برنامه برای ویندوز ۶۴ بیتی ساخته شده است.'
    if (-not $Silent) { Read-Host 'Enter برای خروج' | Out-Null }
    exit 1
}

# ---------------------------------------------------------------- 2) files
Line ''
Line '  [2/5] بررسی پیش‌نیازها (همه باید داخل همین پوشه باشند)' 'White'
$missing = @()
foreach ($p in $parts) {
    if (Test-Path -LiteralPath $p.Path) {
        Ok $p.Name
    } else {
        Warn "$($p.Name)  ->  پیدا نشد"
        $missing += $p.Name
    }
}

if ($missing.Count -gt 0) {
    Line ''
    Line "  [*] $($missing.Count) مورد ناقص است؛ تلاش برای ترمیم خودکار" 'Yellow'

    # source of the repair package: -RepairFrom, a nearby ZIP, or the GitHub release
    $source = $RepairFrom
    if (-not $source) {
        $candidates = @(
            (Get-ChildItem -LiteralPath $app -Filter '*portable*.zip' -File -ErrorAction SilentlyContinue),
            (Get-ChildItem -LiteralPath (Split-Path $app -Parent) -Filter '*portable*.zip' -File -ErrorAction SilentlyContinue)
        ) | Where-Object { $_ } | Select-Object -First 1
        if ($candidates) { $source = $candidates.FullName }
    }
    if (-not $source) { $source = $zipUrl }

    $tmp = Join-Path $env:TEMP ('vnclink_boot_' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Path $tmp -Force | Out-Null
        if ($source -match '^https?://') {
            Step "دانلود بستهٔ کامل از $source ..."
            $zip = Join-Path $tmp 'app.zip'
            $wc = New-Object Net.WebClient
            $wc.Headers.Add('User-Agent', "VNC-Link-Bootstrap/$version")
            $wc.DownloadFile($source, $zip)
        } else {
            $zip = $source
            Step "استفاده از بستهٔ محلی: $zip"
        }
        Step 'استخراج ...'
        Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force

        $inner = $tmp
        if (-not (Test-Path (Join-Path $tmp 'VNC-Link.exe'))) {
            $sub = Get-ChildItem -LiteralPath $tmp -Directory |
                   Where-Object { Test-Path (Join-Path $_.FullName 'VNC-Link.exe') } |
                   Select-Object -First 1
            if ($sub) { $inner = $sub.FullName }
        }
        if (-not (Test-Path (Join-Path $inner 'VNC-Link.exe'))) {
            throw 'ساختار بستهٔ ترمیم شناخته نشد (VNC-Link.exe داخلش نیست).'
        }
        Copy-Item -Path (Join-Path $inner '*') -Destination $app -Recurse -Force
        Ok 'ترمیم انجام شد.'
    } catch {
        $msg = $_.Exception.Message
        Fail "ترمیم خودکار ممکن نشد: $msg"
        if ($msg -match '404|403|Not Found|Forbidden' -or $source -match 'github\.com') {
            Warn 'بستهٔ گیت‌هاب برای دانلود عمومی در دسترس نیست (ریپو خصوصی است).'
            Warn 'راه‌حل ۱: کل پوشهٔ VNC-Link را از سیستم مبدأ کامل کپی کنید.'
            Warn 'راه‌حل ۲: فایل ZIP را کنار همین پوشه بگذارید (یا کنار پوشهٔ والد) و دوباره اجرا کنید.'
            Warn 'راه‌حل ۳: در خط فرمان:  Run-VNC-Link.bat -RepairFrom "D:\path\VNC-Link-$version-portable.zip"'
        } else {
            Warn 'اینترنت را بررسی کنید یا پوشهٔ کامل برنامه را دستی کپی کنید.'
        }
        if (-not $Silent) { Read-Host 'Enter برای خروج' | Out-Null }
        exit 1
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------- 3) unblock
Line ''
Line '  [3/5] رفع مسدودی فایل‌ها (SmartScreen)' 'White'
try {
    $count = 0
    Get-ChildItem -LiteralPath $app -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in '.exe', '.dll', '.ps1', '.bat', '.js', '.html' } |
        ForEach-Object {
            try { Unblock-File -LiteralPath $_.FullName -ErrorAction Stop; $count++ } catch {}
        }
    Ok "فایل‌ها آزاد شدند ($count فایل بررسی شد)."
} catch {
    Warn 'Unblock انجام نشد (اهمیت ندارد).'
}

# ---------------------------------------------------------------- 4) network
Line ''
Line '  [4/5] بررسی اینترنت و پورت‌ها' 'White'
$online = $false
foreach ($target in @(@('1.1.1.1', 443), @('api.telegram.org', 443))) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $task = $client.ConnectAsync($target[0], $target[1])
        if ($task.Wait(2000) -and $client.Connected) { $online = $true; $client.Close(); break }
        $client.Close()
    } catch {}
}
if ($online) { Ok 'اینترنت برقرار است.' }
else { Warn 'اینترنت در دسترس نیست — برای ساخت لینک و ارسال تلگرام به اینترنت نیاز است.' }

function Test-Port($port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $t = $c.ConnectAsync('127.0.0.1', $port)
        $busy = $t.Wait(400) -and $c.Connected
        $c.Close()
        return $busy
    } catch { return $false }
}
$busyPorts = @()
foreach ($port in 5900, 6080) { if (Test-Port $port) { $busyPorts += $port } }
if ($busyPorts.Count -gt 0) {
    Warn "پورت(های) $($busyPorts -join ', ') اشغال است — یک VNC یا نمونهٔ دیگر برنامه باز است."
} else {
    Ok 'پورت‌های 5900 و 6080 آزادند.'
}

# ---------------------------------------------------------------- 5) launch
Line ''
Line '  [5/5] اجرا' 'White'
if ($CheckOnly) {
    Ok 'بررسی کامل شد؛ همه‌چیز آماده است (برنامه اجرا نشد چون -CheckOnly داده شده).'
    Line ''
    exit 0
}
$running = Get-Process -Name 'VNC-Link' -ErrorAction SilentlyContinue
if ($running) {
    Warn 'برنامه از قبل باز است؛ پنجرهٔ همان را ببینید.'
    exit 0
}
if ($busyPorts.Count -gt 0) {
    Warn 'ابتدا برنامه/سرور VNC دیگری را ببندید.'
    if (-not $Silent) { Read-Host 'Enter برای خروج' | Out-Null }
    exit 1
}
try {
    Start-Process -FilePath $exe -WorkingDirectory $app
    Start-Sleep -Milliseconds 1500
    Ok 'برنامه اجرا شد.'
    Line ''
    Line '  راهنما: رمز بگذارید (یا خالی بگذارید) و «ایجاد لینک» را بزنید.' 'DarkGray'
    Start-Sleep -Milliseconds 1200
    exit 0
} catch {
    Fail "اجرا ممکن نشد: $($_.Exception.Message)"
    if (-not $Silent) { Read-Host 'Enter برای خروج' | Out-Null }
    exit 1
}
