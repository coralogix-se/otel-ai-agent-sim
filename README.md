# OpenTelemetry AI Agent Simulator

This repository runs a **synthetic multi-agent workload** that emits OpenTelemetry traces, logs, and Prometheus-style metrics resembling Claude Code, Gemini CLI, Codex, Cursor, GitHub Copilot CLI, and related tooling. Telemetry is shaped so it can be exercised in Coralogix (and similar backends) for demos, dashboards, and pipeline testing.

The main entrypoint is `app.py` (container image built from `Dockerfile`).

## Prerequisites

- Python **3.12+** (see `requirements.txt`)
- For Kubernetes: `kubectl` and a cluster that can pull your container image (the manifests reference an example ECR image; override with your registry)

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Configure behavior with environment variables (see comments in `k8s/codeagentsim/sim-deployment.yaml` and `k8s/deployment.yaml` for common knobs such as `TRACE_INTERVAL_SEC`, `SIM_FORCE_AGENT`, and Coralogix-related settings).

To inspect OTLP locally without sending to Coralogix, run a debug collector as documented in `config/otel-collector-local-dev.yaml`, then point the sim at `localhost:4317` (for example `OTLP_ENDPOINT=localhost:4317` with `OTLP_INSECURE=true`).

## Build the container image

```bash
docker build --platform linux/amd64 -t <your-registry>/otel-ai-agent-sim:latest .
```

`Dockerfile` targets `linux/amd64` for typical EKS node pools.

## Deploy on Kubernetes

There are **two** supported layouts. Pick one.

### A. `codeagentsim` namespace — in-cluster collector with **multiple Coralogix exporters**

Manifests live under `k8s/codeagentsim/`. Here the simulator sends OTLP only to a **sidecar-style OpenTelemetry Collector** (`otel-collector-codeagentsim`). The collector is configured to **fan out** the same traces and logs to **three** OTLP/Coralogix endpoints, and to send scraped Prometheus metrics via **three** `prometheusremotewrite` exporters:

| Exporter role | Region / ingress | Credential (Kubernetes Secret key) |
|---------------|------------------|--------------------------------------|
| Primary US2 | `ingress.us2.coralogix.com` | `private_key_us2` |
| EU1 | `ingress.eu1.coralogix.com` | `private_key_eu1` |
| Second US2 tenant (same ingress as US2, **different** Send-Your-Data key) | `ingress.us2.coralogix.com` | `private_key_onlineboutique_dev` |

So you have **multiple Coralogix exporters** in one collector config: two keys hit the same US2 ingress for **different** Coralogix instances, plus a separate EU1 key. The sim pod itself does **not** hold Coralogix API keys; only the collector Deployment reads `Secret` `coralogix-multi-export`.

**Apply order**

1. `kubectl apply -f k8s/codeagentsim/namespace.yaml`
2. Create the Secret `coralogix-multi-export` in namespace `codeagentsim` with keys `private_key_us2`, `private_key_eu1`, and `private_key_onlineboutique_dev`. Use `k8s/codeagentsim/secret-multi-export.example.yaml` as a template, or the `kubectl create secret generic` commands in `k8s/codeagentsim/secrets.env.example`.
3. `kubectl apply -f k8s/codeagentsim/otel-collector-configmap.yaml`
4. `kubectl apply -f k8s/codeagentsim/otel-collector-deployment.yaml`
5. Edit `k8s/codeagentsim/sim-deployment.yaml` if needed (image name, env), then `kubectl apply -f k8s/codeagentsim/sim-deployment.yaml`

The sim is configured to use `OTLP_ENDPOINT=otel-collector-codeagentsim:24317` and `OTLP_INSECURE=true` (in-cluster gRPC without TLS to the collector). The collector listens on **24317/24318** so it does not collide with another collector using `4317/4318` in the same cluster.

### B. `ai-agent-sim` namespace — **direct** OTLP to Coralogix (single key)

`k8s/deployment.yaml` defines namespace `ai-agent-sim` and a Deployment where the sim talks **directly** to Coralogix OTLP ingress (`ingress.us2.coralogix.com:443`) using a single Send-Your-Data key from Secret `coralogix-otel-credentials` (see `k8s/secret-coralogix-otel.example.yaml`).

`scripts/redeploy.sh` is oriented toward this path: it applies `k8s/deployment.yaml` by default and restarts the `otel-ai-agent-sim` Deployment in namespace `ai-agent-sim`. Override `K8S_NAMESPACE`, `ECR_REGISTRY`, and related variables as needed.

### Optional: Coralogix Helm `otel-integration` values

If a cluster-wide Coralogix OpenTelemetry integration scrapes the sim, these value fragments adjust application/subsystem resolution and optional vCenter metrics:

- `k8s/coralogix-exporter-appname-patch.yaml` — prefer `cx.application.name` for Coralogix **application** mapping.
- `k8s/coralogix-vcenter-receiver-patch.yaml` — example **vCenter receiver** snippet (replace endpoint and credentials for your environment).

## Security and secrets

- Do **not** commit real Send-Your-Data keys. Use the `*.example.yaml` files and `secrets.env.example` as templates; keep working copies gitignored (see `.gitignore`).
- For the multi-export collector, all three keys must be present in `coralogix-multi-export` or the collector will fail to expand `${env:...}` at runtime.

## Repository layout (short)

| Path | Purpose |
|------|---------|
| `app.py` | Simulator entrypoint and wiring |
| `sim/` | Per-agent emitters and shared helpers |
| `prompb/`, `prometheus_rw.py`, `otlp_metrics.py` | Metrics / remote write helpers |
| `k8s/codeagentsim/` | Namespace, collector + sim, **multi-exporter** Coralogix fan-out |
| `k8s/deployment.yaml` | Single-tenant direct-ingest example |
| `config/` | Local collector samples |
| `scripts/redeploy.sh` | Build, push (ECR), rollout for default `k8s/deployment.yaml` |
