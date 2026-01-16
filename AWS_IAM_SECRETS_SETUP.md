# AWS IAM Roles and Secrets Manager Setup Guide

This guide explains how to configure AWS credentials using IAM roles and Secrets Manager for secure, production-ready credential management.

## Overview

The application supports three methods of AWS credential management (in priority order):

1. **IAM Roles** (recommended for production on EC2/ECS/Lambda)
2. **Secrets Manager** (recommended for secure credential storage)
3. **Environment Variables** (for local development)

## Method 1: IAM Roles (Production)

IAM roles provide the most secure way to grant AWS permissions without managing access keys.

### For EC2 Instances

1. **Create an IAM Role:**
   ```bash
   aws iam create-role \
     --role-name DubMVPWorkerRole \
     --assume-role-policy-document '{
       "Version": "2012-10-17",
       "Statement": [{
         "Effect": "Allow",
         "Principal": {"Service": "ec2.amazonaws.com"},
         "Action": "sts:AssumeRole"
       }]
     }'
   ```

2. **Attach Required Policies:**
   ```bash
   # Grant Translate and Polly permissions
   aws iam attach-role-policy \
     --role-name DubMVPWorkerRole \
     --policy-arn arn:aws:iam::aws:policy/AmazonTranslateFullAccess
   
   aws iam attach-role-policy \
     --role-name DubMVPWorkerRole \
     --policy-arn arn:aws:iam::aws:policy/AmazonPollyFullAccess
   ```

   **For least-privilege (recommended):**
   ```bash
   # Create custom policy with minimal permissions
   aws iam create-policy \
     --policy-name DubMVPLimitedAccess \
     --policy-document '{
       "Version": "2012-10-17",
       "Statement": [{
         "Effect": "Allow",
         "Action": [
           "translate:TranslateText",
           "polly:SynthesizeSpeech",
           "polly:DescribeVoices"
         ],
         "Resource": "*"
       }]
     }'
   
   # Attach custom policy
   aws iam attach-role-policy \
     --role-name DubMVPWorkerRole \
     --policy-arn arn:aws:iam::YOUR_ACCOUNT_ID:policy/DubMVPLimitedAccess
   ```

3. **Create Instance Profile:**
   ```bash
   aws iam create-instance-profile \
     --instance-profile-name DubMVPInstanceProfile
   
   aws iam add-role-to-instance-profile \
     --instance-profile-name DubMVPInstanceProfile \
     --role-name DubMVPWorkerRole
   ```

4. **Attach to EC2 Instance:**
   - Via AWS Console: EC2 → Instances → Select instance → Actions → Security → Modify IAM role
   - Via CLI:
     ```bash
     aws ec2 associate-iam-instance-profile \
       --instance-id i-1234567890abcdef0 \
       --iam-instance-profile Name=DubMVPInstanceProfile
     ```

5. **Configure Application:**
   ```bash
   export AWS_USE_IAM_ROLE="auto"  # or "true" to force IAM role
   export AWS_REGION="eu-west-1"
   export USE_AWS="1"
   ```

### For ECS Tasks

1. **Create Task Role** (same as EC2 role above)

2. **Configure in Task Definition:**
   ```json
   {
     "taskRoleArn": "arn:aws:iam::YOUR_ACCOUNT_ID:role/DubMVPWorkerRole",
     "executionRoleArn": "arn:aws:iam::YOUR_ACCOUNT_ID:role/ecsTaskExecutionRole"
   }
   ```

3. **No environment variables needed** - ECS automatically provides credentials via task role

### For Lambda Functions

1. **Create Lambda Execution Role** (similar to EC2 role)

2. **Attach to Lambda:**
   ```bash
   aws lambda update-function-configuration \
     --function-name dub-mvp-worker \
     --role arn:aws:iam::YOUR_ACCOUNT_ID:role/DubMVPWorkerRole
   ```

### Cross-Account Role Assumption

If you need to assume a role in another AWS account:

```bash
export AWS_IAM_ROLE_ARN="arn:aws:iam::TARGET_ACCOUNT_ID:role/DubMVPWorkerRole"
export AWS_USE_IAM_ROLE="true"
```

The application will automatically assume this role and use temporary credentials.

## Method 2: Secrets Manager (Secure Credential Storage)

AWS Secrets Manager provides secure storage and automatic rotation of credentials.

### Step 1: Store Credentials in Secrets Manager

```bash
# Create secret with credentials
aws secretsmanager create-secret \
  --name dub-mvp/aws-credentials \
  --description "AWS credentials for Dub MVP application" \
  --secret-string '{
    "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
    "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "AWS_REGION": "eu-west-1"
  }'
```

### Step 2: Grant Access to Secrets Manager

Create an IAM policy that allows reading the secret:

```bash
aws iam create-policy \
  --policy-name DubMVPSecretsManagerAccess \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:eu-west-1:YOUR_ACCOUNT_ID:secret:dub-mvp/aws-credentials-*"
    }]
  }'
```

