#!/usr/bin/env python3
"""Log -> register (Unity Catalog) -> serve the Attach War-Room ResponsesAgent (Tier 2.3).

This turns the in-process demo loop into a governed, versioned, autoscaling
Model Serving endpoint with Agent Evaluation + MLflow Tracing attached — the
production posture the agent README describes. The tool layer (tools.py) and the
loop (agent.py) are unchanged; responses_agent.py is the only new surface.

It does three things:
  1. mlflow.pyfunc.log_model() the ResponsesAgent, carrying the agent's own
     source files via code_paths so the model is self-contained.
  2. Register the logged model into Unity Catalog (catalog.schema.model_name).
  3. databricks.agents.deploy() it to a Model Serving endpoint, then print the URL.

------------------------------------------------------------------------------
PREREQUISITES (read before running — this provisions billable infra)
------------------------------------------------------------------------------
  * Auth: a Databricks CLI profile with workspace + UC + serving rights. Locally
    we use profile 'DEFAULT' (matches dbx.py default). In a Databricks notebook
    auth is implicit. Set DATABRICKS_CONFIG_PROFILE or rely on env.
        export DATABRICKS_CONFIG_PROFILE=DEFAULT     # local
  * Unity Catalog: the target catalog+schema must exist and you need CREATE
    MODEL on the schema. Default target is the demo schema:
        bolttech_workshop_demo.attach_war_room.attach_war_room_agent
    Override via env: UC_CATALOG, UC_SCHEMA, UC_MODEL_NAME.
  * The served endpoint runs as a service principal / on-behalf-of identity that
    must be able to reach the SAME backends the agent uses at runtime:
        - the SQL warehouse (WAREHOUSE_ID)  -> CAN USE + the metric views
        - the Genie space (GENIE_SPACE_ID)  -> CAN RUN
        - the Lakebase instance (attach-war-room-db / attach_war_room) -> connect + DML
        - Foundation Model API serving endpoints (MODEL_AGENT, MODEL_CLASSIFIER)
    Declare these as `resources` (below) so Agent Framework provisions the
    endpoint with automatic short-lived credentials (M2M OAuth) for them. See:
      https://docs.databricks.com/en/generative-ai/agent-framework/deploy-agent.html
    NOTE: at time of writing, Genie spaces and Lakebase instances may not be
    expressible as typed DatabricksResource entries in every mlflow version. If
    your mlflow rejects DatabricksGenieSpace / a Lakebase resource type, drop it
    from `resources` and instead grant the serving SP access out-of-band, or pass
    PG*/connection config via the endpoint's environment_vars. Verify the typed
    resource list for your mlflow version:
      https://mlflow.org/docs/latest/python_api/mlflow.models.html#mlflow.models.resources
  * pip: mlflow>=2.18, databricks-agents, databricks-sdk, openai, psycopg2-binary.

------------------------------------------------------------------------------
Run
------------------------------------------------------------------------------
  uv run --with 'mlflow>=2.18' --with databricks-agents --with databricks-sdk \
      --with openai --with psycopg2-binary 03_agent/register_and_serve.py

  # log + register only, skip the (slow, billable) endpoint deploy:
  SKIP_DEPLOY=1 uv run ... 03_agent/register_and_serve.py
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

import mlflow  # noqa: E402

# --- configuration (all env-overridable) -----------------------------------
UC_CATALOG = os.environ.get("UC_CATALOG", "main")
UC_SCHEMA = os.environ.get("UC_SCHEMA", "attach_war_room")
UC_MODEL_NAME = os.environ.get("UC_MODEL_NAME", "attach_war_room_agent")
REGISTERED_MODEL = f"{UC_CATALOG}.{UC_SCHEMA}.{UC_MODEL_NAME}"

# Runtime config the served endpoint needs (mirrors 04_app/app.yaml). Models are
# referenced by name so a retired model can be swapped via env, never hardcoded.
WAREHOUSE_ID = os.environ.get("WAREHOUSE_ID", "")
GENIE_SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")
MODEL_AGENT = os.environ.get("MODEL_AGENT", "databricks-claude-sonnet-4-6")
MODEL_CLASSIFIER = os.environ.get("MODEL_CLASSIFIER", "databricks-claude-haiku-4-5")
SCHEMA_FQN = os.environ.get("SCHEMA_FQN", f"{UC_CATALOG}.{UC_SCHEMA}")
LAKEBASE_INSTANCE = os.environ.get("LAKEBASE_INSTANCE", "attach-war-room-db")

EXPERIMENT = os.environ.get("MLFLOW_EXPERIMENT", "/Shared/attach-war-room-agent")

# Files the model imports at serving time — bundled into the model artifact so
# the endpoint is self-contained (no reliance on the repo layout).
CODE_PATHS = [
    os.path.join(THIS_DIR, "agent.py"),
    os.path.join(THIS_DIR, "dbx.py"),
    os.path.join(THIS_DIR, "tools.py"),
    os.path.join(THIS_DIR, "profile.py"),
    os.path.join(THIS_DIR, "fixtures.py"),
]

# Pin the runtime deps for the serving image.
PIP_REQUIREMENTS = [
    "mlflow>=2.18",
    "databricks-sdk",
    "openai",
    "psycopg2-binary",
]


def _resources():
    """Typed Databricks resources the endpoint should get auto-credentials for.

    Wrapped defensively: the set of typed resource classes differs across mlflow
    versions. We include the ones that are broadly available (serving endpoints +
    SQL warehouse) and *attempt* Genie; anything unavailable is skipped with a
    note rather than crashing the script.
    """
    res = []
    try:
        from mlflow.models.resources import (
            DatabricksServingEndpoint,
            DatabricksSQLWarehouse,
        )
        res.append(DatabricksServingEndpoint(endpoint_name=MODEL_AGENT))
        res.append(DatabricksServingEndpoint(endpoint_name=MODEL_CLASSIFIER))
        res.append(DatabricksSQLWarehouse(warehouse_id=WAREHOUSE_ID))
    except Exception as e:  # pragma: no cover - depends on mlflow version
        print(f"[warn] base resources unavailable in this mlflow: {e}")
    try:
        from mlflow.models.resources import DatabricksGenieSpace
        res.append(DatabricksGenieSpace(genie_space_id=GENIE_SPACE_ID))
    except Exception as e:
        print(f"[warn] DatabricksGenieSpace not available; grant Genie access to the "
              f"serving SP out-of-band ({e})")
    # Lakebase: most mlflow versions don't yet expose a typed Lakebase/Database
    # resource. If yours does (e.g. DatabricksDatabaseInstance), add it here.
    try:
        from mlflow.models.resources import DatabricksDatabaseInstance  # type: ignore
        res.append(DatabricksDatabaseInstance(instance_name=LAKEBASE_INSTANCE))
    except Exception:
        print("[note] No typed Lakebase resource in this mlflow — ensure the serving "
              "SP can connect to instance '%s' and pass PG*/LAKEBASE_* via endpoint "
              "environment_vars if needed." % LAKEBASE_INSTANCE)
    return res


def log_and_register():
    """Log the ResponsesAgent and register it to Unity Catalog. Returns the
    ModelInfo (which carries the registered model version)."""
    # UC is the model registry.
    mlflow.set_registry_uri("databricks-uc")
    try:
        mlflow.set_experiment(EXPERIMENT)
    except Exception as e:
        print(f"[warn] could not set experiment {EXPERIMENT}: {e}")

    # An input example in the Responses schema both validates the signature and
    # exercises predict() once at log time (so a broken agent fails fast here).
    input_example = {
        "input": [
            {
                "role": "user",
                "content": "Why did attach rate drop for Velora Telecom mid-tier in Italy, and what should we do?",
            }
        ]
    }

    with mlflow.start_run(run_name="attach-war-room-agent"):
        info = mlflow.pyfunc.log_model(
            # 'name' is the current arg (older mlflow used 'artifact_path').
            name="agent",
            python_model=os.path.join(THIS_DIR, "responses_agent.py"),
            code_paths=CODE_PATHS,
            input_example=input_example,
            pip_requirements=PIP_REQUIREMENTS,
            resources=_resources(),
            registered_model_name=REGISTERED_MODEL,
        )
    print(f"[ok] logged + registered {REGISTERED_MODEL} version {info.registered_model_version}")
    print(f"     run: {info.run_id}  model_uri: {info.model_uri}")
    return info


def deploy(info):
    """Deploy the registered model version to a Model Serving endpoint via the
    Databricks Agent Framework. Prints the endpoint URL."""
    from databricks import agents

    # Pass the agent's runtime config to the endpoint so dbx.py reads the right
    # warehouse/Genie/schema/models/Lakebase in the container.
    environment_vars = {
        "WAREHOUSE_ID": WAREHOUSE_ID,
        "GENIE_SPACE_ID": GENIE_SPACE_ID,
        "SCHEMA_FQN": SCHEMA_FQN,
        "MODEL_AGENT": MODEL_AGENT,
        "MODEL_CLASSIFIER": MODEL_CLASSIFIER,
        "LAKEBASE_INSTANCE": LAKEBASE_INSTANCE,
    }

    print(f"[..] deploying {REGISTERED_MODEL} v{info.registered_model_version} "
          f"(this provisions a billable serving endpoint and can take ~10-20 min)")
    deployment = agents.deploy(
        REGISTERED_MODEL,
        info.registered_model_version,
        environment_vars=environment_vars,
        # scale_to_zero defaults vary by version; the demo endpoint can scale to
        # zero between sessions. Uncomment to be explicit:
        # scale_to_zero=True,
    )

    # The deployment object exposes the query + review endpoints. Field names
    # have been stable but verify against your databricks-agents version:
    #   https://docs.databricks.com/en/generative-ai/agent-framework/deploy-agent.html
    endpoint = getattr(deployment, "endpoint_name", None) or getattr(deployment, "endpoint", None)
    query_url = getattr(deployment, "query_endpoint", None)
    review_url = getattr(deployment, "review_app_url", None)
    print(f"[ok] endpoint: {endpoint}")
    if query_url:
        print(f"     query:  {query_url}")
    if review_url:
        print(f"     review app: {review_url}")
    print("     (also visible under Serving in the workspace UI)")
    return deployment


if __name__ == "__main__":
    info = log_and_register()
    if os.environ.get("SKIP_DEPLOY", "").lower() in ("1", "true", "yes"):
        print("[skip] SKIP_DEPLOY set — registered only, not deploying an endpoint.")
        sys.exit(0)
    deploy(info)
