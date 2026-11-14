# TravelSync - store Kakao REST API key in local .env without hard-coding it in source files.
# Run this script from the repository root after copying it there.

$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"

$secure = Read-Host "새 Kakao REST API Key 입력" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}

if ([string]::IsNullOrWhiteSpace($plain)) {
    throw "Kakao REST API Key가 비어 있습니다."
}

if (!(Test-Path $envPath)) {
    New-Item -ItemType File -Path $envPath | Out-Null
}

$lines = @(Get-Content $envPath -ErrorAction SilentlyContinue)
$settings = [ordered]@{
    "KAKAO_REST_API_KEY" = $plain
    "KAKAO_ROUTE_RADIUS" = "5000"
    "KAKAO_ROUTE_CONCURRENCY" = "8"
    "PLANNING_DATA_URL" = "http://planning-data:8004"
}

foreach ($key in $settings.Keys) {
    $value = $settings[$key]
    $pattern = "^" + [regex]::Escape($key) + "="
    if ($lines -match $pattern) {
        $lines = $lines | ForEach-Object {
            if ($_ -match $pattern) { "$key=$value" } else { $_ }
        }
    } else {
        $lines += "$key=$value"
    }
}

Set-Content -Path $envPath -Value $lines -Encoding UTF8
Write-Host "완료: .env에 Kakao 설정을 저장했습니다."
Write-Host "주의: .env를 Git에 커밋하지 마세요."
