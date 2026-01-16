#!/usr/bin/env python3
"""
Check AWS configuration and test TTS functionality.
Tests IAM roles, Secrets Manager, and environment variable credentials.
"""

import os
import sys

# Add app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

print("=" * 60)
print("AWS Configuration Check")
print("=" * 60)

# Check environment variables
use_aws = os.getenv("USE_AWS", "1")
aws_region = os.getenv("AWS_REGION", "eu-west-1")
secrets_name = os.getenv("AWS_SECRETS_MANAGER_SECRET_NAME", "")
use_iam_role = os.getenv("AWS_USE_IAM_ROLE", "auto")
iam_role_arn = os.getenv("AWS_IAM_ROLE_ARN", "")

print(f"\nEnvironment Variables:")
print(f"   USE_AWS: {use_aws}")
print(f"   AWS_REGION: {aws_region}")
print(f"   AWS_SECRETS_MANAGER_SECRET_NAME: {secrets_name or '(not set)'}")
print(f"   AWS_USE_IAM_ROLE: {use_iam_role}")
print(f"   AWS_IAM_ROLE_ARN: {iam_role_arn or '(not set)'}")

if use_aws == "0":
    print("\n[WARNING] USE_AWS is set to '0' - AWS services are disabled!")
    print("   TTS will generate 600ms silence files instead of real audio.")
    print("   Set USE_AWS=1 to enable AWS Polly.")
    sys.exit(1)

# Check AWS credentials using new credential management
print(f"\nAWS Credentials:")
try:
    from app.aws_credentials import get_aws_session, CredentialsError, get_aws_client
    from botocore.exceptions import ClientError
    
    # Get session using new credential management
    try:
        session = get_aws_session()
        credentials = session.get_credentials()
        
        if credentials is None:
            print("   [ERROR] No AWS credentials found!")
            print("\n   To configure AWS credentials:")
            print("   1. IAM Role (for EC2/ECS/Lambda):")
            print("      - Attach IAM role to instance/task")
            print("      - Set AWS_USE_IAM_ROLE=true (or leave as 'auto')")
            print("   2. Secrets Manager:")
            print("      - Store credentials in AWS Secrets Manager")
            print("      - Set AWS_SECRETS_MANAGER_SECRET_NAME=your-secret-name")
            print("   3. Environment variables (local dev):")
            print("      export AWS_ACCESS_KEY_ID=your_key")
            print("      export AWS_SECRET_ACCESS_KEY=your_secret")
            print("   4. AWS credentials file:")
            print("      aws configure")
            sys.exit(1)
        else:
            print("   [OK] AWS credentials found")
            access_key = credentials.access_key
            print(f"   Access Key ID: {access_key[:8]}...{access_key[-4:]}")
            if hasattr(credentials, 'token') and credentials.token:
                print("   Using temporary credentials (IAM role or assumed role)")
            else:
                print("   Using long-term credentials")
    
    except CredentialsError as e:
        print(f"   [ERROR] {e}")
        sys.exit(1)
    
    # Test Polly connection using new credential management
    print(f"\nTesting AWS Polly connection...")
    polly = get_aws_client("polly", region_name=aws_region)
    
    try:
        # Try to list voices (this will fail if credentials are invalid)
        response = polly.describe_voices(LanguageCode="ru-RU")
        voices = response.get("Voices", [])
        print(f"   [OK] Polly connection successful!")
        print(f"   Found {len(voices)} Russian voices:")
        for voice in voices[:5]:  # Show first 5
            print(f"      - {voice['Name']} ({voice['Gender']})")
        
        # Test actual synthesis
        print(f"\nTesting TTS synthesis...")
        test_text = "Привет, это тест."
        response = polly.synthesize_speech(
            Text=test_text,
            OutputFormat="pcm",
            VoiceId="Tatyana",
            SampleRate="16000"
        )
        audio_data = response["AudioStream"].read()
        print(f"   [OK] TTS synthesis successful!")
        print(f"   Generated {len(audio_data)} bytes of audio")
        print(f"   Duration: ~{len(audio_data) / 32000:.2f} seconds (estimated)")
        
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        print(f"   [ERROR] Polly error: {error_code}")
        print(f"   Error message: {e}")
        if error_code == "InvalidSignatureException":
            print("\n   This usually means:")
            print("   - AWS credentials are incorrect")
            print("   - AWS region is wrong")
            print("   - System clock is out of sync")
        sys.exit(1)
        
except ImportError as e:
    print(f"   [ERROR] Import error: {e}")
    print("   Make sure you're running from the dub_mvp directory")
    print("   Install dependencies with: pip install -r requirements.txt")
    sys.exit(1)
except Exception as e:
    print(f"   [ERROR] Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("[OK] AWS configuration looks good!")
print("=" * 60)
print("\nCredential Management Features:")
print("   - IAM Role support (for EC2/ECS/Lambda)")
print("   - Secrets Manager support (for secure credential storage)")
print("   - Environment variable fallback (for local development)")
print("\nIf TTS is still generating silence, check:")
print("   1. Worker process has USE_AWS=1 set")
print("   2. Worker process has AWS credentials available")
print("   3. Check worker logs for TTS errors")
print("   4. Verify IAM role permissions (if using IAM roles)")
print("   5. Verify Secrets Manager secret exists (if using Secrets Manager)")