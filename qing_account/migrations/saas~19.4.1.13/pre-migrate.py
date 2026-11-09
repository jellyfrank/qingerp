def migrate(cr, version):
    cr.execute(
        """
        UPDATE qing_bank_import_profile
           SET delimiter = CASE delimiter
               WHEN ',' THEN 'comma'
               WHEN ';' THEN 'semicolon'
               WHEN E'\\t' THEN 'tab'
               ELSE delimiter
           END
         WHERE delimiter IN (',', ';', E'\\t')
        """
    )
