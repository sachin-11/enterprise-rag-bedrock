from functools import lru_cache

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

from app.core.config import settings


@lru_cache
def get_boto3_session() -> boto3.Session:
    return boto3.Session(
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
    )


@lru_cache
def get_s3_client():
    return get_boto3_session().client("s3")


@lru_cache
def get_bedrock_agent_client():
    return get_boto3_session().client("bedrock-agent")


@lru_cache
def get_bedrock_agent_runtime_client():
    return get_boto3_session().client("bedrock-agent-runtime")


@lru_cache
def get_bedrock_runtime_client():
    return get_boto3_session().client("bedrock-runtime")


@lru_cache
def get_cognito_client():
    return get_boto3_session().client("cognito-idp")


@lru_cache
def get_dynamodb_resource():
    return get_boto3_session().resource("dynamodb")


@lru_cache
def get_ses_client():
    return get_boto3_session().client("sesv2")


@lru_cache
def get_opensearch_client() -> OpenSearch:
    credentials = get_boto3_session().get_credentials()
    # "aoss" signs requests for OpenSearch Serverless; use "es" instead if
    # OPENSEARCH_ENDPOINT points at a traditional managed OpenSearch domain.
    auth = AWSV4SignerAuth(credentials, settings.aws_region, "aoss")
    host = settings.opensearch_endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )
