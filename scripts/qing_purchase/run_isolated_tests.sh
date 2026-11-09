#!/usr/bin/env bash
# Run on a Docker test host with a dedicated PostgreSQL container and network.
set -euo pipefail
root=${TEST_ROOT:?Set TEST_ROOT to the task-specific directory containing addons}
image=${ODOO_IMAGE:?Set ODOO_IMAGE to a verified Odoo 20 Community image}
solutions=${SOLUTIONS_PATH:?Set SOLUTIONS_PATH to the matching Solutions checkout}
name=qing-purchase-test
test -d "$root/addons/qing_purchase"
test -d "$root/addons/qing_base"
test -d "$solutions/mommy_base"

if ! docker network inspect "$name" >/dev/null 2>&1; then
    docker network create "$name" >/dev/null
fi
if ! docker container inspect qing-purchase-db >/dev/null 2>&1; then
    docker run -d --name qing-purchase-db --network "$name" \
        -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=qing-purchase-isolated \
        -e POSTGRES_DB=postgres postgres:16-alpine >/dev/null
fi
for attempt in {1..30}; do
    docker exec qing-purchase-db pg_isready -U odoo >/dev/null 2>&1 && break
    sleep 1
done
docker exec qing-purchase-db pg_isready -U odoo >/dev/null
if docker container inspect "$name" >/dev/null 2>&1; then
    [ "$(docker inspect "$name" --format '{{.State.Running}}')" = false ] || exit 1
    docker logs "$name" > "$root/previous.log" 2>&1
    docker rm -v "$name" >/dev/null
fi
docker run -d --name "$name" --network "$name" \
    -v "$root/addons:/mnt/qing-addons:ro" -v "$solutions:/mnt/solutions:ro" \
    --entrypoint odoo "$image" \
    --db_host=qing-purchase-db --db_user=odoo --db_password=qing-purchase-isolated \
    -d qing_purchase_test \
    --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/qing-addons,/mnt/solutions \
    -i qing_purchase -u qing_purchase --without-demo \
    --test-enable --test-tags=/qing_purchase --stop-after-init \
    --workers=0 --http-port=8097 --max-cron-threads=0
