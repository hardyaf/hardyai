#!/usr/bin/env python3
"""Create or rotate one least-privilege Paperless API service account.

This script is intended to run inside the Paperless container. It writes only
the generated API token to stdout so the host can redirect it directly into a
protected secret file. Diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import os
import sys


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookup-user-id", action="store_true")
    parser.add_argument("username")
    parser.add_argument(
        "permissions",
        nargs="*",
        metavar="APP_LABEL.CODENAME",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "paperless.settings")

    import django

    django.setup()

    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.db import transaction
    from rest_framework.authtoken.models import Token

    requested: list[tuple[str, str]] = []
    for value in args.permissions:
        app_label, separator, codename = value.partition(".")
        if not separator or not app_label or not codename:
            print(f"invalid permission: {value}", file=sys.stderr)
            return 2
        requested.append((app_label, codename))

    resolved = []
    for app_label, codename in requested:
        matches = list(
            Permission.objects.filter(
                content_type__app_label=app_label,
                codename=codename,
            )
        )
        if len(matches) != 1:
            print(
                f"expected one permission for {app_label}.{codename}; found {len(matches)}",
                file=sys.stderr,
            )
            return 2
        resolved.append(matches[0])

    user_model = get_user_model()
    if args.lookup_user_id:
        try:
            user = user_model.objects.get(username=args.username)
        except user_model.DoesNotExist:
            print(f"unknown Paperless user: {args.username}", file=sys.stderr)
            return 2
        sys.stdout.write(str(user.pk))
        return 0
    if not requested:
        print("at least one permission is required", file=sys.stderr)
        return 2
    with transaction.atomic():
        user, _ = user_model.objects.get_or_create(username=args.username)
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        user.set_unusable_password()
        user.save()
        user.groups.clear()
        user.user_permissions.set(resolved)
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)

    sys.stdout.write(token.key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
