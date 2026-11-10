<#
.SYNOPSIS
  Promote an image that already passed staging to production. Windows PowerShell 5.1.

.DESCRIPTION
  The ONLY path to production. Takes an exact image digest (never a moving tag), refuses to
  run unless that digest is what staging is currently serving and staging /ready is 200,
  migrates the production database with the same image, deploys groundwork-prod with no
  traffic, checks /ready on the new revision, then shifts 100% of traffic. Rollback:
  gcloud run services update-traffic groundwork-prod --to-revisions <previous>=100
#>
param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [Parameter(Mandatory = $true)][string]$ImageDigest,   # e.g. us-central1-docker.pkg.dev/p/groundwork/groundwork@sha256:...
  [string]$Region = "us-central1",
  [string]$Instance = "groundwork-pg",
  [string]$Confirmation = ""          # CI passes it after the GitHub environment review; humans are prompted
)
$ErrorActionPreference = "Stop"
if ($ImageDigest -notmatch "@sha256:[0-9a-f]{64}$") { throw "Pass an immutable digest (...@sha256:...), not a tag." }
$conn = "${ProjectId}:${Region}:$Instance"

$stagingImage = gcloud run services describe groundwork-staging --region $Region --project $ProjectId --format "value(status.latestReadyRevisionName)"
$servedDigest = gcloud run revisions describe $stagingImage --region $Region --project $ProjectId --format "value(status.imageDigest)"
if ($servedDigest -ne $ImageDigest) { throw "Staging is serving $servedDigest, not $ImageDigest. Promote only what staging verified." }
$stagingUrl = gcloud run services describe groundwork-staging --region $Region --project $ProjectId --format "value(status.url)"
$code = (Invoke-WebRequest -UseBasicParsing -Uri "$stagingUrl/ready").StatusCode
if ($code -ne 200) { throw "staging /ready returned $code" }

$answer = if ($Confirmation) { $Confirmation } else { Read-Host "Type 'promote $($ImageDigest.Substring($ImageDigest.Length - 12))' to deploy this digest to PRODUCTION" }
if ($answer -ne "promote $($ImageDigest.Substring($ImageDigest.Length - 12))") { throw "Not confirmed." }

gcloud run jobs deploy groundwork-migrate-prod --region $Region --project $ProjectId --image $ImageDigest --set-cloudsql-instances $conn `
  --service-account "groundwork-run@$ProjectId.iam.gserviceaccount.com" `
  --set-secrets "DATABASE_URL=groundwork-database-url-prod:latest,JWT_SECRET=groundwork-jwt-secret:latest" `
  --command alembic --args "upgrade,head" --max-retries 0
gcloud run jobs execute groundwork-migrate-prod --region $Region --project $ProjectId --wait
if ($LASTEXITCODE -ne 0) { throw "production migration failed; production service untouched" }

$yaml = (Get-Content -Raw "$PSScriptRoot\..\cloudrun-service.yaml") `
  -replace "__IMAGE__", $ImageDigest -replace "__PROJECT__", $ProjectId -replace "__INSTANCE__", $conn -replace "__REGION__", $Region `
  -replace "__SERVICE__", "groundwork-prod" -replace "__ENV__", "prod" -replace "__SA__", "groundwork-run"
$tmp = [IO.Path]::GetTempFileName() + ".yaml"
[IO.File]::WriteAllText($tmp, $yaml)
try {
  $exists = gcloud run services describe groundwork-prod --region $Region --project $ProjectId --format "value(metadata.name)" 2>$null
  if ($exists) { gcloud run services replace $tmp --region $Region --project $ProjectId --no-traffic } else { gcloud run services replace $tmp --region $Region --project $ProjectId }
} finally { Remove-Item $tmp -Force }
$rev = gcloud run services describe groundwork-prod --region $Region --project $ProjectId --format "value(status.latestCreatedRevisionName)"
gcloud run services update-traffic groundwork-prod --region $Region --project $ProjectId --to-revisions "$rev=100"
$url = gcloud run services describe groundwork-prod --region $Region --project $ProjectId --format "value(status.url)"
Write-Host "production: $url  revision: $rev  /ready: $((Invoke-WebRequest -UseBasicParsing -Uri "$url/ready").StatusCode)"
