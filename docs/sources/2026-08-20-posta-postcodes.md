# Faroese Postcode Reference

Source: https://posta.fo/en/private/send-receive/postcodes
Retrieved: 2026-08-20

Method: Transcribe every currently listed Faroese postcode once into `schema.sql`.
Assign its region from the island/region containing the listed city. Verify each
city-centre latitude and longitude against OpenStreetMap before committing.
Coordinates represent a public city centre, never a business address.
