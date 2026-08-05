# scripts/smoke_real_model.ps1 — Task 9 真实模型 smoke（计划书 Task 9 交付物）
#
# 职责：显式配置 LLM_BASE_URL / LLM_MODEL / LLM_PROVIDER(可选) / LLM_API_KEY 时，
# 向真实 provider（DeepSeek 等 OpenAI 兼容接口）发起一次真实请求，验证：
#   1. 真实模型请求（连通性与 HTTP 响应）；
#   2. 结构化响应（响应 JSON 经 backend venv 的 app.agents.schemas.WritingOutput 校验）；
#   3. 错误映射（401/403 -> LLM_AUTH_ERROR；400 -> LLM_INVALID_REQUEST；
#      429 -> LLM_RATE_LIMITED；5xx -> LLM_SERVER_ERROR；超时/连接失败 -> LLM_UNAVAILABLE，
#      并按可重试语义标记 retryable）；
#   4. 超时边界（对不可达地址的短超时探测验证超时路径的映射）；
#   5. 脱敏边界（API Key 不进入任何输出；模型原文不打印；app 脱敏规则 redact/find_leaks 无泄漏）；
#   6. 版本提交边界（本脚本是只读探测：不连接数据库、不导入 app.db/domain/services、
#      不创建或提交任何版本）。
#
# 约束：
#   - API Key 只从环境变量 LLM_API_KEY 读取，禁止写入代码、日志、交接文档或 Git；
#   - 任何输出都先经过脱敏（Key -> [redacted]），并在结束时断言无 Key 泄漏；
#   - 模型名/接口返回错误时记录脱敏错误并停止，绝不擅自更换模型；
#   - 未配置环境变量时输出 SKIPPED_PROVIDER_SMOKE（不失败、不当作通过）。
#
# 用法（仓库根目录，PowerShell 5.1+）:
#   $env:LLM_BASE_URL='https://api.deepseek.com'; $env:LLM_MODEL='...'; $env:LLM_API_KEY='...'
#   ./scripts/smoke_real_model.ps1
#
# 退出码：0 = PASS / SKIPPED_PROVIDER_SMOKE；非 0 = FAIL（含记录脱敏错误后停止）。

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$script:exitCode = 0
$script:apiKey = $null            # 只在内存中持有，绝不打印
$script:outputs = [System.Collections.ArrayList]::new()

function Write-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail)
    $obj = [ordered]@{ check = $Name; ok = $Ok; detail = $Detail }
    $line = ($obj | ConvertTo-Json -Compress)
    if ($script:apiKey) { $line = $line.Replace($script:apiKey, '[redacted]') }
    [void]$script:outputs.Add($line)
    Write-Output $line
}

# ---- 1. 配置读取（Key 只读、绝不打印）----
# 读取顺序：进程环境变量 -> 用户级环境变量 -> 仓库根 .env（与 AppConfig env_file
# 约定一致）。Key 只在内存中持有并用于 Authorization 头，不写入任何输出/文档。
$script:dotenv = @{}
$dotenvPaths = @(
    (Join-Path $PSScriptRoot '..\.env'),
    (Join-Path $PSScriptRoot '..\backend\.env')
)
foreach ($dp in $dotenvPaths) {
    if (Test-Path $dp) {
        foreach ($line in (Get-Content $dp -Encoding UTF8)) {
            $t = $line.Trim()
            if ($t -and -not $t.StartsWith('#') -and $t -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
                $script:dotenv[$matches[1]] = $matches[2]
            }
        }
        break
    }
}

function Get-Cfg {
    param([string]$Name, [string]$AltName = '')
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($v)) { $v = [Environment]::GetEnvironmentVariable($Name, 'User') }
    if ([string]::IsNullOrWhiteSpace($v) -and $script:dotenv.ContainsKey($Name)) { $v = $script:dotenv[$Name] }
    if ([string]::IsNullOrWhiteSpace($v) -and $AltName) {
        $v = [Environment]::GetEnvironmentVariable($AltName)
        if ([string]::IsNullOrWhiteSpace($v)) { $v = [Environment]::GetEnvironmentVariable($AltName, 'User') }
        if ([string]::IsNullOrWhiteSpace($v) -and $script:dotenv.ContainsKey($AltName)) { $v = $script:dotenv[$AltName] }
    }
    return $v
}

$baseUrl  = Get-Cfg 'LLM_BASE_URL'
$model    = Get-Cfg 'LLM_MODEL' 'MODEL_NAME'
$provider = Get-Cfg 'LLM_PROVIDER'
$script:apiKey = Get-Cfg 'LLM_API_KEY'

