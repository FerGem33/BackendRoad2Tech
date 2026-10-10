"""AWS client construction shared by the SageMaker operator commands."""
from __future__ import annotations

import boto3
from botocore.config import Config


def session(profile: str | None, region: str) -> boto3.Session:
    """Create an explicit session so team members can use named local profiles."""
    return boto3.Session(profile_name=profile, region_name=region)


def client(aws_session: boto3.Session, service_name: str):
    """Create a bounded-retry SDK client for one command invocation."""
    return aws_session.client(
        service_name,
        config=Config(retries={"total_max_attempts": 3, "mode": "standard"}, connect_timeout=10, read_timeout=60),
    )


def resource(aws_session: boto3.Session, service_name: str):
    """Create a boto3 resource for services with useful native type marshalling."""
    return aws_session.resource(
        service_name,
        config=Config(retries={"total_max_attempts": 3, "mode": "standard"}, connect_timeout=10, read_timeout=60),
    )
