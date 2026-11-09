#!/usr/bin/env bash
# Run on the isolated development node after syncing this worktree.
set -euo pipefail
root=/tmp/qing-partner-test
name=qing-partner-test
phase=${1:-test}
case "$phase" in
  test) extra=(-u qing_partner --test-enable --test-tags=/qing_partner);;
  stock) extra=(-i sale_stock,purchase_stock,sale_purchase_stock -u qing_partner --test-enable --test-tags=/qing_partner);;
  *) exit 2;;
esac
if docker container inspect "$name" >/dev/null 2>&1; then
  [ "$(docker inspect "$name" --format '{{.State.Running}}')" = false ] || exit 1
  docker logs "$name" > "$root/previous.log" 2>&1
  docker rm "$name" >/dev/null
fi
docker run -d --name "$name" --network qing-partner-test \
  -v "$root/addons:/mnt/qing-addons:ro" \
  -v /var/lib/saas-code/tenant_qingerp_200/25/releases/015b7a73341d4efe9dccf6b064ebee9bb3b4757c:/mnt/solutions:ro \
  --entrypoint odoo ohm/saas-deploy:tenant-qingerp-200-663 \
  --db_host=qing-partner-db --db_user=odoo --db_password=qing-partner-isolated \
  -d qing_partner_test --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/qing-addons,/mnt/solutions \
  "${extra[@]}" --without-demo=all --stop-after-init --workers=0 --http-port=8097 --max-cron-threads=0
