#!/usr/bin/env bash
# Use only synthetic databases in a dedicated Docker network and PostgreSQL server.
set -euo pipefail

root=${TEST_ROOT:?Set TEST_ROOT to the task-specific directory containing addons}
image=${ODOO_IMAGE:?Set ODOO_IMAGE to a verified Odoo 20 Community image}
solutions=${SOLUTIONS_PATH:?Set SOLUTIONS_PATH to the matching Solutions checkout}
database=${TEST_DB:-qing_rental_test}
name=qing-rental-test
db_container=qing-rental-db
owner=qing-rental
password=qing-rental-isolated

case "$database" in
    qing_rental_*) ;;
    *) echo 'TEST_DB must start with qing_rental_.' >&2; exit 1 ;;
esac
[[ "$database" =~ ^[a-zA-Z0-9_]+$ ]] || exit 1
test -d "$root/addons"
test -d "$solutions/mommy_base"
docker image inspect "$image" >/dev/null

check_owner() {
    local kind=$1 object=$2
    [ "$(docker "$kind" inspect "$object" --format '{{index .Labels "com.qingerp.test"}}' 2>/dev/null)" = "$owner" ]
}

check_container_owner() {
    [ "$(docker container inspect "$1" --format '{{index .Config.Labels "com.qingerp.test"}}')" = "$owner" ] || {
        echo "Refusing to change a container not owned by this test: $1" >&2
        exit 1
    }
}

if docker network inspect "$name" >/dev/null 2>&1; then
    check_owner network "$name" || {
        echo 'Refusing to reuse a network not owned by this test.' >&2
        exit 1
    }
else
    docker network create --internal --label "com.qingerp.test=$owner" "$name" >/dev/null
fi

if docker container inspect "$db_container" >/dev/null 2>&1; then
    check_container_owner "$db_container"
    if [ "$(docker inspect "$db_container" --format '{{.State.Running}}')" != true ]; then
        docker start "$db_container" >/dev/null
    fi
else
    docker run -d --name "$db_container" --network "$name" \
        --label "com.qingerp.test=$owner" \
        -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD="$password" \
        -e POSTGRES_DB=postgres postgres:16-alpine >/dev/null
fi
for attempt in {1..30}; do
    docker exec "$db_container" pg_isready -h 127.0.0.1 -U odoo -d postgres >/dev/null 2>&1 && break
    sleep 1
done
docker exec "$db_container" pg_isready -h 127.0.0.1 -U odoo -d postgres >/dev/null

if [ "${PREPARE_ONLY:-0}" = 1 ]; then
    echo 'Dedicated qing-rental-test network and qing-rental-db PostgreSQL are ready.'
    exit 0
fi

test -d "$root/addons/qing_rental"
test -d "$root/addons/qing_base"
if docker container inspect "$name" >/dev/null 2>&1; then
    check_container_owner "$name"
    [ "$(docker inspect "$name" --format '{{.State.Running}}')" = false ] || {
        echo 'A rental test is already running.' >&2
        exit 1
    }
    docker logs "$name" > "$root/previous.log" 2>&1
    docker rm -v "$name" >/dev/null
fi

docker run -d --name "$name" --network "$name" \
    --label "com.qingerp.test=$owner" \
    -v "$root/addons:/mnt/qing-addons:ro" -v "$solutions:/mnt/solutions:ro" \
    --entrypoint odoo "$image" \
    --db_host="$db_container" --db_user=odoo --db_password="$password" \
    -d "$database" \
    --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/qing-addons,/mnt/solutions \
    -i qing_rental -u qing_rental --without-demo \
    --test-enable --test-tags=/qing_rental --stop-after-init \
    --workers=0 --http-port=8098 --max-cron-threads=0 >/dev/null

result=$(docker wait "$name")
docker logs "$name" > "$root/test.log" 2>&1
tail -n 40 "$root/test.log"
echo "Rental test exit code: $result; full log: $root/test.log"
exit "$result"
