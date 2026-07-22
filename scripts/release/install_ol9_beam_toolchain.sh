#!/usr/bin/env bash
set -euo pipefail
umask 022

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
TOOLCHAIN_ROOT=${LIQI_BEAM_TOOLCHAIN_ROOT:-/opt/liqi-beam-toolchain}
BUILD_ROOT=${RUNNER_TEMP:-/var/tmp}/liqi-beam-toolchain-build
OTP_VERSION='28.5.0.3'
OTP_SHA256='63c56a954fe6134f283a01312ebefad00fb0f3ac7d7d42062ca3aa8e92ccd21d'
ELIXIR_VERSION='1.20.2'
ELIXIR_SHA256='5559e5c496ad959bde0bab4dd2b7e92757a0bd45fba6fc58d35584a8337d0ad1'
OTP_URL="https://github.com/erlang/otp/releases/download/OTP-${OTP_VERSION}/otp_src_${OTP_VERSION}.tar.gz"
ELIXIR_URL="https://github.com/elixir-lang/elixir/releases/download/v${ELIXIR_VERSION}/elixir-otp-28.zip"

[[ -r /etc/os-release ]] || { echo 'missing /etc/os-release' >&2; exit 69; }
grep -q '^PLATFORM_ID="platform:el9"$' /etc/os-release || {
  echo 'BEAM release toolchain requires an Enterprise Linux 9 userspace' >&2
  exit 69
}
[[ "$(uname -m)" == 'x86_64' ]] || { echo 'x86_64 builder required' >&2; exit 69; }

mapfile -t versions < <(tr -d '\r' < "$ROOT_DIR/.tool-versions")
[[ "${versions[0]:-}" == "erlang ${OTP_VERSION}" ]] || { echo 'unexpected OTP version pin' >&2; exit 65; }
[[ "${versions[1]:-}" == "elixir ${ELIXIR_VERSION}-otp-28" ]] || { echo 'unexpected Elixir version pin' >&2; exit 65; }

# Packages are installed only inside the ephemeral, digest-pinned OL9 CI container.
dnf -y install \
  autoconf automake binutils bzip2 ca-certificates clang curl diffutils file findutils \
  gcc gcc-c++ git gzip libatomic make m4 ncurses-devel openssl openssl-devel patch \
  perl pkgconf-pkg-config procps-ng python3.11 python3.11-devel python3.11-pip \
  readline-devel tar unzip which xz zlib-devel
dnf clean all

rm -rf "$BUILD_ROOT" "$TOOLCHAIN_ROOT"
install -d -m 0755 "$BUILD_ROOT" "$TOOLCHAIN_ROOT"

curl --proto '=https' --tlsv1.2 --fail --location --retry 4 --silent --show-error \
  "$OTP_URL" --output "$BUILD_ROOT/otp.tar.gz"
printf '%s  %s\n' "$OTP_SHA256" "$BUILD_ROOT/otp.tar.gz" | sha256sum --check --strict

tar --extract --gzip --file "$BUILD_ROOT/otp.tar.gz" --directory "$BUILD_ROOT"
otp_source="$BUILD_ROOT/otp_src_${OTP_VERSION}"
[[ -d "$otp_source" && ! -L "$otp_source" ]]
(
  cd "$otp_source"
  ./configure \
    --prefix="$TOOLCHAIN_ROOT/erlang" \
    --without-javac \
    --without-odbc \
    --without-wx
  make -j2
  make install
)

curl --proto '=https' --tlsv1.2 --fail --location --retry 4 --silent --show-error \
  "$ELIXIR_URL" --output "$BUILD_ROOT/elixir.zip"
printf '%s  %s\n' "$ELIXIR_SHA256" "$BUILD_ROOT/elixir.zip" | sha256sum --check --strict
install -d -m 0755 "$TOOLCHAIN_ROOT/elixir"
unzip -q "$BUILD_ROOT/elixir.zip" -d "$TOOLCHAIN_ROOT/elixir"

export PATH="$TOOLCHAIN_ROOT/erlang/bin:$TOOLCHAIN_ROOT/elixir/bin:$PATH"
if [[ -n "${GITHUB_PATH:-}" ]]; then
  printf '%s\n%s\n' "$TOOLCHAIN_ROOT/erlang/bin" "$TOOLCHAIN_ROOT/elixir/bin" >> "$GITHUB_PATH"
fi

[[ "$(erl -noshell -eval 'io:format("~s", [erlang:system_info(otp_release)]), halt().' )" == '28' ]]
[[ "$(elixir --short-version)" == "$ELIXIR_VERSION" ]]
[[ "$(ldd --version | head -1)" == *' 2.34'* ]]

printf 'OTP_RELEASE=%s\n' "$(erl -noshell -eval 'io:format("~s", [erlang:system_info(otp_release)]), halt().' )"
printf 'ELIXIR_VERSION=%s\n' "$(elixir --short-version)"
printf 'BUILDER_GLIBC=%s\n' "$(ldd --version | head -1)"
rm -rf "$BUILD_ROOT"
