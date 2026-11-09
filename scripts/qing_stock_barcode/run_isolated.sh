#!/usr/bin/env bash
# Dedicated disposable PostgreSQL and Odoo instances; never use tenant databases.
set -euo pipefail
root=${TEST_ROOT:-/tmp/qing-barcode-test}
name=qing-barcode-test
image=${ODOO_IMAGE:?Set ODOO_IMAGE to a verified Odoo 20 Community image}
solutions=${SOLUTIONS_PATH:?Set SOLUTIONS_PATH to the verified Solutions 20.0 checkout}
test -d "$root/addons/qing_stock_barcode"
test -d "$root/addons/qing_base"
test -d "$solutions/mommy_base"
if ! docker network inspect "$name" >/dev/null 2>&1; then docker network create "$name" >/dev/null; fi
if ! docker container inspect qing-barcode-db >/dev/null 2>&1; then
    docker run -d --name qing-barcode-db --network "$name" \
        -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=qing-barcode-isolated \
        -e POSTGRES_DB=postgres postgres:16-alpine >/dev/null
fi
for attempt in {1..30}; do
    docker exec qing-barcode-db pg_isready -U odoo >/dev/null 2>&1 && break
    sleep 1
done
if docker container inspect "$name" >/dev/null 2>&1; then
    [ "$(docker inspect "$name" --format '{{.State.Running}}')" = false ] || exit 1
    docker logs "$name" > "$root/previous.log" 2>&1
    docker rm "$name" >/dev/null
fi
docker run -d --name "$name" --network "$name" \
    -v "$root/addons:/mnt/qing-addons:ro" -v "$solutions:/mnt/solutions:ro" \
    --entrypoint odoo "$image" \
    --db_host=qing-barcode-db --db_user=odoo --db_password=qing-barcode-isolated -d qing_barcode_test \
    --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/qing-addons,/mnt/solutions \
    -i qing_stock_barcode -u qing_stock_barcode --test-enable --test-tags=/qing_stock_barcode \
    --without-demo --stop-after-init --workers=0 --http-port=8096 --max-cron-threads=0
