"""S3 Service for presigned URL generation.

This module provides S3 operations for the presigned upload workflow:
1. Generate presigned PUT URLs for browser uploads
2. Generate presigned GET URLs for Deepgram to fetch files
3. Verify object existence after upload

Security:
- Presigned URLs are short-lived (5 minutes for PUT, 1 hour for GET)
- File keys are tenant-scoped: tenant/{tenant_id}/uploads/{job_id}/{filename}
- No public access to bucket - all access via presigned URLs

Configuration:
- UPLOAD_BUCKET_NAME: S3 bucket name
- UPLOAD_REGION: AWS region (defaults to AWS_REGION)
- AWS credentials from environment (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Default expiry times
PUT_URL_EXPIRY_SECONDS = 300  # 5 minutes for upload
GET_URL_EXPIRY_SECONDS = 3600  # 1 hour for Deepgram to fetch


class S3ServiceError(Exception):
    """Raised when S3 operations fail."""
    pass


class S3Service:
    """S3 service for presigned URL generation and object verification."""

    def __init__(self):
        """Initialize S3 client with configured bucket and region."""
        self.bucket_name = os.getenv("UPLOAD_BUCKET_NAME", "eq-live-transcription-uploads-dev")
        self.region = os.getenv("UPLOAD_REGION", os.getenv("AWS_REGION", "us-east-1"))

        # Configure boto3 client with signature v4 (required for presigned URLs)
        config = Config(
            region_name=self.region,
            signature_version='s3v4',
            retries={'max_attempts': 3, 'mode': 'standard'}
        )

        self.client = boto3.client('s3', config=config)
        logger.info(f"S3Service initialized: bucket={self.bucket_name}, region={self.region}")

    def generate_file_key(
        self,
        tenant_id: str,
        job_id: str,
        filename: str
    ) -> str:
        """Generate a tenant-scoped S3 key for an upload.

        Format: tenant/{tenant_id}/uploads/{job_id}/{filename}

        This format ensures:
        - Tenant isolation via prefix
        - Job correlation for cleanup
        - Original filename preserved for debugging

        Args:
            tenant_id: UUID string of the tenant
            job_id: UUID string of the job
            filename: Original filename (will be sanitized)

        Returns:
            S3 object key string
        """
        # Sanitize filename (remove path separators, limit length)
        safe_filename = filename.replace("/", "_").replace("\\", "_")
        if len(safe_filename) > 100:
            # Keep extension, truncate name
            parts = safe_filename.rsplit(".", 1)
            if len(parts) == 2:
                name, ext = parts
                safe_filename = f"{name[:90]}.{ext}"
            else:
                safe_filename = safe_filename[:100]

        return f"tenant/{tenant_id}/uploads/{job_id}/{safe_filename}"

    def generate_presigned_put_url(
        self,
        file_key: str,
        content_type: str = "application/octet-stream",
        expiry_seconds: int = PUT_URL_EXPIRY_SECONDS
    ) -> tuple[str, datetime]:
        """Generate a presigned PUT URL for browser upload.

        Args:
            file_key: S3 object key
            content_type: MIME type of the file
            expiry_seconds: URL expiry time in seconds

        Returns:
            Tuple of (presigned_url, expires_at)

        Raises:
            S3ServiceError: If URL generation fails
        """
        try:
            url = self.client.generate_presigned_url(
                'put_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': file_key,
                    'ContentType': content_type
                },
                ExpiresIn=expiry_seconds
            )

            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds)

            logger.info(f"Generated presigned PUT URL: key={file_key[:50]}..., expires_in={expiry_seconds}s")
            return url, expires_at

        except ClientError as e:
            logger.error(f"Failed to generate presigned PUT URL: {e}")
            raise S3ServiceError(f"Failed to generate upload URL: {e}")

    def generate_presigned_get_url(
        self,
        file_key: str,
        expiry_seconds: int = GET_URL_EXPIRY_SECONDS
    ) -> str:
        """Generate a presigned GET URL for Deepgram to fetch the file.

        The URL must live long enough for Deepgram to start fetching.
        Default is 1 hour which is generous for most use cases.

        Args:
            file_key: S3 object key
            expiry_seconds: URL expiry time in seconds

        Returns:
            Presigned GET URL

        Raises:
            S3ServiceError: If URL generation fails
        """
        try:
            url = self.client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': file_key
                },
                ExpiresIn=expiry_seconds
            )

            logger.info(f"Generated presigned GET URL: key={file_key[:50]}..., expires_in={expiry_seconds}s")
            return url

        except ClientError as e:
            logger.error(f"Failed to generate presigned GET URL: {e}")
            raise S3ServiceError(f"Failed to generate download URL: {e}")

    def verify_object_exists(self, file_key: str) -> bool:
        """Verify that an object exists in S3.

        Used after browser upload to confirm the file was uploaded successfully.

        Args:
            file_key: S3 object key

        Returns:
            True if object exists, False otherwise
        """
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=file_key)
            logger.debug(f"Object exists: {file_key[:50]}...")
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                logger.warning(f"Object not found: {file_key[:50]}...")
                return False
            logger.error(f"Error checking object existence: {e}")
            return False

    def get_object_metadata(self, file_key: str) -> Optional[dict]:
        """Get metadata for an object.

        Args:
            file_key: S3 object key

        Returns:
            Dict with content_type, content_length, last_modified, or None if not found
        """
        try:
            response = self.client.head_object(Bucket=self.bucket_name, Key=file_key)
            return {
                'content_type': response.get('ContentType'),
                'content_length': response.get('ContentLength'),
                'last_modified': response.get('LastModified'),
            }
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return None
            logger.error(f"Error getting object metadata: {e}")
            return None

    def extract_tenant_from_key(self, file_key: str) -> Optional[str]:
        """Extract tenant_id from a file key.

        Expected format: tenant/{tenant_id}/uploads/{job_id}/{filename}

        Args:
            file_key: S3 object key

        Returns:
            Tenant ID string, or None if format doesn't match
        """
        parts = file_key.split("/")
        if len(parts) >= 2 and parts[0] == "tenant":
            return parts[1]
        return None

    def validate_key_belongs_to_tenant(self, file_key: str, tenant_id: str) -> bool:
        """Validate that a file key belongs to the specified tenant.

        Security check to prevent cross-tenant access.

        Args:
            file_key: S3 object key
            tenant_id: Expected tenant ID

        Returns:
            True if key belongs to tenant, False otherwise
        """
        expected_prefix = f"tenant/{tenant_id}/"
        return file_key.startswith(expected_prefix)
