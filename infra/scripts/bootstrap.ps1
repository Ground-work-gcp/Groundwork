<#
.SYNOPSIS
  Provision Groundwork's Google Cloud staging environment. Windows PowerShell 5.1.

.DESCRIPTION
  Idempotent: every resource is looked up first and only created if missing. Nothing is
  deleted. Without -CreateBillableResources the script only INSPECTS and prints what it would
  create. Requires the gcloud CLI, signed in (gcloud auth login) as a project Owner/Editor.

  Creates (staging): APIs, Artifact Registry repo, private evidence bucket, Cloud SQL
  PostgreSQL 16 (db-f1-micro, no public IP authorised networks, backups on), runtime +
  scheduler service accounts with least-privilege roles, Secret Manager secrets (values are
  generated or prompted locally - never echoed), a Document AI OCR processor, the scheduled
  Cloud Run Jobs + Cloud Scheduler triggers, and a budget-friendly log-based alert policy.

.EXAMPLE
  .\infra\scripts\bootstrap.ps1 -ProjectId my-proj                       # inspect only
  .\infra\scripts\bootstrap.ps1 -ProjectId my-proj -CreateBillableResources
#>
param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [string]$Region = "us-central1",
  [string]$Env = "staging",
  [string]$SqlTier = "db-f1-micro",
  [string]$GitHubRepo = "",            # owner/name, enables Workload Identity Federation for GitHub Actions
  [string]$AlertEmail = "",
  [switch]$CreateBillableResources
)
$ErrorActionPreference = "Stop"
$Do = [bool]$CreateBillableResources