Attach this policy to your EC2 instance role or ECS task role.

### Step 3: Configure Application

```bash
export AWS_SECRETS_MANAGER_SECRET_NAME="dub-mvp/aws-credentials"
export AWS_SECRETS_MANAGER_REGION="eu-west-1"  # Optional, defaults to AWS_REGION
export AWS_REGION="eu-west-1"
export USE_AWS="1"
```

### Step 4: Verify Access

```bash
# Test secret retrieval
aws secretsmanager get-secret-value \
  --secret-id dub-mvp/aws-credentials \
  --region eu-west-1
```

## Method 3: Environment Variables (Local Development)

For local development, you can use environment variables:

```bash
export AWS_ACCESS_KEY_ID="your-access-key-id"
export AWS_SECRET_ACCESS_KEY="your-secret-access-key"
export AWS_REGION="eu-west-1"
export USE_AWS="1"
```

**Note:** Never commit credentials to version control. Use `.env` files (gitignored) or AWS credentials file (`~/.aws/credentials`).

## Configuration Priority

The application checks credentials in this order:

1. **IAM Role** (if `AWS_USE_IAM_ROLE` is "true" or "auto" and running on EC2/ECS)
2. **Secrets Manager** (if `AWS_SECRETS_MANAGER_SECRET_NAME` is set)
3. **Environment Variables** (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
4. **AWS Credentials File** (`~/.aws/credentials`)

## Testing Configuration

Run the configuration check script:

```bash
cd dub_mvp
python check_aws_config.py
```

This will:
- Detect which credential method is being used
- Test AWS Translate and Polly connectivity
- Verify permissions

## Security Best Practices

### 1. Least Privilege IAM Policies

Create custom IAM policies with only the permissions needed:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "translate:TranslateText"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "polly:SynthesizeSpeech",
        "polly:DescribeVoices"
      ],
      "Resource": "*"
    }
  ]
}
```

### 2. Use IAM Roles Instead of Access Keys

- IAM roles eliminate the need to manage access keys
- Credentials are automatically rotated
- No risk of credential leakage in code or environment variables

### 3. Enable CloudTrail Logging

Monitor AWS API calls:

```bash
aws cloudtrail create-trail \
  --name dub-mvp-trail \
  --s3-bucket-name your-cloudtrail-bucket
```

### 4. Rotate Secrets Regularly

If using Secrets Manager, enable automatic rotation:

```bash
aws secretsmanager rotate-secret \
  --secret-id dub-mvp/aws-credentials \
  --rotation-lambda-arn arn:aws:lambda:eu-west-1:YOUR_ACCOUNT_ID:function:rotate-credentials
```

### 5. Use VPC Endpoints (for EC2/ECS)

Reduce exposure by using VPC endpoints for AWS services:

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-12345678 \
  --service-name com.amazonaws.eu-west-1.translate \
  --vpc-endpoint-type Interface
```

## Troubleshooting

### "No credentials found"

- Check that `USE_AWS=1` is set
- Verify IAM role is attached (for EC2/ECS)
- Check Secrets Manager secret exists and is accessible
- Verify environment variables are set (for local dev)

### "Access Denied" errors

- Verify IAM role/policy has required permissions
- Check CloudTrail logs for specific denied actions
- Ensure region matches where services are available

### Secrets Manager access denied

- Verify IAM role has `secretsmanager:GetSecretValue` permission
- Check secret ARN matches exactly
- Verify region is correct

### IAM role not detected

- On EC2: Check instance metadata service is accessible (`curl http://169.254.169.254/latest/meta-data/`)
- Set `AWS_USE_IAM_ROLE=true` to force IAM role usage
- Verify instance profile is attached

## Example: Complete Production Setup

```bash
# 1. Create IAM role with least-privilege policy
aws iam create-role --role-name DubMVPWorkerRole --assume-role-policy-document file://trust-policy.json
aws iam put-role-policy --role-name DubMVPWorkerRole --policy-name DubMVPLimitedAccess --policy-document file://policy.json

# 2. Create instance profile and attach role
aws iam create-instance-profile --instance-profile-name DubMVPInstanceProfile
aws iam add-role-to-instance-profile --instance-profile-name DubMVPInstanceProfile --role-name DubMVPWorkerRole

# 3. Attach to EC2 instance
aws ec2 associate-iam-instance-profile --instance-id i-1234567890abcdef0 --iam-instance-profile Name=DubMVPInstanceProfile

# 4. Configure application (no credentials needed!)
export AWS_REGION="eu-west-1"
export USE_AWS="1"
export AWS_USE_IAM_ROLE="auto"  # Will auto-detect EC2 instance metadata
```

## References

- [AWS IAM Roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles.html)
- [AWS Secrets Manager](https://docs.aws.amazon.com/secretsmanager/)
- [boto3 Credentials](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html)
- [Least Privilege Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
