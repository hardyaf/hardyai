#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  sudo bash scripts/provision_document_storage.sh \
    --device /dev/nvme0n1 \
    --confirm-erase /dev/nvme0n1 \
    --mount-path /mnt/hardyai-documents

This irreversibly formats one whole disk as LUKS2, enrolls the host TPM2, creates
an ext4 filesystem, and installs persistent crypttab/fstab entries. The script
refuses mounted, partitioned, signed, non-disk, root-backing, or mismatched devices.
EOF
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

device=""
confirmed_device=""
mount_path=""
mapper_name="hardyai-documents"
owner_uid="1001"
owner_gid="1001"

while (($#)); do
  case "$1" in
    --device)
      device="${2:-}"
      shift 2
      ;;
    --confirm-erase)
      confirmed_device="${2:-}"
      shift 2
      ;;
    --mount-path)
      mount_path="${2:-}"
      shift 2
      ;;
    --mapper-name)
      mapper_name="${2:-}"
      shift 2
      ;;
    --owner-uid)
      owner_uid="${2:-}"
      shift 2
      ;;
    --owner-gid)
      owner_gid="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || fail "run this script through sudo"
[[ -n "$device" && -n "$confirmed_device" && -n "$mount_path" ]] || {
  usage >&2
  fail "--device, --confirm-erase, and --mount-path are required"
}
[[ "$device" == /dev/* && "$confirmed_device" == /dev/* ]] || fail "device paths must be absolute"
[[ "$mount_path" == /* && "$mount_path" != / ]] || fail "mount path must be absolute and non-root"
[[ "$mapper_name" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$ ]] || fail "invalid mapper name"
[[ "$owner_uid" =~ ^[0-9]+$ && "$owner_gid" =~ ^[0-9]+$ ]] || fail "UID/GID must be numeric"

for command in cryptsetup findmnt lsblk mkfs.ext4 mount readlink systemd-cryptenroll systemd-cryptsetup wipefs; do
  command -v "$command" >/dev/null || fail "required command is unavailable: $command"
done

resolved_device="$(readlink -f -- "$device")"
resolved_confirmation="$(readlink -f -- "$confirmed_device")"
[[ "$resolved_device" == "$resolved_confirmation" ]] || fail "erase confirmation does not match device"
[[ -b "$resolved_device" ]] || fail "target is not a block device: $resolved_device"
[[ "$(lsblk -dnro TYPE -- "$resolved_device")" == "disk" ]] || fail "target must be a whole disk"

mapfile -t target_nodes < <(lsblk -nrpo PATH -- "$resolved_device")
[[ "${#target_nodes[@]}" -eq 1 ]] || fail "target has partitions or mapped child devices"
if lsblk -nrpo MOUNTPOINTS -- "$resolved_device" | grep -q '[^[:space:]]'; then
  fail "target or one of its children is mounted"
fi

root_source="$(findmnt -nro SOURCE /)"
mapfile -t root_devices < <(lsblk -srnpo PATH -- "$root_source")
for root_device in "${root_devices[@]}"; do
  if [[ "$(readlink -f -- "$root_device")" == "$resolved_device" ]]; then
    fail "refusing to erase a disk that backs the root filesystem"
  fi
done

if cryptsetup isLuks "$resolved_device" >/dev/null 2>&1; then
  fail "target is already a LUKS device; use the recovery procedure instead"
fi
if [[ -n "$(wipefs -n --output TYPE --noheadings -- "$resolved_device" | tr -d '[:space:]')" ]]; then
  fail "target contains an existing disk signature; inspect it manually before erasure"
fi
[[ ! -e "/dev/mapper/$mapper_name" ]] || fail "mapper already exists: $mapper_name"
if [[ -e "$mount_path" ]]; then
  [[ -d "$mount_path" ]] || fail "mount path exists and is not a directory"
  [[ -z "$(find "$mount_path" -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail "mount path is not empty"
  findmnt -rn --mountpoint "$mount_path" >/dev/null 2>&1 && fail "mount path is already mounted"
fi

# This root-only probe must pass before the disk is touched.
systemd-cryptenroll --tpm2-device=list >/dev/null \
  || fail "TPM2 enrollment is unavailable; no disk changes were made"

printf 'Validated destructive target: %s\n' "$resolved_device"
printf 'Core/root source remains: %s\n' "$root_source"
printf 'You will be prompted to create and verify the LUKS recovery passphrase.\n'
cryptsetup luksFormat --type luks2 --verify-passphrase --pbkdf argon2id --batch-mode "$resolved_device"

opened=0
mounted=0
cleanup_on_error() {
  status=$?
  if [[ "$status" -ne 0 ]]; then
    if [[ "$mounted" -eq 1 ]]; then
      umount "$mount_path" || true
    fi
    if [[ "$opened" -eq 1 ]]; then
      cryptsetup close "$mapper_name" || true
    fi
    printf 'Provisioning stopped after LUKS creation; preserve the recovery passphrase and inspect manually.\n' >&2
  fi
  exit "$status"
}
trap cleanup_on_error EXIT

printf 'Enter the same recovery passphrase once more to open the new volume.\n'
cryptsetup open "$resolved_device" "$mapper_name"
opened=1
mkfs.ext4 -L "$mapper_name" "/dev/mapper/$mapper_name"
mkdir -p "$mount_path"
chmod 0700 "$mount_path"
mount "/dev/mapper/$mapper_name" "$mount_path"
mounted=1
chown root:"$owner_gid" "$mount_path"
chmod 0710 "$mount_path"

printf 'Enter the recovery passphrase to authorize TPM2 enrollment.\n'
systemd-cryptenroll --tpm2-device=auto "$resolved_device"

# Prove that the enrolled TPM can reopen the volume before making it persistent.
umount "$mount_path"
mounted=0
cryptsetup close "$mapper_name"
opened=0
systemd-cryptsetup attach "$mapper_name" "$resolved_device" - tpm2-device=auto
opened=1
mount "/dev/mapper/$mapper_name" "$mount_path"
mounted=1

luks_uuid="$(cryptsetup luksUUID "$resolved_device")"
filesystem_uuid="$(blkid -s UUID -o value "/dev/mapper/$mapper_name")"
[[ -n "$luks_uuid" && -n "$filesystem_uuid" ]] || fail "could not resolve new volume UUIDs"

grep -qE "^[[:space:]]*$mapper_name[[:space:]]" /etc/crypttab 2>/dev/null \
  && fail "crypttab already contains mapper name $mapper_name"
grep -qF "$luks_uuid" /etc/crypttab 2>/dev/null \
  && fail "crypttab already contains LUKS UUID $luks_uuid"
grep -qF "$mount_path" /etc/fstab \
  && fail "fstab already contains mount path $mount_path"

printf '%s UUID=%s none luks,tpm2-device=auto,nofail\n' \
  "$mapper_name" "$luks_uuid" >>/etc/crypttab
printf 'UUID=%s %s ext4 defaults,nofail,x-systemd.device-timeout=30s 0 2\n' \
  "$filesystem_uuid" "$mount_path" >>/etc/fstab

install -d -m 0700 -o 999 -g "$owner_gid" \
  "$mount_path/paperless/valkey" \
  "$mount_path/paperless/postgres"
install -d -m 0700 -o "$owner_uid" -g "$owner_gid" \
  "$mount_path/paperless/data" \
  "$mount_path/paperless/media" \
  "$mount_path/paperless/export" \
  "$mount_path/jarvis" \
  "$mount_path/jarvis/spool" \
  "$mount_path/jarvis/artifacts" \
  "$mount_path/jarvis/import" \
  "$mount_path/control" \
  "$mount_path/backups" \
  "$mount_path/restore-drills"
install -d -m 0700 -o "$owner_uid" -g "$owner_gid" /etc/hardyai/documents
install -m 0600 -o "$owner_uid" -g "$owner_gid" /dev/null \
  /etc/hardyai/documents/paperless_read_user_id \
  /etc/hardyai/documents/docling_api_key

systemctl daemon-reload
trap - EXIT
printf 'Document storage provisioned successfully.\n'
findmnt -no TARGET,SOURCE,FSTYPE "$mount_path"
cryptsetup luksDump "$resolved_device" | sed -n '1,18p'
