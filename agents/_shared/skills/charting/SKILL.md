---
name: charting
description: Create dashboard charts, saved Plotly reports, or images delivered to
  Telegram and the conversation.
when_to_use: 'When the user needs a chart or graph. Read before emitting a chart
  fence, using ReportBuilder, or exporting a chart image.'
created: '2026-09-04T10:07:23Z'
source: chat
---

# Charting

Choose the output for the user's channel and request:

| Output | Use |
| --- | --- |
| `chart` fence | Inline dashboard chart: `line`, `area` or `bar`, up to 200 points and 4 series. |
| PNG/JPEG + `send_notification(image_path=...)` | Telegram photo or image attachment in the originating conversation. |
| `ReportBuilder` | Saved interactive figures, larger datasets or complex layouts. |

Use observed data and identify its units, timeframe and as-of time. Report missing
data rather than inventing values. Include exact values when they help interpret
the chart; use bullets instead of Markdown tables in Telegram.

## Dashboard chart fence

Emit JSON directly in the reply; no `run_code` call is required. This example
illustrates the schema, not observed data:

~~~
```chart
{
  "type": "line",
  "title": "Example",
  "x": "time",
  "series": [{"key": "value", "name": "Value"}],
  "data": [{"time": "12:00", "value": 10}, {"time": "16:00", "value": 12}]
}
```
~~~

- Required: `type`, `x`, `series` and `data`. `type` is `line`, `area` or `bar`;
  `title` is optional.
- Each series has `key` and `name`. Its optional `color` is `up`, `down`, `yellow`
  or a hex string.
- `x` and every series `key` must name fields in each data row. Use one field per
  series; ISO-8601 timestamps or short labels work for the x-axis.
- Downsample to at most 200 rows and select at most 4 relevant series. Use a
  saved report or image when those limits or chart types do not fit.

## Saved Plotly report

Inside `run_code`, build a Plotly `fig`, then save it with the existing API:

```python
from condor.reports import ReportBuilder

report = ReportBuilder(title="Analysis")
report.plotly(fig)
report_id = await report.save()
print({"report_id": report_id})
```

Optional sections are `report.markdown(text)` and `report.table(rows)`, where
`rows` is a list of dictionaries. `report.source("routine", "routine-name")`
records a producing routine. Saving returns a report ID; it does not deliver a
Telegram image.

## Image export and delivery

Build and export the figure with `run_code`. Save research files under
`.condor/research/` using the [research artifact standard](../../../_defaults/research-artifacts.md)
and its report template. Within the study directory, this exports an existing
`fig` and prints the absolute server-local path needed by the tool:

```python
# study is the Path of the research directory created under that standard.
image_path = (study / "assets" / "chart.png").resolve()
image_path.parent.mkdir(parents=True, exist_ok=True)
fig.write_image(str(image_path), format="png", width=1300, height=800, scale=1)
print({"image_path": str(image_path)})
```

Call the `send_notification` MCP tool with that `image_path` and optional `text`.
The path must name a local file, not a URL or Telegram file ID. File access
requires admin or explicit `code_run` permission. Use a static PNG or JPEG of at
most 5 MiB, with width + height at most 10,000 and aspect ratio at most 20.
The tool sends the photo before text and paginates long text.

Check the result for the requested destination:

| Field | Meaning |
| --- | --- |
| `image_sent` | Telegram acknowledged the photo. |
| `text_sent` | Telegram delivery of the explanation succeeded. |
| `sent` | All requested Telegram delivery succeeded. |
| `image_recorded` | The attachment was saved in the originating conversation. |
| `recorded` | A dashboard record exists; this alone does not confirm image delivery. |

Local mode can record an image without sending to Telegram. Without an
originating conversation, the image cannot be retained as a chat attachment.
Report rendering and delivery failures accurately. After a timeout or partial
success, inspect `error`, `image_error` and the delivery fields; do not blindly
resend or bypass the tool with a direct bot send.

A local path, Markdown image or saved report does not upload a Telegram photo.
After delivery, avoid repeating lengthy text already sent by the tool. Share the
research entry point with a descriptive `/research/<relative-path>/report.md`
link, prefixed with the configured dashboard origin in Telegram. Keep local
filesystem paths out of user-facing links.
