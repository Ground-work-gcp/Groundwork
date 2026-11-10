<#
.SYNOPSIS
  PRINTS the commands that remove Groundwork STAGING resources. It never runs them.
  Review, then copy the ones you want. Deletions are permanent (the bucket holds evidence).
#>
param([Parameter(Mandatory = $true)][string]$ProjectId, [string]$Region = "us-central1")
@"
# Stop paying for compute first (safe, reversible by redeploying):
gcloud run services delete groundwork-staging --region $Region --project $ProjectId
gcloud run jobs delete groundwork-migrate-staging --region $Region --project $ProjectId
gcloud run jobs delete groundwork-expire-actions-staging --region $Region --project $ProjectId
gcloud run jobs delete groundwork-verify-audit-staging --region $Region --project $ProjectId
gcloud scheduler jobs delete groundwork-expire-actions-staging --location $Region --project $ProjectId
gcloud scheduler jobs delete groundwork-verify-audit-staging --location $Region --project $ProjectId

# Cloud SQL is the largest fixed cost. Stop it without deleting (keeps data, stops CPU billing):
gcloud sql instances patch groundwork-pg --activation-policy NEVER --project $ProjectId
# ...or delete permanently (backups are deleted with it):
# gcloud sql instances delete groundwork-pg --project $ProjectId

# PERMANENT: staging evidence objects
# gcloud storage rm --recursive gs://$ProjectId-groundwork-evidence-staging
# Old images (keeps the repo):
# gcloud artifacts docker images list $Region-docker.pkg.dev/$ProjectId/groundwork/groundwork
"@