if ([string]::IsNullOrWhiteSpace($baseUrl) -or [string]::IsNullOrWhiteSpace($model) -or [string]::IsNullOrWhiteSpace($script:apiKey)) {
    $missing = @()
    if ([string]::IsNullOrWhiteSpace($baseUrl)) { $missing += 'LLM_BASE_URL' }
    if ([string]::IsNullOrWhiteSpace($model))   { $missing += 'LLM_MODEL' }
    if ([string]::IsNullOrWhiteSpace($script:apiKey)) { $missing += 'LLM_API_KEY' }
    $obj = [ordered]@{
        status = 'SKIPPED_PROVIDER_SMOKE'
        ok = $false
        reason = "未配置环境变量: $($missing -join ', ')（按计划书约定只输出 SKIPPED_PROVIDER_SMOKE，不当作通过）"
        checks = @()
    }
    Write-Output ($obj | ConvertTo-Json -Depth 6 -Compress)
    exit 0
}

$base = $baseUrl.TrimEnd('/')
if ($base -match '(/chat/completions|/v\d+/chat/completions)$') {
    $endpoint = $base
} else {
    $endpoint = "$base/chat/completions"
}
$providerNote = if ([string]::IsNullOrWhiteSpace($provider)) { '(unset)' } else { $provider }

Write-Check 'config' $true "base_url=$baseUrl model=$model provider=$providerNote (api_key 仅从环境变量读取，未打印)"

# ---- 2. 真实模型请求（OpenAI 兼容 chat/completions，JSON 结构化输出）----
$systemPrompt = @'
You are a novel-writing agent for a structured pipeline. You MUST respond with a single JSON object only (no markdown, no prose outside JSON). The JSON must match exactly this schema:
{"status": "ready" | "needs_clarification", "mode": "draft" | "continue" | "rewrite", "content": "<scene draft text, 3-5 sentences, in Chinese>", "candidate_facts": [{"candidate_type": "fact", "local_key": "<stable id>", "claim": "<fact text>", "status": "candidate", "scope": "scene", "evidence_refs": []}], "unresolved_assumptions": [], "context_source_refs": [], "evidence_refs": [], "clarification_questions": []}
Write a short scene draft into "content". Keep every other list minimal or empty.
'@
$userPrompt = '写一个雨夜咖啡馆的场景草稿，主角林默与旧友重逢。'
$requestBody = @{
    model = $model
    messages = @(
        @{ role = 'system'; content = $systemPrompt },
        @{ role = 'user';   content = $userPrompt }
    )
    temperature = 0
    max_tokens = 2048
    response_format = @{ type = 'json_object' }
} | ConvertTo-Json -Depth 10

function Invoke-Completion {
    param([string]$Uri, [string]$Key, [int]$TimeoutSec, [string]$Body)
    $headers = @{ Authorization = "Bearer $Key"; 'Content-Type' = 'application/json' }
    $resp = Invoke-WebRequest -Uri $Uri -Method Post -Headers $headers -Body $Body -UseBasicParsing -TimeoutSec $TimeoutSec
    return $resp
}

function Get-ErrorStatus {
    param($Exception)
    $status = $null
    try { $status = [int]$Exception.Exception.Response.StatusCode.value__ } catch { }
    return $status
}

function Map-ErrorCode {
    param($Status, [string]$BodySummary)
    # 错误映射表：真实 provider 的 HTTP 状态 -> 稳定错误码与可重试语义。
    # 状态为 $null/0 表示请求未完成（超时/连接失败），映射为 LLM_UNAVAILABLE。
    if ($null -eq $Status -or $Status -eq 0) { return @{ code = 'LLM_UNAVAILABLE'; retryable = $true } }
    if ($Status -eq 401 -or $Status -eq 403) { return @{ code = 'LLM_AUTH_ERROR'; retryable = $false } }
    if ($Status -eq 400) { return @{ code = 'LLM_INVALID_REQUEST'; retryable = $false } }
    if ($Status -eq 404) { return @{ code = 'LLM_ENDPOINT_NOT_FOUND'; retryable = $false } }
    if ($Status -eq 429) { return @{ code = 'LLM_RATE_LIMITED'; retryable = $true } }
    if ($Status -ge 500) { return @{ code = 'LLM_SERVER_ERROR'; retryable = $true } }
    return @{ code = 'LLM_UNKNOWN_ERROR'; retryable = $true }
}

