"""
AWS S3 upload service for prescription PDFs.
Uses ap-south-1 (Mumbai) region.
"""
import boto3
from botocore.exceptions import ClientError
from app.config import Settings


async def upload_pdf_to_s3(
    pdf_bytes: bytes,
    s3_key: str,
    settings: Settings,
    expiry_seconds: int = 3600,
) -> str:
    """
    Upload PDF bytes to S3 and return a pre-signed URL valid for expiry_seconds.
    Raises on failure — caller should handle and fall back to base64.
    """
    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        raise ValueError("AWS credentials not configured.")

    s3_client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )

    s3_client.put_object(
        Bucket=settings.aws_s3_bucket,
        Key=s3_key,
        Body=pdf_bytes,
        ContentType="application/pdf",
        # Prescriptions are private — no public access
        ACL="private",
        ServerSideEncryption="AES256",
    )

    # Generate pre-signed URL for browser download/print
    url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.aws_s3_bucket, "Key": s3_key},
        ExpiresIn=expiry_seconds,
    )

    return url


async def upload_bytes_to_s3(
    data: bytes,
    s3_key: str,
    settings: Settings,
    *,
    content_type: str = "application/octet-stream",
    expiry_seconds: int = 3600,
) -> str:
    """Upload arbitrary bytes to S3 and return a pre-signed URL."""
    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        raise ValueError("AWS credentials not configured.")

    s3_client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )

    s3_client.put_object(
        Bucket=settings.aws_s3_bucket,
        Key=s3_key,
        Body=data,
        ContentType=content_type,
        ACL="private",
        ServerSideEncryption="AES256",
    )

    url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.aws_s3_bucket, "Key": s3_key},
        ExpiresIn=expiry_seconds,
    )
    return url


def download_bytes_from_s3(s3_key: str, settings: Settings) -> bytes:
    """Download object bytes from S3 (sync — use in thread pool)."""
    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        raise ValueError("AWS credentials not configured.")

    s3_client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    obj = s3_client.get_object(Bucket=settings.aws_s3_bucket, Key=s3_key)
    return obj["Body"].read()


def delete_object_from_s3(s3_key: str, settings: Settings) -> None:
    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        return
    s3_client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    s3_client.delete_object(Bucket=settings.aws_s3_bucket, Key=s3_key)
