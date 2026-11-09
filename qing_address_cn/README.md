# Qing China Addresses

Adds linked Chinese address selection to contacts, child contacts, delivery and invoice addresses, and company addresses.

When a contact's country is China, its form shows country, province, city, district/county, optional subdistrict/township, detailed address and postcode in that order. Province and city share one row. Other countries keep their native address layout. The layout follows the contact being edited, independently of the current company's country.

Province, city, district and subdistrict selections use administrative codes and filter each following level. Changing a parent clears incompatible selections. Imports and ORM writes validate the same hierarchy. Printed addresses include districts and subdistricts without repeating municipality names.

The mainland administrative catalog is a 2020 snapshot with 31 provinces, 341 city-level records, 2,843 districts and 41,001 subdistricts. Hong Kong, Macao and Taiwan continue to use native free-text address entry. Postcodes remain user supplied. Existing ambiguous addresses are retained for manual review.

The module is optional and depends on `qing_base` and native `base_address_extended`; it does not require Chinese accounting localization. Ordinary users can read the catalog, while administrators maintain it.
