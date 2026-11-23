import mimetypes
from pathlib import Path

from botocore.exceptions import ClientError
from django.apps import apps
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import models


class Command(BaseCommand):
    help = "Upload media referenced by database records to the configured private Supabase Storage bucket."

    def handle(self, *args, **options):
        storage = default_storage
        if not getattr(settings, "SUPABASE_STORAGE_ENABLED", False):
            raise CommandError("Enable Supabase Storage and configure its S3 settings first.")

        bucket = getattr(storage, "bucket_name", None)
        if not bucket or not hasattr(storage, "connection"):
            raise CommandError("Enable Supabase Storage and configure its S3 settings first.")
        client = storage.connection.meta.client
        media_root = Path(settings.MEDIA_ROOT).resolve()
        file_names = set()

        for model in apps.get_models():
            if model._meta.app_label != "e_commerce":
                continue
            for field in model._meta.concrete_fields:
                if not isinstance(field, models.FileField):
                    continue
                values = model.objects.exclude(**{field.name: ""}).values_list(field.name, flat=True)
                file_names.update(str(value) for value in values if value)

        try:
            client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            raise CommandError(
                "The configured bucket is unavailable. Create a private bucket and check S3 settings."
            ) from exc

        uploaded = already_present = missing = 0
        for name in sorted(file_names):
            source = (media_root / name).resolve()
            try:
                source.relative_to(media_root)
            except ValueError:
                missing += 1
                continue
            if not source.is_file():
                missing += 1
                continue

            try:
                client.head_object(Bucket=bucket, Key=name)
                already_present += 1
                continue
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code not in {"404", "NoSuchKey", "NotFound"}:
                    raise CommandError(
                        "Supabase Storage could not check an existing object; verify credentials and bucket access."
                    ) from exc

            content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            try:
                client.upload_file(
                    str(source),
                    bucket,
                    name,
                    ExtraArgs={
                        "ContentType": content_type,
                        "CacheControl": "private, max-age=300",
                    },
                )
            except ClientError as exc:
                raise CommandError(
                    "A media object could not be uploaded; no local files were changed."
                ) from exc
            uploaded += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Media upload complete: {uploaded} uploaded, "
                f"{already_present} already present, {missing} local files missing."
            )
        )
