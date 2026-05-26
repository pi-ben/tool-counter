#Author- Tool Usage Analyzer
#Description- Counts tool usage (T1-T300) across recent Fusion 360 project files
#             and reports which tools are heavily used vs. candidates for renumbering.

import adsk.core
import adsk.fusion
import adsk.cam
import traceback
import csv
import os
from datetime import datetime
from collections import defaultdict


# ====================================================================
# CONFIGURATION  –  edit these before running
# ====================================================================

# How many of the most recently-modified design files to open and scan
MAX_FILES_TO_SCAN = 10

# Tool number range to track
MIN_TOOL_NUMBER = 1
MAX_TOOL_NUMBER = 320

# Where to write the CSV report  (Desktop by default)
OUTPUT_FILE = os.path.join(os.path.expanduser('~'), 'Desktop', 'tool_usage_report.csv')

# Tools used this many times or fewer are flagged as "low usage"
# and show up as candidates for moving to higher tool numbers.
LOW_USAGE_THRESHOLD = 2

# ====================================================================
# END CONFIGURATION
# ====================================================================


# ------------------------------------------------------------------
# File-collection helpers
# ------------------------------------------------------------------

# Extensions that are NOT Fusion designs (machine files, drawings, etc.)
_NON_DESIGN_EXTS = {'.mch', '.pdf', '.dxf', '.dwg', '.igs', '.iges',
                    '.step', '.stp', '.stl', '.obj', '.sat', '.smt',
                    '.ipt', '.iam', '.idw', '.ipn'}


def _is_design_file(data_file):
    """Return True only for Fusion design files; filter out .mch, .pdf, etc."""
    try:
        ext = data_file.fileExtension.lower()
        if ext:
            return ext in ('f3d', 'f3z', 'f3t')
    except AttributeError:
        pass
    # Fallback: reject files whose name ends with a known non-design extension
    try:
        name = data_file.name.lower()
        return not any(name.endswith(bad) for bad in _NON_DESIGN_EXTS)
    except Exception:
        return True


def collect_items_from_folder(folder, items_list, depth=0, max_depth=12):
    """Recursively walk a DataFolder and collect Fusion design DataFiles."""
    if depth > max_depth:
        return
    try:
        data_files = folder.dataFiles
        for i in range(data_files.count):
            try:
                f = data_files.item(i)
                if _is_design_file(f):
                    items_list.append(f)
            except Exception:
                pass
        data_folders = folder.dataFolders
        for i in range(data_folders.count):
            try:
                collect_items_from_folder(data_folders.item(i), items_list, depth + 1, max_depth)
            except Exception:
                pass
    except Exception:
        pass


# ------------------------------------------------------------------
# Interactive folder selection
# ------------------------------------------------------------------

def _parse_number_list(text, max_n):
    """Parse '1,3-5,7' into a sorted list of 0-based indices."""
    indices = set()
    for part in text.split(','):
        part = part.strip()
        if '-' in part:
            try:
                lo, hi = part.split('-', 1)
                for idx in range(int(lo.strip()), int(hi.strip()) + 1):
                    if 1 <= idx <= max_n:
                        indices.add(idx - 1)
            except Exception:
                pass
        else:
            try:
                idx = int(part)
                if 1 <= idx <= max_n:
                    indices.add(idx - 1)
            except Exception:
                pass
    return sorted(indices)


