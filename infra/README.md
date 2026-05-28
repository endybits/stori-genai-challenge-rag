# Infrastructure (CDK)

Production-shape AWS deployment of the Stori RAG agent. **Synth-only**: the
code produces valid CloudFormation but has not been deployed against a live
account. It exists to communicate the production architecture, not as
ready-to-run IaC.

## Architecture

```
                       Internet
                          │
                          ▼
                  ┌──────────────┐
                  │     ALB      │   (HTTP)
                  └──────┬───────┘
                         │
                  ┌──────▼───────────────┐
                  │  Fargate: rag        │   /chat, /health
                  │  (app container)     │   1–4 tasks, CPU autoscale
                  └──┬─────────┬─────────┘
                     │         │
                     ▼         ▼
              ┌──────────┐  ┌──────────────┐
              │ S3 Index │  │  DynamoDB    │
              │  bucket  │  │  checkpointer│
              └─────▲────┘  └──────────────┘
                    │
              ┌─────┴────────────┐
              │ Fargate: ingest  │   one-off task (RunTask)
              └─────────┬────────┘
                        │
                        ▼
                  ┌──────────┐
                  │ S3 Corpus│
                  │  bucket  │
                  └──────────┘
```

## Stacks

| Stack | Bounded context | Resources |
|---|---|---|
| `stori-rag-network` | The outside | VPC (2 AZs, 1 NAT), ALB, security groups |
| `stori-rag-data` | Persistent state | S3 corpus + index buckets, DynamoDB checkpointer |
| `stori-rag-compute` | The running app | ECR repo, Fargate service, ALB target, autoscaling |
| `stori-rag-ingest` | The ingestion job | Fargate task definition for one-off RunTask |

Separated by bounded context rather than service type — same approach as the
hexagonal/DDD structure in the application code.

## Code↔Infrastructure contract

The container image expects, in production:

| Env var | Source | Used by |
|---|---|---|
| `MODE` | task definition (`serve` or `ingest`) | entrypoint dispatcher |
| `GOOGLE_API_KEY` | **NOT in this stack** — see "Out of scope" | LLM + embeddings |
| `EMBEDDING_MODEL` | task env (hardcoded to `gemini-embedding-2`) | ingestion + agent |
| `INDEX_BUCKET` | task env | image sync at startup (serve) / upload after build (ingest) |
| `CORPUS_BUCKET` | task env | ingestion only |
| `CHECKPOINTER_TABLE` | task env | LangGraph state persistence (replaces local SQLite) |
| `AWS_REGION` | task env | AWS SDK |

The container code does not yet implement S3-sync-at-startup or
DynamoDB-backed checkpointer — those would be part of the cloud migration.
The dev image uses local SQLite and a Chroma directory on a Docker volume.

## How to synth

```bash
cd infra/
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cdk synth
```

No AWS credentials required for synth.

## Deliberately out of scope

These are noted to make the gap between this synth-only deliverable and a
real production deployment explicit:

- **Secrets management.** `GOOGLE_API_KEY` is declared as a required env var
  but is not provisioned by the stack. For a real deployment, it would live
  in Secrets Manager with `ecs.Secret.from_secrets_manager` binding on both
  task definitions. Left out here so the stack stays a shape demo and not
  half-implemented operational IaC.
- **HTTPS.** ALB has an HTTP listener only. Production would add ACM cert
  and an HTTPS listener with HTTP→HTTPS redirect.
- **WAF.** No rate limiting, no managed rule sets.
- **Custom VPC topology.** Production would use isolated subnets for
  Fargate, VPC endpoints for S3 / ECR to avoid public egress, NAT per AZ.
- **Observability.** Logs are captured; no dashboards, no alarms, no X-Ray.
- **CI/CD.** No CodePipeline / GitHub Actions integration for image build
  and deploy.
- **Multi-region / DR.** Single-region only.
- **Bedrock as LLM provider.** Stays on Gemini for parity with the dev
  implementation. Migration path lives behind the LangChain abstraction
  in the application code.
- **Eval harness.** `JUDGE_MODEL` is not surfaced into the production
  environment — evaluation is a local-only concern.
