#!/usr/bin/env python3
import os
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.data_stack import DataStack
from stacks.compute_stack import ComputeStack
from stacks.ingest_stack import IngestStack


env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

PROJECT = "stori-rag"

app = cdk.App()

network = NetworkStack(app, f"{PROJECT}-network", env=env)
data = DataStack(app, f"{PROJECT}-data", env=env)

compute = ComputeStack(
    app,
    f"{PROJECT}-compute",
    env=env,
    vpc=network.vpc,
    alb=network.alb,
    alb_security_group=network.alb_security_group,
    index_bucket=data.index_bucket,
    checkpointer_table=data.checkpointer_table,
)

ingest = IngestStack(
    app,
    f"{PROJECT}-ingest",
    env=env,
    vpc=network.vpc,
    corpus_bucket=data.corpus_bucket,
    index_bucket=data.index_bucket,
    ecr_repository=compute.ecr_repository,
)

compute.add_dependency(network)
compute.add_dependency(data)
ingest.add_dependency(data)
ingest.add_dependency(compute)

cdk.Tags.of(app).add("project", PROJECT)
cdk.Tags.of(app).add("managed-by", "cdk")

app.synth()