function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Exists([scriptblock]$probe) { try { & $probe 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) } catch { return $false } }
function Make($what, [scriptblock]$create) {
  if ($Do) { Write-Host "  creating $what"; & $create; if ($LASTEXITCODE -ne 0) { throw "failed: $what" } }
  else { Write-Host "  WOULD CREATE $what (re-run with -CreateBillableResources)" -ForegroundColor Yellow }
}
function NewSecret([int]$bytes = 48) {
  $b = New-Object byte[] $bytes; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  return [Convert]::ToBase64String($b).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
function PutSecret($name, $value) {
  if (Exists { gcloud secrets describe $name --project $ProjectId }) { Write-Host "  secret $name exists (version kept)"; return }
  Make "secret $name" {
    $tmp = [IO.Path]::GetTempFileName()
    try { [IO.File]::WriteAllText($tmp, $value); gcloud secrets create $name --project $ProjectId --replication-policy automatic --data-file $tmp | Out-Null }
    finally { Remove-Item $tmp -Force }
  }
}

Step "Identity and project"
$account = gcloud config get-value account 2>$null
if (-not $account) { throw "Not signed in. Run: gcloud auth login" }
Write-Host "  account: $account"
gcloud projects describe $ProjectId --format "value(projectId,projectNumber,lifecycleState)"
$ProjectNumber = gcloud projects describe $ProjectId --format "value(projectNumber)"
$billing = gcloud billing projects describe $ProjectId --format "value(billingEnabled)" 2>$null
Write-Host "  billing enabled: $billing"
if ($billing -ne "True") { throw "Billing is not enabled on $ProjectId. Link a billing account in the console first (this script never changes billing)." }
gcloud config set project $ProjectId | Out-Null

Step "APIs (only what Groundwork uses)"
$apis = "run.googleapis.com", "sqladmin.googleapis.com", "storage.googleapis.com", "documentai.googleapis.com", "aiplatform.googleapis.com",
        "dlp.googleapis.com", "secretmanager.googleapis.com", "artifactregistry.googleapis.com", "cloudbuild.googleapis.com",
        "cloudscheduler.googleapis.com", "iamcredentials.googleapis.com", "monitoring.googleapis.com", "logging.googleapis.com", "sts.googleapis.com"
$enabled = gcloud services list --enabled --format "value(config.name)"
$missing = $apis | Where-Object { $enabled -notcontains $_ }
if ($missing) { Make "APIs: $($missing -join ', ')" { gcloud services enable @missing } } else { Write-Host "  all enabled" }

Step "Artifact Registry"
if (Exists { gcloud artifacts repositories describe groundwork --location $Region }) { Write-Host "  repo groundwork exists" }
else { Make "docker repo groundwork" { gcloud artifacts repositories create groundwork --repository-format docker --location $Region --description "Groundwork images" } }

Step "Evidence bucket (private)"
$Bucket = "$ProjectId-groundwork-evidence-$Env"
if (Exists { gcloud storage buckets describe "gs://$Bucket" }) { Write-Host "  bucket $Bucket exists" }
else { Make "bucket gs://$Bucket" { gcloud storage buckets create "gs://$Bucket" --location $Region --uniform-bucket-level-access --public-access-prevention --default-storage-class STANDARD } }
if ($Do) { gcloud storage buckets update "gs://$Bucket" --versioning --uniform-bucket-level-access --public-access-prevention | Out-Null }

Step "Service accounts (no keys are ever created)"
$RunSa = "groundwork-run@$ProjectId.iam.gserviceaccount.com"
$SchedSa = "groundwork-scheduler@$ProjectId.iam.gserviceaccount.com"
foreach ($sa in @(@{id = "groundwork-run"; n = "Groundwork runtime"}, @{id = "groundwork-scheduler"; n = "Groundwork scheduler"}, @{id = "groundwork-deployer"; n = "Groundwork CI deployer"})) {
  if (Exists { gcloud iam service-accounts describe "$($sa.id)@$ProjectId.iam.gserviceaccount.com" }) { Write-Host "  $($sa.id) exists" }
  else { Make "service account $($sa.id)" { gcloud iam service-accounts create $sa.id --display-name $sa.n } }
}
$runtimeRoles = "roles/cloudsql.client", "roles/secretmanager.secretAccessor", "roles/documentai.apiUser", "roles/aiplatform.user", "roles/dlp.user", "roles/logging.logWriter", "roles/monitoring.metricWriter"
if ($Do) {
  foreach ($r in $runtimeRoles) { gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$RunSa" --role $r --condition None --quiet | Out-Null }
  gcloud storage buckets add-iam-policy-binding "gs://$Bucket" --member "serviceAccount:$RunSa" --role roles/storage.objectUser | Out-Null   # bucket-scoped, not project-wide
  $deployer = "groundwork-deployer@$ProjectId.iam.gserviceaccount.com"
  foreach ($r in "roles/cloudbuild.builds.editor", "roles/run.admin", "roles/artifactregistry.writer", "roles/storage.admin", "roles/logging.viewer") {
    gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$deployer" --role $r --condition None --quiet | Out-Null }
  gcloud iam service-accounts add-iam-policy-binding $RunSa --member "serviceAccount:$deployer" --role roles/iam.serviceAccountUser | Out-Null
  $cb = "$ProjectNumber-compute@developer.gserviceaccount.com"
  foreach ($r in "roles/run.admin", "roles/artifactregistry.writer", "roles/logging.logWriter") { gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$cb" --role $r --condition None --quiet | Out-Null }
  gcloud iam service-accounts add-iam-policy-binding $RunSa --member "serviceAccount:$cb" --role roles/iam.serviceAccountUser | Out-Null
  Write-Host "  roles bound"
} else { Write-Host "  WOULD BIND runtime roles: $($runtimeRoles -join ', ') + bucket-scoped storage.objectUser" -ForegroundColor Yellow }

Step "Cloud SQL for PostgreSQL 16 ($SqlTier)"
$Instance = "groundwork-pg"
if (Exists { gcloud sql instances describe $Instance }) { Write-Host "  instance $Instance exists" }
else {
  Make "Cloud SQL $Instance ($SqlTier, ~USD 9-12/month)" {
    gcloud sql instances create $Instance --database-version POSTGRES_16 --tier $SqlTier --region $Region --edition ENTERPRISE `
      --storage-size 10 --storage-auto-increase --backup-start-time 03:00 --enable-point-in-time-recovery --retained-backups-count 7 `
      --require-ssl --database-flags cloudsql.iam_authentication=on
  }
}
$ConnName = "${ProjectId}:${Region}:$Instance"
foreach ($db in "groundwork_$Env") {
  if (Exists { gcloud sql databases describe $db --instance $Instance }) { Write-Host "  database $db exists" }
  else { Make "database $db" { gcloud sql databases create $db --instance $Instance } }
}
if (-not (Exists { gcloud secrets describe "groundwork-database-url-$Env" })) {
  $dbPass = NewSecret 24
  if (Exists { gcloud sql users list --instance $Instance --format "value(name)" | Select-String -SimpleMatch "groundwork" }) { Write-Host "  sql user groundwork exists; set its password manually and create the secret" }
  else { Make "sql user groundwork" { gcloud sql users create groundwork --instance $Instance --password $dbPass | Out-Null } }
  PutSecret "groundwork-database-url-$Env" "postgresql+psycopg://groundwork:$dbPass@/groundwork_$Env?host=/cloudsql/$ConnName"
}

Step "Secrets"
PutSecret "groundwork-jwt-secret" (NewSecret 48)
PutSecret "groundwork-webhook-secret" (NewSecret 32)

Step "Document AI OCR processor (us)"
$token = $(gcloud auth print-access-token)
$hdr = @{ Authorization = "Bearer $token"; "x-goog-user-project" = $ProjectId }
$procs = Invoke-RestMethod -Headers $hdr -Uri "https://us-documentai.googleapis.com/v1/projects/$ProjectId/locations/us/processors" -Method Get
$proc = $procs.processors | Where-Object { $_.displayName -eq "groundwork-ocr" } | Select-Object -First 1
if ($proc) { Write-Host "  processor $($proc.name) ($($proc.state))" }
elseif ($Do) {
  $proc = Invoke-RestMethod -Headers $hdr -ContentType "application/json" -Method Post -Uri "https://us-documentai.googleapis.com/v1/projects/$ProjectId/locations/us/processors" -Body '{"type":"OCR_PROCESSOR","displayName":"groundwork-ocr"}'
  Write-Host "  created $($proc.name)"
} else { Write-Host "  WOULD CREATE OCR_PROCESSOR groundwork-ocr" -ForegroundColor Yellow }
if ($proc) { PutSecret "groundwork-documentai-processor" (($proc.name -split "/")[-1]) }

Step "Workload Identity Federation for GitHub Actions"
if ($GitHubRepo) {
  if (Exists { gcloud iam workload-identity-pools describe github --location global }) { Write-Host "  pool github exists" }
  else { Make "WIF pool github" { gcloud iam workload-identity-pools create github --location global --display-name "GitHub Actions" } }
  if (Exists { gcloud iam workload-identity-pools providers describe github-oidc --location global --workload-identity-pool github }) { Write-Host "  provider exists" }
  else {
    Make "WIF provider github-oidc (restricted to $GitHubRepo)" {
      gcloud iam workload-identity-pools providers create-oidc github-oidc --location global --workload-identity-pool github `
        --issuer-uri "https://token.actions.githubusercontent.com" `
        --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" `
        --attribute-condition "assertion.repository=='$GitHubRepo'"
    }
  }
  if ($Do) {
    gcloud iam service-accounts add-iam-policy-binding "groundwork-deployer@$ProjectId.iam.gserviceaccount.com" --role roles/iam.workloadIdentityUser `
      --member "principalSet://iam.googleapis.com/projects/$ProjectNumber/locations/global/workloadIdentityPools/github/attribute.repository/$GitHubRepo" | Out-Null
  }
  Write-Host "  GitHub secrets to set: GCP_PROJECT_ID=$ProjectId  GCP_WIF_PROVIDER=projects/$ProjectNumber/locations/global/workloadIdentityPools/github/providers/github-oidc  GCP_DEPLOYER_SA=groundwork-deployer@$ProjectId.iam.gserviceaccount.com  GCP_SQL_INSTANCE=$ConnName"
} else { Write-Host "  skipped (pass -GitHubRepo owner/name)" }

Step "Scheduled jobs (Cloud Run Jobs + Cloud Scheduler)"
$image = "$Region-docker.pkg.dev/$ProjectId/groundwork/groundwork:latest"
Write-Host "  After the first image is pushed, create the jobs with:"
foreach ($job in @(@{n = "expire-actions"; c = "0 * * * *"}, @{n = "verify-audit"; c = "30 2 * * *"})) {
  Write-Host "    gcloud run jobs deploy groundwork-$($job.n)-$Env --region $Region --image <IMAGE> --set-cloudsql-instances $ConnName --service-account $RunSa --set-secrets DATABASE_URL=groundwork-database-url-${Env}:latest,JWT_SECRET=groundwork-jwt-secret:latest --command python --args=-m,app.jobs,$($job.n) --max-retries 1"
  Write-Host "    gcloud scheduler jobs create http groundwork-$($job.n)-$Env --location $Region --schedule `"$($job.c)`" --http-method POST --uri https://$Region-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$ProjectId/jobs/groundwork-$($job.n)-${Env}:run --oauth-service-account-email $SchedSa"
}
if ($Do) { gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$SchedSa" --role roles/run.invoker --condition None --quiet | Out-Null }

Step "Alerting"
if ($AlertEmail -and $Do) {
  $ch = gcloud beta monitoring channels create --display-name "Groundwork on-call" --type email --channel-labels "email_address=$AlertEmail" --format "value(name)"
  Write-Host "  notification channel $ch - create policies from infra/monitoring/alert-policies.md"
} else { Write-Host "  pass -AlertEmail you@example.org with -CreateBillableResources to create a notification channel" }

Step "Summary"
Write-Host "  Cloud SQL connection: $ConnName"
Write-Host "  Bucket: gs://$Bucket"
Write-Host "  Runtime SA: $RunSa"
if (-not $Do) { Write-Host "`nInspection only. Nothing was created." -ForegroundColor Yellow }