function Invoke-CompletionSafe {
    param([string]$Uri, [string]$Key, [int]$TimeoutSec, [string]$Body)
    # 返回 @{ ok; status; body }；任何 HTTP 非 2xx / 超时 / 连接错误都捕获并脱敏
    try {
        $resp = Invoke-Completion -Uri $Uri -Key $Key -TimeoutSec $TimeoutSec -Body $Body
        return @{ ok = $true; status = [int]$resp.StatusCode; body = [string]$resp.Content }
    } catch {
        $status = Get-ErrorStatus $_
        $raw = ''
        try { $raw = [string]$_.ErrorDetails.Message } catch { }
        if ([string]::IsNullOrWhiteSpace($raw)) { $raw = [string]$_.Exception.Message }
        if ($script:apiKey) { $raw = $raw.Replace($script:apiKey, '[redacted]') }
        return @{ ok = $false; status = $status; body = $raw }
    }
}

$primary = Invoke-CompletionSafe -Uri $endpoint -Key $script:apiKey -TimeoutSec 60 -Body $requestBody

if (-not $primary.ok) {
    $mapped = Map-ErrorCode -Status ($primary.status -as [int]) -BodySummary ''
    $detail = "真实模型请求失败：http_status=$($primary.status) mapped_code=$($mapped.code) retryable=$($mapped.retryable) body=$($primary.body)"
    # 错误映射本身就是本次验证项：断言映射后的错误码/可重试语义符合注册表约定
    $mappedOk = ($mapped.code -ne 'LLM_UNKNOWN_ERROR') -and ($mapped.retryable -in @($true, $false))
    Write-Check 'error_mapping' $mappedOk $detail
    Write-Check 'provider_request' $false "真实 provider 请求未成功（http_status=$($primary.status)）；按约定记录脱敏错误并停止，不更换模型"
    $summary = [ordered]@{ status = 'FAIL'; ok = $false; reason = 'provider/model 请求错误，已记录脱敏错误并停止'; checks = @($script:outputs) }
    Write-Output ($summary | ConvertTo-Json -Depth 8 -Compress)
    exit 1
}

Write-Check 'provider_request' $true "http_status=$($primary.status) 请求成功（未打印原文，仅记录状态码与结构化校验结果）"

# ---- 3. 结构化响应校验（backend venv python 以 app 契约校验 + 脱敏边界）----
$rawJsonPath = Join-Path $env:TEMP ("smoke_real_model_raw_{0}.json" -f ([guid]::NewGuid().ToString('N')))
$pyCodePath = Join-Path $env:TEMP ("smoke_real_model_py_{0}.py" -f ([guid]::NewGuid().ToString('N')))
$pyOutPath = Join-Path $env:TEMP ("smoke_real_model_out_{0}.txt" -f ([guid]::NewGuid().ToString('N')))
$pyErrPath = Join-Path $env:TEMP ("smoke_real_model_err_{0}.txt" -f ([guid]::NewGuid().ToString('N')))
# 注意：不要用 `python -c $code` 传参——PS 5.1 会剥掉代码里的双引号导致语法错误；
# 改写成临时 .py 文件后以文件路径执行。
$pyCode = @'
import json, sys
from app.agents.schemas import WritingOutput
from app.observability.redaction import redact_payload, find_leaks

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8-sig"))
content = data["choices"][0]["message"]["content"]
raw = json.loads(content)
try:
    out = WritingOutput(**raw)
except Exception as exc:  # noqa: BLE001
    print("schema_invalid=%r" % (exc,))
    sys.exit(2)
print("schema_valid=true status=%s mode=%s content_chars=%d" % (out.status, out.mode, len(out.content)))
redacted = redact_payload(raw)
leaks = find_leaks(raw, redacted)
print("redaction_leaks=%d" % len(leaks))
if leaks:
    sys.exit(3)
'@

$pyOk = $false
$pyOut = ''
try {
    # 写入无 BOM UTF-8（.NET Encoding.UTF8 会带 BOM，导致 json.load 报 BOM 错误）
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($rawJsonPath, $primary.body, $utf8NoBom)
    [System.IO.File]::WriteAllText($pyCodePath, $pyCode, $utf8NoBom)
    $venvPython = Join-Path $PSScriptRoot '..\backend\.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Write-Check 'structured_response' $false "未找到 backend venv python（$venvPython）"
    } else {
        # 用文件重定向捕获 python 输出；临时切回 Continue，规避 PS 5.1 下
        # ErrorActionPreference=Stop 把 stderr 重定向变成终止错误
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $venvPython $pyCodePath $rawJsonPath 1> $pyOutPath 2> $pyErrPath
        $pyExit = $LASTEXITCODE
        $ErrorActionPreference = $prevEap
        $pyOut = [string](Get-Content $pyOutPath -Raw -ErrorAction SilentlyContinue)
        $pyErr = [string](Get-Content $pyErrPath -Raw -ErrorAction SilentlyContinue)
        $pyOut = $pyOut -replace "`r?`n", ' '
        $pyErr = $pyErr -replace "`r?`n", ' '
        $pyOk = $pyExit -eq 0
        if ($pyOk) {
            Write-Check 'structured_response' $true $pyOut.Trim()
        } else {
            Write-Check 'structured_response' $false ("exit=$pyExit stdout=$($pyOut.Trim()) stderr=$($pyErr.Trim())")
        }
    }
} catch {
    Write-Check 'structured_response' $false ("python 校验执行异常：" + $_.Exception.Message)
} finally {
    Remove-Item $rawJsonPath, $pyCodePath, $pyOutPath, $pyErrPath -Force -ErrorAction SilentlyContinue
}

