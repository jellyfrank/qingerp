#!/usr/bin/env bash
# A new database in a dedicated PostgreSQL container; no tenant volumes or credentials.
set -euo pipefail
root=${TEST_ROOT:-/tmp/qing-spreadsheet-test}
name=qing-spreadsheet-test
image=${ODOO_IMAGE:?Set ODOO_IMAGE to a verified Odoo 20 Community image}
extra=${TEST_EXTRA_MODULES:-}
solutions=${SOLUTIONS_PATH:?Set SOLUTIONS_PATH to the matching Solutions checkout}
test -d "$root/addons/qing_spreadsheet"
test -d "$solutions/mommy_base"
if ! docker network inspect "$name" >/dev/null 2>&1; then docker network create "$name" >/dev/null; fi
if ! docker container inspect qing-spreadsheet-db >/dev/null 2>&1; then
    docker run -d --name qing-spreadsheet-db --network "$name" \
        -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=qing-spreadsheet-isolated \
        -e POSTGRES_DB=postgres postgres:16-alpine >/dev/null
fi
for attempt in {1..30}; do
    docker exec qing-spreadsheet-db pg_isready -U odoo >/dev/null 2>&1 && break
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
    --db_host=qing-spreadsheet-db --db_user=odoo --db_password=qing-spreadsheet-isolated -d qing_spreadsheet_test \
    --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/qing-addons,/mnt/solutions \
    -i "qing_spreadsheet,test_qing_spreadsheet_adapter${extra:+,$extra}" -u qing_spreadsheet,test_qing_spreadsheet_adapter --test-enable --test-tags=/qing_spreadsheet,/test_qing_spreadsheet_adapter \
    --without-demo --stop-after-init --workers=0 --http-port=8098 --max-cron-threads=0
