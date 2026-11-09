#!/usr/bin/env bash
set -euo pipefail
root=${TEST_ROOT:-/tmp/qing-spreadsheet-test}
image=${ODOO_IMAGE:?Set ODOO_IMAGE}
solutions=${SOLUTIONS_PATH:?Set SOLUTIONS_PATH}
# Separate from the stop-after-init test runner; loopback binding only.
docker run -d --name qing-spreadsheet-ui --network qing-spreadsheet-test \
    -p 127.0.0.1:18998:8098 -v "$root/addons:/mnt/qing-addons:ro" -v "$solutions:/mnt/solutions:ro" \
    --entrypoint odoo "$image" \
    --db_host=qing-spreadsheet-db --db_user=odoo --db_password=qing-spreadsheet-isolated -d qing_spreadsheet_test \
    --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/qing-addons,/mnt/solutions \
    --workers=0 --http-interface=0.0.0.0 --http-port=8098 --max-cron-threads=0 --db-filter='^qing_spreadsheet_test$' --no-database-list