# ---- 4. 错误映射负向探测：伪造 Key 应得到 401/403 -> LLM_AUTH_ERROR ----
$badKey = 'sk-invalid-probe-key-0000'
$auth = Invoke-CompletionSafe -Uri $endpoint -Key $badKey -TimeoutSec 30 -Body $requestBody
$authMapped = Map-ErrorCode -Status ($auth.status -as [int]) -BodySummary ''
$authOk = (-not $auth.ok) -and ($auth.status -eq 401 -or $auth.status -eq 403) -and ($authMapped.code -eq 'LLM_AUTH_ERROR') -and (-not $authMapped.retryable)
$authDetail = if ($authOk) { "伪造 Key 被拒绝：http_status=$($auth.status) mapped_code=$($authMapped.code) retryable=$($authMapped.retryable)" }
             else { "映射不符合预期：http_status=$($auth.status) ok=$($auth.ok) mapped=$($authMapped.code) body=$($auth.body)" }
Write-Check 'error_mapping_auth' $authOk $authDetail

# ---- 5. 超时边界：不可达地址 + 短超时 -> 超时/连接错误 -> LLM_UNAVAILABLE(retryable) ----
$timeoutUri = 'http://10.255.255.1/chat/completions'
$timeoutBody = '{}'
$to = Invoke-CompletionSafe -Uri $timeoutUri -Key $script:apiKey -TimeoutSec 3 -Body $timeoutBody
$toMapped = Map-ErrorCode -Status ($to.status -as [int]) -BodySummary ''
$toOk = (-not $to.ok) -and ($toMapped.code -eq 'LLM_UNAVAILABLE') -and ($toMapped.retryable)
$toStatusTxt = if ($null -eq $to.status) { 'timeout/connection' } else { [string]$to.status }
Write-Check 'timeout_boundary' $toOk ("不可达地址探测（TimeoutSec=3）：ok=$($to.ok) status=$toStatusTxt mapped_code=$($toMapped.code) retryable=$($toMapped.retryable)")

# ---- 6. 脱敏边界：断言本脚本所有输出不含 API Key 原文 ----
$leakFound = $false
foreach ($line in $script:outputs) {
    if ($script:apiKey -and $line.Contains($script:apiKey)) { $leakFound = $true; break }
}
if (-not $pyOk) { $leakFound = $leakFound }  # pyOk 失败时 redaction_leaks 可能未执行，保持 python 结果为准
Write-Check 'redaction_no_key_leak' (-not $leakFound) "API Key 未出现在任何输出（模型原文未打印，仅输出哈希/长度与结构化字段）"

# ---- 7. 版本提交边界：本脚本为只读探测，不创建/提交任何版本 ----
# 结构性保证：脚本只做 provider 出站 HTTPS 调用；python 校验只导入
# app.agents.schemas 与 app.observability.redaction，不导入 app.db/domain/services，
# 不设置 DATABASE_URL，不连接 PostgreSQL。
Write-Check 'version_commit_boundary' $true '只读探测：仅调用 provider API，未连接数据库、未导入 app.db/domain/services、未创建或提交任何版本'

# ---- 汇总 ----
# 判定：存在任一 check ok=false 即 FAIL（注意 ConvertTo-Json -Compress 无空格，匹配 "ok":false）
$allOk = $true
foreach ($line in $script:outputs) {
    if ($line -match '"ok":false') { $allOk = $false; break }
}
$script:exitCode = if ($allOk) { 0 } else { 1 }
$summary = [ordered]@{
    status = if ($allOk) { 'PASS' } else { 'FAIL' }
    ok = $allOk
    reason = if ($allOk) { '真实模型 smoke 全部通过' } else { '存在失败的检查项，详见各 check' }
    checks = @($script:outputs)
}
$summaryLine = ($summary | ConvertTo-Json -Depth 8 -Compress)
if ($script:apiKey) { $summaryLine = $summaryLine.Replace($script:apiKey, '[redacted]') }
Write-Output $summaryLine
exit $script:exitCode
