"""Amazon S3 and S3-compatible storage (MinIO, Cloudflare R2, Wasabi, Backblaze B2): files under a bucket prefix.

Location: ``bucket`` or ``bucket/prefix/``. Objects with a supported file type are read; an object's ETag is its
version, so a sync downloads only new and changed files. Needs the ``connectors`` extra (boto3).
"""

from __future__ import annotations

from functools import partial
from pathlib import PurePosixPath
from typing import Any

from ragkit.config import FILE_TYPES
from ragkit.connectors import ACCOUNT_KINDS, CONNECTORS, Account, AccountKind, Cache, Connector, ConnectorError, Item

MAX_OBJECT_BYTES = 200 * 1024 * 1024


def _client(account: Account) -> Any:
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise ConnectorError("S3 needs the 'connectors' extra: pip install 'ragkit[connectors]'") from exc
    endpoint = account.settings.get("endpoint_url") or None
    return boto3.client(
        "s3",
        region_name=account.settings.get("region") or None,
        endpoint_url=endpoint,
        aws_access_key_id=account.secret.get("access_key_id") or None,
        aws_secret_access_key=account.secret.get("secret_access_key") or None,
        # A custom endpoint usually wants bucket names in the path; AWS itself prefers virtual hosts.
        config=Config(s3={"addressing_style": "path" if endpoint else "auto"}, retries={"max_attempts": 3}),
    )


def _error(exc: Exception) -> ConnectorError:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
    messages = {
        "AccessDenied": "the key isn't allowed to read that bucket",
        "NoSuchBucket": "that bucket doesn't exist",
        "InvalidAccessKeyId": "the access key isn't recognised",
        "SignatureDoesNotMatch": "the secret key doesn't match the access key",
    }
    return ConnectorError(messages.get(str(code), f"S3 said: {exc}"))


def _read(client: Any, bucket: str, key: str) -> bytes:
    return bytes(client.get_object(Bucket=bucket, Key=key)["Body"].read())


def check(account: Account) -> str:
    try:
        buckets = _client(account).list_buckets().get("Buckets", [])
    except ConnectorError:
        raise
    except Exception as exc:
        raise _error(exc) from exc
    return f"{len(buckets)} bucket{'' if len(buckets) == 1 else 's'} visible"


def fetch(account: Account, location: str, cache: Cache, limit: int) -> list[Item]:
    bucket, _, prefix = location.strip("/").partition("/")
    if not bucket:
        raise ConnectorError("enter a bucket, optionally followed by a folder: my-bucket/policies/")
    client = _client(account)
    items: list[Item] = []
    try:
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for entry in page.get("Contents", []):
                key = entry["Key"]
                suffix = PurePosixPath(key).suffix.lower()
                if suffix not in FILE_TYPES or entry.get("Size", 0) > MAX_OBJECT_BYTES:
                    continue
                uri = f"s3://{bucket}/{key}"
                items.append(
                    cache.keep(
                        uri,
                        str(entry.get("ETag", "")) + str(entry.get("Size", "")),
                        suffix,
                        partial(_read, client, bucket, key),
                        {"modified": entry["LastModified"].isoformat()} if entry.get("LastModified") else {},
                    )
                )
                if len(items) >= limit:
                    return items
    except ConnectorError:
        raise
    except Exception as exc:
        raise _error(exc) from exc
    return items


ACCOUNT_KINDS["s3"] = AccountKind(
    "Amazon S3 or compatible storage",
    settings=(("region", "Region (e.g. eu-west-1)"), ("endpoint_url", "Endpoint, for MinIO, R2 and others (optional)")),
    secrets=(("access_key_id", "Access key ID"), ("secret_access_key", "Secret access key")),
    check=check,
)
CONNECTORS["s3"] = Connector("s3", "Bucket and folder, e.g. my-bucket/policies/", fetch)
