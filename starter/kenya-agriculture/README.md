# Kenya starter pack

Real evidence to try the server with: reports and indicator data from Kenyan agriculture projects since 2020, from the [IATI Datastore](https://iatistandard.org).

- `manifest.csv` lists each report with its project, donor, study phase, year, country and source link.
- `indicators.csv` holds the projects' targets and actuals, as their funders published them to IATI.

The reports themselves are not stored here. Most publishers don't allow their reports to be redistributed, so `mel-starter load` (run by `./setup.sh --starter`) downloads each one from its source link onto your computer. If a link has stopped working, that report is skipped.

The indicator data is published to IATI under each publisher's open data licence. The reports remain the work of their publishers (the World Bank, the UK's FCDO, the African Development Bank and others); cite them, not this project, when you use their findings.

The pack is rebuilt by maintainers with `mel-starter build`, from a manifest and tracker made with `mel-fetch-iati` and `mel-download-docs`. Check that each report in `manifest.csv` still opens before committing a new version.