def select_scan_scope(ui, hub):
    """
    Two-step interactive selection:
      1. Pick which projects to include.
      2. Pick which immediate sub-folders of those projects to scan.
    Returns a list of DataFolder objects to scan, or None if the user cancelled.
    Leave either prompt blank to include everything at that level.
    """
    projects = hub.dataProjects
    n_proj   = projects.count

    # --- Step 1: pick projects ---
    lines = ['Select projects to scan.',
             'Enter numbers (e.g. 1,3  or  2-5), or leave blank for ALL.\n']
    for i in range(n_proj):
        try:
            lines.append(f'  {i + 1:3}.  {projects.item(i).name}')
        except Exception:
            lines.append(f'  {i + 1:3}.  (project {i + 1})')
    result, cancelled = ui.inputBox('\n'.join(lines), 'Step 1 of 2 — Select Projects', '')
    if cancelled:
        return None

    proj_indices = (_parse_number_list(result, n_proj)
                   if result.strip() else list(range(n_proj)))
    selected_projects = [projects.item(i) for i in proj_indices]

    # --- Step 2: pick immediate sub-folders ---
    all_folders = []   # list of (project_name, folder_name, DataFolder)
    for project in selected_projects:
        try:
            root = project.rootFolder
            subs = root.dataFolders
            for i in range(subs.count):
                try:
                    sf = subs.item(i)
                    all_folders.append((project.name, sf.name, sf))
                except Exception:
                    pass
        except Exception:
            pass

    if not all_folders:
        # No sub-folders found — scan the project roots directly
        roots = []
        for project in selected_projects:
            try:
                roots.append(project.rootFolder)
            except Exception:
                pass
        return roots

    lines2 = ['Select folders to scan (recursive).',
              'Enter numbers (e.g. 1,4-7), or leave blank for ALL folders in selected projects.\n']
    for i, (proj, folder, _) in enumerate(all_folders):
        lines2.append(f'  {i + 1:3}.  [{proj}]  {folder}')
    result2, cancelled2 = ui.inputBox('\n'.join(lines2), 'Step 2 of 2 — Select Folders', '')
    if cancelled2:
        return None

    if result2.strip():
        folder_indices = _parse_number_list(result2, len(all_folders))
        return [all_folders[i][2] for i in folder_indices]
    else:
        return [entry[2] for entry in all_folders]


# ------------------------------------------------------------------
# CAM scanning
# ------------------------------------------------------------------

def scan_cam_product(cam):
    """
    Iterate all operations in an adsk.cam.CAM product using index-based access.
    Returns ({tool_number: use_count}, diagnostic_string).
    """
    counts = defaultdict(int)
    diag   = []
    try:
        all_ops  = cam.allOperations
        n_ops    = all_ops.count
        n_setups = cam.setups.count
        diag.append(f'{n_setups} setup(s), {n_ops} operation(s)')
        for i in range(n_ops):
            try:
                op   = all_ops.item(i)
                tool = op.tool
                if tool is not None:
                    # Read tool number via CAMParameters.itemByName
                    num = None
                    for pname in ('tool_number', 'number', 'toolNumber', 'tool-number'):
                        try:
                            p = tool.parameters.itemByName(pname)
                            if p is not None:
                                raw = p.value
                                # CAMParameter.value returns an IntegerParameterValue
                                # object; call .value again to get the plain int.
                                num = raw.value if hasattr(raw, 'value') else raw
                                diag.append(f'  op[{i}] "{op.name}": T{num} (via "{pname}")')
                                break
                        except Exception:
                            pass
                    if num is None:
                        diag.append(f'  op[{i}] "{op.name}": tool number param not found')
                    elif MIN_TOOL_NUMBER <= int(num) <= MAX_TOOL_NUMBER:
                        counts[int(num)] += 1
                    else:
                        diag.append(f'    ^ out of range ({num})')
                else:
                    diag.append(f'  op[{i}] "{op.name}": no tool assigned')
            except Exception as oe:
                diag.append(f'  op[{i}] error: {oe}')
    except Exception as e:
        diag.append(f'cam scan error: {e}')
    return counts, '\n'.join(diag)


# ------------------------------------------------------------------
# Date helpers
# ------------------------------------------------------------------

def _parse_date(item):
    """Return a sortable datetime for a DataItem (handles datetime or ISO string)."""
    try:
        d = item.dateModified
        if isinstance(d, datetime):
            return d
        if isinstance(d, str):
            return datetime.fromisoformat(d.replace('Z', '+00:00'))
    except Exception:
        pass
    return datetime.min


def _fmt_date(item):
    try:
        d = item.dateModified
        if isinstance(d, datetime):
            return d.strftime('%Y-%m-%d %H:%M')
        return str(d)
    except Exception:
        return ''


# ------------------------------------------------------------------
# CSV report writer
# ------------------------------------------------------------------

def _unused_ranges(used_set, lo, hi):
    """Summarise unused numbers as a compact range string, e.g. '5-10, 15, 20-30'."""
    ranges, start = [], None
    for n in range(lo, hi + 1):
        if n not in used_set:
            if start is None:
                start = n
            end = n
        else:
            if start is not None:
                ranges.append(str(start) if start == end else f'{start}-{end}')
                start = None
    if start is not None:
        ranges.append(str(start) if start == end else f'{start}-{end}')
    return ', '.join(ranges) if ranges else 'None'


