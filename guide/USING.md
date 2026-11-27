# Using MEL Evidence

Asking questions, the ready-made prompts, and how to read the answers.

## Ask questions

Just ask in plain language. Name the place and years when they matter, so the assistant filters by them:

- *"What reasons did Kenyan agriculture projects give for delays since 2020?"*
- *"Compare what the baseline and endline reports say about access to credit."*
- *"What share of households used fertiliser at endline, by gender?"*
- *"Can I compare yields between baseline and endline?"* (the assistant checks for renamed or recoded variables first)
- *"How is the maize yield indicator doing against target, and do the reports explain why?"*

### Ready-made prompts

The server includes three prompts that set out a thorough method step by step:

| Prompt | Use it for | Arguments |
|---|---|---|
| `evidence_summary` | A full evidence review: themes with cited examples, a donor-by-theme matrix, tracker figures checked against the reports, and a grade for the evidence | country, first year |
| `compare_phases` | What changed between two study phases on a topic | topic, two phases |
| `indicator_report` | One indicator against its targets, with explanations from the reports | indicator code |

In Claude Code, prompts are slash commands. **Claude Code splits arguments at spaces, so type single words only**, then give your question when Claude asks for it:

```
/mcp__mel-evidence__evidence_summary Kenya 2020
```

Typing `/` shows the available prompts. In Claude Desktop, choose prompts from the + menu and fill in each argument.

Not every assistant shows MCP prompts. If yours doesn't, the tools still work: ask in plain language, or paste the steps from `EVIDENCE_STEPS` in `src/mel_mcp/prompts.py` before your question.

### Reading the answers

- **Layout.** Answers start with a short answer of a few bullet points, put each source straight after its claim, compare tracker and report figures in a table, and end with a Sources list. You don't need to ask for this: the server tells every assistant to write this way. Assistants that can save files, such as Claude Code, also save long answers as a Markdown (`.md`) file in the folder you started them in.
- **Citations** give the title, page (where the file has page numbers), study phase, year and sample size (n), and the report's link when the manifest has one. For a PDF the link opens at the cited page in most browsers. Reports from IATI usually have no sample size.
- **Indicator figures from IATI** come with a link to the activity's page on d-portal, where the published targets and results can be checked.
- **Your own reports and figures** have no web link. A report without a link in the manifest is cited by its file in your documents folder (for example `documents folder: surveys/endline.pdf`), and a figure from your own tracker by the tracker and the row's `source`.
- **Indicator figures** are only used when they pass data checks (a real target, a reported actual, real dates, a plausible % of target, whole numbers for counts). Figures that pass but look odd, such as having no baseline or an actual exactly equal to the target, are checked against the reports, and the report's figure is used if they disagree.
- **Gaps are stated.** If the evidence doesn't cover a question, the answer should say so, rather than guess.
- **Answers vary between runs**, like any AI answer. For important questions, use the `evidence_summary` prompt, and check key figures against the cited pages.

Every tool call is logged in `logs/audit.jsonl` (who asked, which tool, with which filters, how many results). To see the latest calls:

```bash
tail -20 logs/audit.jsonl
```
