#!/usr/bin/env bash
set -euo pipefail

revision="${1:?Usage: github_deploy.sh COMMIT_SHA}"
deploy_base="${DECKVIEW_DEPLOY_BASE:-/srv/deckview}"
repository_url="${DECKVIEW_REPOSITORY_URL:-https://github.com/Zulut30/deckview-telegram-bot.git}"
services="${DECKVIEW_DEPLOY_SERVICES:-deckview-bot deckview-web deckview-worker}"
healthcheck_url="${DECKVIEW_HEALTHCHECK_URL:-http://127.0.0.1:8080/deckview-api/v1/health}"

if [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Deployment revision must be a full Git commit SHA." >&2
  exit 2
fi

if [[ "$deploy_base" != /* || "$deploy_base" == "/" || \
      "$deploy_base" == "/home" || "$deploy_base" =~ ^/home/[^/]+/?$ ]]; then
  echo "Unsafe DECKVIEW_DEPLOY_BASE: $deploy_base" >&2
  exit 2
fi

releases_dir="$deploy_base/releases"
shared_dir="$deploy_base/shared"
current_link="$deploy_base/current"
release_dir="$releases_dir/$revision"
previous_release=""

mkdir -p "$releases_dir" "$shared_dir"
if [[ ! -f "$shared_dir/.env" ]]; then
  echo "Missing $shared_dir/.env; complete the one-time bootstrap first." >&2
  exit 3
fi
if [[ -L "$current_link" ]]; then
  previous_release="$(readlink -f "$current_link")"
fi

if [[ ! -d "$release_dir/.git" ]]; then
  if [[ -e "$release_dir" ]]; then
    echo "Release path already exists but is not a Git checkout: $release_dir" >&2
    exit 4
  fi
  git clone --quiet --filter=blob:none "$repository_url" "$release_dir"
fi
git -C "$release_dir" fetch --quiet origin "$revision"
git -C "$release_dir" checkout --quiet --detach "$revision"

link_shared_directory() {
  local relative_path="$1"
  local packaged_path="$release_dir/$relative_path"
  local shared_path="$shared_dir/$relative_path"
  mkdir -p "$(dirname "$packaged_path")" "$shared_path"
  if [[ -d "$packaged_path" && ! -L "$packaged_path" ]]; then
    if [[ -f "$packaged_path/.gitkeep" ]] && \
       [[ "$(find "$packaged_path" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ]]; then
      mv "$packaged_path" "$packaged_path.repository-placeholder"
    else
      rmdir "$packaged_path" 2>/dev/null || {
        echo "Refusing to replace non-empty packaged directory: $packaged_path" >&2
        exit 5
      }
    fi
  fi
  ln -sfn "$shared_path" "$packaged_path"
}

ln -sfn "$shared_dir/.env" "$release_dir/.env"
for relative_path in cache cards library tmp_decks static/generated user_assets/backgrounds user_assets/logos; do
  link_shared_directory "$relative_path"
done

python3 -m venv "$release_dir/.venv"
"$release_dir/.venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$release_dir/.venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$release_dir/requirements.txt"

(
  cd "$release_dir"
  TOKEN=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi \
  BATTLE_NET_TOKEN=test-token \
  DASHBOARD_SECRET=test-secret-for-deploy \
  WEB_DATABASE_PATH="$release_dir/deploy-test.db" \
    .venv/bin/python -m unittest discover -v
  TOKEN=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi \
  BATTLE_NET_TOKEN=test-token \
  DASHBOARD_SECRET=test-secret-for-deploy \
  WEB_DATABASE_PATH="$release_dir/deploy-imports.db" \
    .venv/bin/python scripts/check_modular_imports.py
  .venv/bin/python scripts/render_regression_decks.py \
    --output-dir "$release_dir/artifacts/reno-regression"
)

next_link="$deploy_base/.current-$revision"
ln -sfn "$release_dir" "$next_link"
mv -Tf "$next_link" "$current_link"

restart_services() {
  # shellcheck disable=SC2086
  sudo systemctl restart $services
}

rollback() {
  if [[ -n "$previous_release" && -d "$previous_release" ]]; then
    rollback_link="$deploy_base/.rollback-$revision"
    ln -sfn "$previous_release" "$rollback_link"
    mv -Tf "$rollback_link" "$current_link"
    restart_services || true
    echo "Deployment rolled back to $previous_release" >&2
  fi
}
trap rollback ERR

restart_services
for attempt in 1 2 3 4 5; do
  if curl --fail --silent --show-error --max-time 5 "$healthcheck_url" >/dev/null; then
    trap - ERR
    echo "Deployed $revision; health check passed on attempt $attempt."
    exit 0
  fi
  sleep 2
done

echo "Health check failed after deploying $revision" >&2
exit 6
