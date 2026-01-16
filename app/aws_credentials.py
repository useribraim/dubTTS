"""
AWS Credentials Management with IAM Roles and Secrets Manager support.

This module implements least-privilege AWS credential management:
1. IAM Roles (for EC2/ECS/Lambda) - preferred for production
2. Secrets Manager (for storing credentials securely)
3. Environment variables (fallback for local development)

Usage:
    from app.aws_credentials import get_aws_session, get_aws_client
    
    # Option 1: Get session and create clients
    session = get_aws_session()
    translate_client = session.client("translate", region_name="eu-west-1")
    
    # Option 2: Get client directly (recommended)
    translate_client = get_aws_client("translate", region_name="eu-west-1")
"""

import os
import json
import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from botocore.credentials import InstanceMetadataProvider, InstanceMetadataFetcher

logger = logging.getLogger(__name__)

# Configuration via environment variables
AWS_REGION = os.getenv("AWS_REGION", "eu-west-1")
USE_AWS = os.getenv("USE_AWS", "1") == "1"

# Secrets Manager configuration
SECRETS_MANAGER_SECRET_NAME = os.getenv("AWS_SECRETS_MANAGER_SECRET_NAME", "")
AWS_SECRETS_MANAGER_REGION = os.getenv("AWS_SECRETS_MANAGER_REGION", AWS_REGION)

# IAM Role configuration (for EC2/ECS/Lambda)
USE_IAM_ROLE = os.getenv("AWS_USE_IAM_ROLE", "auto")  # "auto", "true", "false"
IAM_ROLE_ARN = os.getenv("AWS_IAM_ROLE_ARN", "")  # Optional: specific role ARN


class CredentialsError(Exception):
    """Raised when credentials cannot be obtained."""
    pass


def _get_credentials_from_secrets_manager() -> Optional[dict]:
    """
    Retrieve AWS credentials from AWS Secrets Manager.
    
    Expected secret format (JSON):
    {
        "AWS_ACCESS_KEY_ID": "...",
        "AWS_SECRET_ACCESS_KEY": "...",
        "AWS_REGION": "eu-west-1"  // optional
    }
    
    Returns:
        dict with credentials or None if not configured/available
    """
    if not SECRETS_MANAGER_SECRET_NAME:
        logger.debug("AWS_SECRETS_MANAGER_SECRET_NAME not set, skipping Secrets Manager")
        return None
    
    try:
        # Use a temporary session to get credentials from Secrets Manager
        # This session can use IAM role or environment variables
        temp_session = boto3.Session(region_name=AWS_SECRETS_MANAGER_REGION)
        secrets_client = temp_session.client("secretsmanager")
        
        logger.info(f"Retrieving credentials from Secrets Manager: {SECRETS_MANAGER_SECRET_NAME}")
        response = secrets_client.get_secret_value(SecretId=SECRETS_MANAGER_SECRET_NAME)
        
        secret_string = response.get("SecretString", "")
        if not secret_string:
            logger.warning("Secrets Manager returned empty secret")
            return None
        
        # Parse JSON secret
        secret_data = json.loads(secret_string)
        
        credentials = {
            "aws_access_key_id": secret_data.get("AWS_ACCESS_KEY_ID") or secret_data.get("aws_access_key_id"),
            "aws_secret_access_key": secret_data.get("AWS_SECRET_ACCESS_KEY") or secret_data.get("aws_secret_access_key"),
        }
        
        # Optional: override region from secret
        if "AWS_REGION" in secret_data or "aws_region" in secret_data:
            region = secret_data.get("AWS_REGION") or secret_data.get("aws_region")
            logger.info(f"Using region from secret: {region}")
            global AWS_REGION
            AWS_REGION = region
        
        if not all(credentials.values()):
            logger.warning("Secrets Manager secret missing required fields")
            return None
        
        logger.info("Successfully retrieved credentials from Secrets Manager")
        return credentials
        
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "ResourceNotFoundException":
            logger.warning(f"Secret not found in Secrets Manager: {SECRETS_MANAGER_SECRET_NAME}")
        else:
            logger.error(f"Error retrieving secret from Secrets Manager: {error_code}: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Secrets Manager secret as JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error retrieving credentials from Secrets Manager: {e}", exc_info=True)
        return None


