"""Normalize the shared 2020 China administrative snapshot to stable XML IDs."""
import argparse
import csv
import hashlib
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def build(solutions, odoo, output):
    source = solutions / 'mommy_address_china/security'
    cities, districts, subdistricts = (read_csv(source / filename) for filename in ('res.city.csv', 'res.district.csv', 'res.street.csv'))
    native_states = read_csv(odoo / 'odoo/addons/base/data/res.country.state.csv')
    state_ids = {row['name']: 'base.' + row['id'] for row in native_states if row['country_id:id'] == 'cn'}
    for rows in (cities, districts, subdistricts):
        assert len(rows) == len({row['code'] for row in rows}), 'Administrative codes must be unique'
    cities_by_name = {row['name']: row for row in cities}
    assert len(cities_by_name) == len(cities)
    district_by_code = {row['code']: row for row in districts}
    normalized_cities = {}
    city_parent_codes = {}
    province_codes = {}
    for row in cities:
        code = row['code']
        state = row['state_id']
        assert state in state_ids, state
        province_codes[state] = code[:2]
        # Province-direct counties belong in the district selector, not the city selector.
        normalized_code = code if code.endswith('00') else code[:4] + '00'
        name = row['name'] if code.endswith('00') else ('自治区直辖县级行政区划' if code.startswith('65') else '省直辖县级行政区划')
        normalized_cities.setdefault(normalized_code, {'id': 'city_' + normalized_code, 'name': name,
            'country_id/id': 'base.cn', 'state_id/id': state_ids[state], 'qing_cn_code': normalized_code})
        city_parent_codes[row['name']] = normalized_code
    result_districts = []
    for row in districts:
        parent_code = city_parent_codes[row['city_id']]
        prefix = parent_code[:2] if parent_code.endswith('0000') else parent_code[:4]
        assert row['code'].startswith(prefix), row
        result_districts.append({'id': 'district_' + row['code'], 'name': row['name'], 'code': row['code'],
                                'city_id/id': 'qing_address_cn.city_' + parent_code})
    result_subdistricts = []
    for row in subdistricts:
        district = district_by_code[row['code'][:6]]
        assert district['name'] == row['district_id'], row
        result_subdistricts.append({'id': 'subdistrict_' + row['code'], 'name': row['name'], 'code': row['code'],
                                   'district_id/id': 'qing_address_cn.district_' + district['code']})
    output.mkdir(parents=True, exist_ok=True)
    document = ET.Element('odoo')
    for state, code in sorted(province_codes.items(), key=lambda item: item[1]):
        record = ET.SubElement(document, 'record', id=state_ids[state], model='res.country.state')
        ET.SubElement(record, 'field', name='qing_cn_code').text = code
    ET.indent(document)
    ET.ElementTree(document).write(output / 'res_country_state_data.xml', encoding='utf-8', xml_declaration=True)
    write_csv(output / 'res.city.csv', ['id', 'name', 'country_id/id', 'state_id/id', 'qing_cn_code'],
              [normalized_cities[code] for code in sorted(normalized_cities)])
    write_csv(output / 'qing.address.cn.district.csv', ['id', 'name', 'code', 'city_id/id'], result_districts)
    write_csv(output / 'qing.address.cn.subdistrict.csv', ['id', 'name', 'code', 'district_id/id'], result_subdistricts)
    source_sha = subprocess.check_output(['git', '-C', str(solutions), 'rev-parse', 'HEAD'], text=True).strip()
    metadata = {'snapshot_year': 2020, 'source_module': 'mommy_address_china', 'solutions_commit': source_sha,
                'source_sha256': {filename: hashlib.sha256((source / filename).read_bytes()).hexdigest()
                                  for filename in ('res.city.csv', 'res.district.csv', 'res.street.csv')},
                'counts': {'provinces': len(province_codes), 'cities_and_direct_groups': len(normalized_cities),
                           'districts': len(result_districts), 'subdistricts': len(result_subdistricts)}}
    (output / 'source.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(metadata['counts']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solutions', type=Path, required=True)
    parser.add_argument('--odoo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.solutions, args.odoo, args.output)
