#!/usr/bin/env bash
set -euo pipefail
umask 077
STATE_DATABASE=${STATE_DATABASE:-liqi_infra_state}
STATE_SCHEMA=${STATE_SCHEMA:-opentofu_v1_live}
STATE_ROLE=${STATE_ROLE:-liqi_tofu_state}
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_command(){ command -v "$1" >/dev/null 2>&1 || fail "required command missing: $1"; }
is_windows_host(){ [[ "$(uname -s 2>/dev/null || true)" =~ ^(MINGW|MSYS|CYGWIN) ]]; }
windows_path(){ command -v cygpath >/dev/null 2>&1 && cygpath -w "$1" || printf '%s' "$1"; }
protect_file(){
  local p=$1 mode=${2:-600}
  [ -f "$p" ] || fail "cannot protect missing file: $p"
  if is_windows_host; then
    require_command powershell.exe; require_command icacls.exe
    local wp me
    wp=$(windows_path "$p")
    me=$(powershell.exe -NoProfile -NonInteractive -Command '[System.Security.Principal.WindowsIdentity]::GetCurrent().Name' | tr -d '\r\n')
    MSYS2_ARG_CONV_EXCL='*' icacls.exe "$wp" /inheritance:r /grant:r "$me:F" 'NT AUTHORITY\SYSTEM:F' 'BUILTIN\Administrators:F' >/dev/null || fail "could not protect Windows file ACL: $p"
  else
    chmod "$mode" "$p"
  fi
}
require_file_0600(){
  local p=$1
  [ -f "$p" ] || fail "required protected file missing: $p"
  if is_windows_host; then
    require_command powershell.exe
    local wp
    wp=$(windows_path "$p")
    LIQI_PROTECTED_FILE="$wp" powershell.exe -NoProfile -NonInteractive -Command '
      $ErrorActionPreference="Stop"
      $path=$env:LIQI_PROTECTED_FILE
      $acl=Get-Acl -LiteralPath $path
      $current=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
      $allowed=@($current,"S-1-5-18","S-1-5-32-544")
      $owner=([System.Security.Principal.NTAccount]$acl.Owner).Translate([System.Security.Principal.SecurityIdentifier]).Value
      if($allowed -notcontains $owner){exit 2}
      $bad=@($acl.Access | Where-Object {
        $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
        $allowed -notcontains $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
      })
      if($bad.Count -ne 0){exit 3}
      $mine=@($acl.Access | Where-Object {
        $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
        $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $current
      })
      if($mine.Count -eq 0){exit 4}
    ' >/dev/null || fail "$p ACL permits an unexpected Windows principal or denies the operator"
  else
    local m
    m=$(stat -c '%a' "$p" 2>/dev/null || true)
    [ -z "$m" ] || [ "$m" = 600 ] || [ "$m" = 400 ] || fail "$p must be mode 0600 or 0400"
  fi
}
validate_identifier(){ [[ "$1" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || fail "invalid PostgreSQL identifier"; }
validate_common(){ validate_identifier "$STATE_DATABASE"; validate_identifier "$STATE_SCHEMA"; validate_identifier "$STATE_ROLE"; [ -n "${PGSERVICEFILE:-}" ] || fail 'PGSERVICEFILE is required'; require_file_0600 "$PGSERVICEFILE"; }
json_sha(){ sha256sum "$1" | awk '{print $1}'; }
