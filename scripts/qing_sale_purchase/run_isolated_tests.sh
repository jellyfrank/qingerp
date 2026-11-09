#!/usr/bin/env bash
# Run on the development node after copying this worktree to the isolated addon path.
set -euo pipefail
name=qing-sale-purchase-test
image=ohm/saas-deploy:tenant-qingerp-200-618
solutions=/var/lib/saas-code/tenant_qingerp_200/25/releases/a377cb78514eaa9f34a54e450a21c1b80caf40b9
root=/tmp/qing-sale-purchase-test
phase=${1:-base}
case "$phase" in
  base) extra=(-i qing_sale_purchase);;
  stock) extra=(-i sale_stock,purchase_stock,sale_purchase_stock);;
  *) exit 2;;
esac
if docker container inspect "$name" >/dev/null 2>&1; then
  if [ "$(docker inspect "$name" --format '{{.State.Running}}')" = true ]; then
    echo 'A test process is already running' >&2
    exit 1
  fi
  docker logs "$name" > "$root/previous.log" 2>&1
  docker rm "$name" >/dev/null
fi
docker run -d --name "$name" --network qing-sale-purchase-test \
  -v "$root/addons:/mnt/qing-addons:ro" -v "$solutions:/mnt/solutions:ro" \
  --entrypoint odoo "$image" \
  --db_host=qing-sale-purchase-db --db_user=odoo --db_password=qing-isolated-test \
  -d qing_purchase_test --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/qing-addons,/mnt/solutions \
  -u qing_sale_purchase "${extra[@]}" --without-demo=all \
  --test-enable --test-tags=/qing_sale_purchase --stop-after-init \
  --workers=0 --http-port=8097 --max-cron-threads=0
