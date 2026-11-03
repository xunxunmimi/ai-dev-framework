#!/usr/bin/env bash

set -Eeuo pipefail

installer_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$installer_dir/install-this-mac.sh"
"$installer_dir/doctor.sh"
