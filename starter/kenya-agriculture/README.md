# Kenya starter pack

Real evidence to try the server with: reports and indicator data from Kenyan agriculture projects since 2020, from the [IATI Datastore](https://iatistandard.org).

- `reports.txt` is the list of links chosen for the pack, with a note on each project.
- `manifest.csv` lists each report with its project, donor, study phase, year, country and source link.
- `indicators.csv` holds the projects' targets and actuals, as their funders published them to IATI.

The reports themselves are not stored here. Most publishers don't allow their reports to be redistributed, so `mel-starter load` (run by `./setup.sh --starter`) downloads each one from its source link onto your computer. If a link has stopped working, that report is skipped. If a download or the indexing was interrupted, run `./setup.sh --starter` again: it fetches only the reports that are missing and carries on from there.

The indicator data is published to IATI under each publisher's open data licence. The reports remain the work of their publishers (the World Bank, the UK's FCDO, KfW, Sida and others); cite them, not this project, when you use their findings.

`reports.txt` lists the links chosen for the pack, one per line. To change the pack, edit that list and run `mel-starter build`. It needs a manifest and tracker made with `mel-fetch-iati` and `mel-download-docs`, because the details of each report come from there. Before writing anything, it downloads and reads every link the way a tester's computer will. If any link fails, it says which one and leaves the pack as it was.