def write_report(path, global_counts, file_counts, file_results, files_scanned):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)

        # --- Header block ---
        w.writerow(['Fusion 360 Tool Usage Analysis Report'])
        w.writerow(['Generated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
        w.writerow(['Files scanned', files_scanned])
        w.writerow(['Files with CAM data', sum(1 for r in file_results if r.get('tools'))])
        w.writerow(['Unique tools found', len(global_counts)])
        w.writerow([])

        # --- Full tool table sorted by usage ---
        w.writerow(['--- ALL TOOLS (sorted by total uses, descending) ---'])
        w.writerow(['Tool Number', 'Total Uses', 'Files Used In', 'Recommendation'])
        sorted_tools = sorted(global_counts.items(), key=lambda x: x[1], reverse=True)
        for tool_num, total_uses in sorted_tools:
            rec = 'Consider renumbering to a higher slot' if total_uses <= LOW_USAGE_THRESHOLD else ''
            w.writerow([f'T{tool_num}', total_uses, file_counts[tool_num], rec])
        w.writerow([])

        # --- Low-usage candidates ---
        low = [(n, c) for n, c in sorted_tools if c <= LOW_USAGE_THRESHOLD]
        if low:
            w.writerow([f'--- LOW-USAGE TOOLS (<= {LOW_USAGE_THRESHOLD} uses) — RENUMBERING CANDIDATES ---'])
            w.writerow(['Tool Number', 'Total Uses', 'Files Used In'])
            for tool_num, total_uses in low:
                w.writerow([f'T{tool_num}', total_uses, file_counts[tool_num]])
            w.writerow([])

        # --- Unused tool numbers ---
        unused_str = _unused_ranges(set(global_counts.keys()), MIN_TOOL_NUMBER, MAX_TOOL_NUMBER)
        w.writerow([f'--- UNUSED TOOL NUMBER SLOTS ({MIN_TOOL_NUMBER}–{MAX_TOOL_NUMBER}) ---'])
        w.writerow(['Free slots:', unused_str])
        w.writerow([])

        # --- Per-file breakdown ---
        w.writerow(['--- PER-FILE BREAKDOWN ---'])
        w.writerow(['File Name', 'Date Modified', 'CAM Tools Used (count)', 'Error'])
        for r in file_results:
            tools_str = '  '.join(f'T{k}({v}x)' for k, v in sorted(r.get('tools', {}).items()))
            w.writerow([r['name'], r.get('date', ''), tools_str, r.get('error', '')])


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

def run(context):
    ui = None
    progress = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        progress = ui.createProgressDialog()
        progress.isCancelButtonShown = True
        progress.show('Tool Usage Analyzer', 'Loading project list…', 0, 100, 0)
        adsk.doEvents()

        # ----------------------------------------------------------
        # 1. Interactive folder selection
        # ----------------------------------------------------------
        progress.hide()   # hide while dialogs are open

        hub = app.data.activeHub
        selected_folders = select_scan_scope(ui, hub)
        if selected_folders is None:   # user hit Cancel
            return
        if not selected_folders:
            ui.messageBox('No folders were selected. Nothing to scan.')
            return

        # ----------------------------------------------------------
        # 2. Collect design files from selected folders
        # ----------------------------------------------------------
        progress.show('Tool Usage Analyzer', 'Collecting files…', 0, 100, 0)
        adsk.doEvents()

        all_items = []
        for folder in selected_folders:
            if progress.wasCancelled:
                break
            try:
                progress.message = f'Indexing: {folder.name}'
                adsk.doEvents()
            except Exception:
                pass
            collect_items_from_folder(folder, all_items)

        if progress.wasCancelled:
            progress.hide()
            return

        if not all_items:
            progress.hide()
            ui.messageBox('No Fusion design files were found in the selected folders.')
            return

        # ----------------------------------------------------------
        # 3. Sort by most-recently modified, take the top N
        # ----------------------------------------------------------
        all_items.sort(key=_parse_date, reverse=True)
        files_to_scan = all_items[:MAX_FILES_TO_SCAN]
        total = len(files_to_scan)

        # ----------------------------------------------------------
        # 3. Open each file and scan for CAM tool data
        # ----------------------------------------------------------
        global_tool_counts = defaultdict(int)   # tool_num  → total uses across all files
        tool_file_counts   = defaultdict(int)   # tool_num  → how many distinct files used it
        file_results       = []

        for i, item in enumerate(files_to_scan):
            if progress.wasCancelled:
                break
            progress.progressValue = 20 + int((i / total) * 75)
            progress.message = f'Scanning {i + 1}/{total}: {item.name}'
            adsk.doEvents()

            doc          = None
            opened_by_us = False
            result = {
                'name':  item.name,
                'date':  _fmt_date(item),
                'tools': {},
                'error': ''
            }

            try:
                # Re-use the document if it is already open
                for idx in range(app.documents.count):
                    try:
                        open_doc = app.documents.item(idx)
                        if open_doc.dataFile and open_doc.dataFile.id == item.id:
                            doc = open_doc
                            break
                    except Exception:
                        pass

                if doc is None:
                    doc          = app.documents.open(item, False)  # invisible
                    opened_by_us = True
                    adsk.doEvents()

                # Show all available product types for diagnostics
                prod_types = []
                for pi in range(doc.products.count):
                    try:
                        prod_types.append(doc.products.item(pi).objectType)
                    except Exception:
                        pass

                cam_product = doc.products.itemByProductType('CAMProductType')
                if cam_product:
                    cam              = adsk.cam.CAM.cast(cam_product)
                    counts, cam_diag = scan_cam_product(cam)
                    result['tools']  = dict(counts)
                    result['error']  = cam_diag
                    for tool_num, count in counts.items():
                        global_tool_counts[tool_num] += count
                        tool_file_counts[tool_num]   += 1
                else:
                    result['error'] = (f'No CAM product found. '
                                       f'Products: {prod_types}')

            except Exception as e:
                result['error'] = str(e)[:300]
            finally:
                if opened_by_us and doc is not None:
                    try:
                        doc.close(False)   # close without saving
                    except Exception:
                        pass

            file_results.append(result)

        # ----------------------------------------------------------
        # 4. Write the CSV report
        # ----------------------------------------------------------
        progress.progressValue = 97
        progress.message = 'Writing report…'
        adsk.doEvents()

        files_with_cam = sum(1 for r in file_results if r['tools'])

        if not global_tool_counts:
            progress.hide()
            per_file = '\n\n'.join(
                f'{r["name"]}:\n  {r["error"] or "(no info)"}'
                for r in file_results
            )
            ui.messageBox(
                f'Scan complete.  {len(file_results)} file(s) checked.\n'
                f'No CAM tool data was found.\n\n'
                f'Per-file diagnostics:\n{per_file}',
                'Tool Usage Analyzer'
            )
            return

        write_report(OUTPUT_FILE, global_tool_counts, tool_file_counts, file_results, total)
        progress.progressValue = 100
        progress.hide()

        # ----------------------------------------------------------
        # 5. Show summary dialog
        # ----------------------------------------------------------
        sorted_tools = sorted(global_tool_counts.items(), key=lambda x: x[1], reverse=True)
        top_10       = sorted_tools[:10]
        low_usage    = [n for n, c in sorted_tools if c <= LOW_USAGE_THRESHOLD]

        top_lines = '\n'.join(f'  T{num:>3}:  {cnt} use(s)  across {tool_file_counts[num]} file(s)'
                              for num, cnt in top_10)

        low_str = ', '.join(f'T{n}' for n in low_usage[:20])
        if len(low_usage) > 20:
            low_str += f'  …(+{len(low_usage) - 20} more)'

        msg = (
            f'Tool Usage Analysis Complete\n'
            f'{"=" * 42}\n'
            f'Files scanned:         {len(file_results)}\n'
            f'Files with CAM data:   {files_with_cam}\n'
            f'Unique tools tracked:  {len(global_tool_counts)}\n'
            f'\n'
            f'Top {len(top_10)} Most-Used Tools:\n{top_lines}\n'
        )
        if low_usage:
            msg += (
                f'\n'
                f'Low-Usage Tools (<= {LOW_USAGE_THRESHOLD} uses)  –  renumbering candidates:\n'
                f'  {low_str}\n'
            )
        msg += f'\nFull report saved to:\n  {OUTPUT_FILE}'

        ui.messageBox(msg, 'Tool Usage Analyzer', 0, 2)

    except Exception:
        if ui:
            ui.messageBox(f'Unexpected error:\n\n{traceback.format_exc()}', 'Tool Usage Analyzer – Error')
    finally:
        if progress is not None:
            try:
                progress.hide()
            except Exception:
                pass
