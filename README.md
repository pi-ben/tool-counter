# Tool Usage Analyzer — Fusion 360 Script

Scans your most recently-modified Fusion 360 cloud project files, counts how often each tool number (T1–T300) appears in CAM operations, and outputs a CSV report highlighting:

- **Most-used tools** (should stay at low numbers)
- **Low-usage tools** (candidates for moving to higher numbers)
- **Unused tool number slots** (free slots available)

---

## Installation

Fusion 360 scripts live in a specific folder. Copy the entire `ToolUsageAnalyzer` folder (the one containing `ToolUsageAnalyzer.py`) there:

| Platform | Scripts folder path |
|----------|---------------------|
| Windows  | `%APPDATA%\Autodesk\Autodesk Fusion 360\API\Scripts\` |
| macOS    | `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/Scripts/` |

The result should look like:
```
…/API/Scripts/ToolUsageAnalyzer/ToolUsageAnalyzer.py
```

---

## Running the Script

1. Open **Fusion 360**.
2. Go to **Tools ▸ Add-ins ▸ Scripts and Add-ins** (or press `Shift+S`).
3. Under the **Scripts** tab, select **ToolUsageAnalyzer** and click **Run**.

The script will show a progress dialog while it:
1. Indexes all projects on your active hub.
2. Sorts the design files by last-modified date.
3. Opens each of the most recent N files (invisible, in the background).
4. Reads every CAM operation's assigned tool number.
5. Closes each file and moves to the next.
6. Writes the CSV report.

---

## Configuration (top of `ToolUsageAnalyzer.py`)

| Variable | Default | Description |
|---|---|---|
| `MAX_FILES_TO_SCAN` | `50` | How many of the most recently-modified files to open |
| `MIN_TOOL_NUMBER` | `1` | Lowest tool number to track |
| `MAX_TOOL_NUMBER` | `300` | Highest tool number to track |
| `OUTPUT_FILE` | `~/Desktop/tool_usage_report.csv` | Where to save the report |
| `SCAN_ALL_HUBS` | `False` | `True` to scan all hubs (personal + team) |
| `SCAN_ALL_PROJECTS` | `True` | `False` to scan only the active project |
| `LOW_USAGE_THRESHOLD` | `2` | Tools used ≤ this many times are flagged as renumbering candidates |

---

## Output (CSV)

The report has four sections:

1. **All Tools** – sorted by total uses descending, with a "consider renumbering" flag on low-usage entries.
2. **Low-Usage Tools** – a focused list of the renumbering candidates.
3. **Unused Slots** – all tool numbers in the 1–300 range that were never seen (shown as ranges, e.g. `5-10, 15, 20-30`).
4. **Per-File Breakdown** – every scanned file with its list of tools and use counts.

---

## Performance Notes

- Opening a file takes 5–30 seconds depending on file complexity and connection speed.
- For 50 files, expect the scan to take **5–25 minutes**.
- You can reduce `MAX_FILES_TO_SCAN` for a faster (but less comprehensive) result.
- The script skips files that contain no Manufacturing (CAM) data.
- If a file is already open in Fusion 360, the script reuses the open document without closing it.
# tool-counter
