"""Google Cloud adapters. Each is imported lazily and authenticates with Application Default
Credentials (Workload Identity on Cloud Run, ``gcloud auth application-default login``
locally). No key files or API keys are read by this code."""