def _should_use_iam_role() -> bool:
    """
    Determine if IAM role should be used.
    
    Returns:
        True if IAM role should be used, False otherwise
    """
    if USE_IAM_ROLE.lower() == "false":
        return False
    if USE_IAM_ROLE.lower() == "true":
        return True
    
    # "auto" mode: use IAM role if running on EC2/ECS/Lambda
    # Check for instance metadata service (available on EC2/ECS)
    try:
        provider = InstanceMetadataProvider(
            iam_role_fetcher=InstanceMetadataFetcher(timeout=1, num_retries=1)
        )
        credentials = provider.load()
        if credentials:
            logger.info("Detected EC2/ECS instance metadata, using IAM role")
            return True
    except Exception:
        # Not on EC2/ECS, or metadata service unavailable
        pass
    
    return False


def get_aws_session(region_name: Optional[str] = None) -> boto3.Session:
    """
    Get a boto3 Session with credentials from the best available source.
    
    Priority order:
    1. IAM Role (if on EC2/ECS/Lambda and enabled)
    2. Secrets Manager (if configured)
    3. Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    4. AWS credentials file (~/.aws/credentials)
    5. Default credential chain
    
    Args:
        region_name: AWS region (defaults to AWS_REGION env var or eu-west-1)
        
    Returns:
        boto3.Session configured with credentials
        
    Raises:
        CredentialsError: If no credentials can be found
    """
    if not USE_AWS:
        raise CredentialsError("USE_AWS is set to 0, AWS services are disabled")
    
    region = region_name or AWS_REGION
    credentials_source = None
    session_kwargs = {"region_name": region}
    
    # Try IAM role first (for production on EC2/ECS/Lambda)
    if _should_use_iam_role():
        try:
            # If specific role ARN is provided, assume that role
            if IAM_ROLE_ARN:
                logger.info(f"Assuming IAM role: {IAM_ROLE_ARN}")
                sts_client = boto3.client("sts", region_name=region)
                assumed_role = sts_client.assume_role(
                    RoleArn=IAM_ROLE_ARN,
                    RoleSessionName="dub-mvp-session"
                )
                credentials = assumed_role["Credentials"]
                session_kwargs.update({
                    "aws_access_key_id": credentials["AccessKeyId"],
                    "aws_secret_access_key": credentials["SecretAccessKey"],
                    "aws_session_token": credentials["SessionToken"],
                })
                credentials_source = "IAM Role (assumed)"
            else:
                # Use instance metadata (EC2/ECS)
                logger.info("Using IAM role from instance metadata")
                credentials_source = "IAM Role (instance metadata)"
        except Exception as e:
            logger.warning(f"Failed to use IAM role: {e}, falling back to other methods")
    
    # Try Secrets Manager if IAM role not used
    if not credentials_source:
        secrets_creds = _get_credentials_from_secrets_manager()
        if secrets_creds:
            session_kwargs.update(secrets_creds)
            credentials_source = "Secrets Manager"
    
    # Create session (will use env vars or credentials file if not specified)
    try:
        session = boto3.Session(**session_kwargs)
        
        # Verify credentials are available
        credentials = session.get_credentials()
        if not credentials:
            raise CredentialsError("No AWS credentials found")
        
        # Log credential source (without exposing sensitive data)
        if credentials_source:
            logger.info(f"Using AWS credentials from: {credentials_source}")
        else:
            # Determine source from credential chain
            if os.getenv("AWS_ACCESS_KEY_ID"):
                credentials_source = "Environment variables"
            else:
                credentials_source = "AWS credentials file or default chain"
            logger.info(f"Using AWS credentials from: {credentials_source}")
        
        return session
        
    except NoCredentialsError:
        raise CredentialsError(
            "No AWS credentials found. Configure one of:\n"
            "  1. IAM role (for EC2/ECS/Lambda)\n"
            "  2. Secrets Manager (set AWS_SECRETS_MANAGER_SECRET_NAME)\n"
            "  3. Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)\n"
            "  4. AWS credentials file (~/.aws/credentials)"
        )
    except Exception as e:
        raise CredentialsError(f"Failed to create AWS session: {e}")


def get_aws_client(service_name: str, region_name: Optional[str] = None):
    """
    Convenience function to get an AWS service client with proper credentials.
    
    Args:
        service_name: AWS service name (e.g., "translate", "polly", "secretsmanager")
        region_name: AWS region (defaults to AWS_REGION)
        
    Returns:
        boto3 client for the specified service
    """
    session = get_aws_session(region_name=region_name)
    return session.client(service_name, region_name=region_name or AWS_REGION)
