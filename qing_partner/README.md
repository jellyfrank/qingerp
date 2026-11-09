# qing_partner

Adds company-scoped customer and supplier roles, contact-access policies, teams, and partner APIs.

Internal users may read the native contact properties definition required by
new-contact forms. This is restricted to `res.partner.properties`; it does not
grant access to unrelated property schemas. Only native system administrators
may create, modify or delete base property definitions. Portal/public access and
contact-record/company permissions remain unchanged.

Contact editors may submit unchanged native defaults when creating a contact.
Protected values differing from server defaults, injected protected context
defaults, and modifying relational commands remain blocked. Existing contacts
retain the same maintenance-field restrictions.
