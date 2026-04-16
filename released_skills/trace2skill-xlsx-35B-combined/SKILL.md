---
name: xlsx
description: "Comprehensive spreadsheet creation, editing, and analysis with support for formulas, formatting, data analysis, and visualization. When Qwen-Agent needs to work with spreadsheets (.xlsx, .xlsm, .csv, .tsv, etc) for: (1) Creating new spreadsheets with formulas and formatting, (2) Reading or analyzing data, (3) Modify existing spreadsheets while preserving formulas, (4) Data analysis and visualization in spreadsheets, or (5) Recalculating formulas"
license: Proprietary. LICENSE.txt has complete terms
---

# Requirements for Outputs

## All Excel files

### Zero Formula Errors
- Every Excel model MUST be delivered with ZERO formula errors (#REF!, #DIV/0!, #VALUE!, #N/A, #NAME?)

### Preserve Existing Templates (when updating templates)
- Study and EXACTLY match existing format, style, and conventions when modifying files
- Never impose standardized formatting on files with established patterns
- Existing template conventions ALWAYS override these guidelines

## Financial models

### Color Coding Standards
Unless otherwise stated by the user or existing template

#### Industry-Standard Color Conventions
- **Blue text (RGB: 0,0,255)**: Hardcoded inputs, and numbers users will change for scenarios
- **Black text (RGB: 0,0,0)**: ALL formulas and calculations
- **Green text (RGB: 0,128,0)**: Links pulling from other worksheets within same workbook
- **Red text (RGB: 255,0,0)**: External links to other files
- **Yellow background (RGB: 255,255,0)**: Key assumptions needing attention or cells that need to be updated

### Number Formatting Standards

#### Required Format Rules
- **Years**: Format as text strings (e.g., "2024" not "2,024")
- **Currency**: Use $#,##0 format; ALWAYS specify units in headers ("Revenue ($mm)")
- **Zeros**: Use number formatting to make all zeros "-", including percentages (e.g., "$#,##0;($#,##0);-")
- **Percentages**: Default to 0.0% format (one decimal)
- **Multiples**: Format as 0.0x for valuation multiples (EV/EBITDA, P/E)
- **Negative numbers**: Use parentheses (123) not minus -123

### Formula Construction Rules

#### Assumptions Placement
- Place ALL assumptions (growth rates, margins, multiples, etc.) in separate assumption cells
- Use cell references instead of hardcoded values in formulas
- Example: Use =B5*(1+$B$6) instead of =B5*1.05

#### Formula Error Prevention
- Verify all cell references are correct
- Check for off-by-one errors in ranges
- Ensure consistent formulas across all projection periods
- Test with edge cases (zero values, negative numbers)
- Verify no unintended circular references

#### Documentation Requirements for Hardcodes
- Comment or in cells beside (if end of table). Format: "Source: [System/Document], [Date], [Specific Reference], [URL if applicable]"
- Examples:
  - "Source: Company 10-K, FY2024, Page 45, Revenue Note, [SEC EDGAR URL]"
  - "Source: Company 10-Q, Q2 2025, Exhibit 99.1, [SEC EDGAR URL]"
  - "Source: Bloomberg Terminal, 8/15/2025, AAPL US Equity"
  - "Source: FactSet, 8/20/2025, Consensus Estimates Screen"

# XLSX creation, editing, and analysis

## Overview

A user may ask you to create, edit, or analyze the contents of an .xlsx file. You have different tools and workflows available for different tasks.



### Pre-Processing Inspection
Before making ANY modifications:
- Load workbook and list all sheets: `wb.sheetnames`
- Read column headers and identify exact column positions (e.g., "Column G = Ship Date")
- Inspect target sheet structure: Read first few rows to understand layout
- Identify key columns/rows: Note headers, data start row, column positions
- Check for empty cells or special markers: Look for "**", empty strings, etc.
- Confirm destination status: Is it empty? Already has data?
- Understand data patterns and formats in each sheet
- Test 2-3 sample cell references before building full transformations
- For large files: Use `read_only=True` mode to process sequentially without loading entire file into memory
- Print full grid before modifying: Iterate through all cells with non-null values to map exact table locations
- Count rows/columns used: Ensure your target range matches actual data boundaries## Important Requirements

**LibreOffice Required for Formula Recalculation**: You can assume LibreOffice is installed for recalculating formula values using the `recalc.py` script. The script automatically configures LibreOffice on first run

## Reading and analyzing data

### Data analysis with pandas
For data analysis, visualization, and basic operations, use **pandas** which provides powerful data manipulation capabilities:

```python
import pandas as pd

# Read Excel
df = pd.read_excel('file.xlsx')  # Default: first sheet
all_sheets = pd.read_excel('file.xlsx', sheet_name=None)  # All sheets as dict

# Analyze
df.head()      # Preview data
df.info()      # Column info
df.describe()  # Statistics

# Write Excel
df.to_excel('output.xlsx', index=False)
```

## Excel File Workflows

## CRITICAL: Use Formulas, Not Hardcoded Values

**Always use Excel formulas instead of calculating values in Python and hardcoding them.** This ensures the spreadsheet remains dynamic and updateable.

### ❌ WRONG - Hardcoding Calculated Values
```python
# Bad: Calculating in Python and hardcoding result
total = df['Sales'].sum()
sheet['B10'] = total  # Hardcodes 5000

# Bad: Computing growth rate in Python
growth = (df.iloc[-1]['Revenue'] - df.iloc[0]['Revenue']) / df.iloc[0]['Revenue']
sheet['C5'] = growth  # Hardcodes 0.15

# Bad: Python calculation for average
avg = sum(values) / len(values)
sheet['D20'] = avg  # Hardcodes 42.5
```

### ✅ CORRECT - Using Excel Formulas
```python
# Good: Let Excel calculate the sum
sheet['B10'] = '=SUM(B2:B9)'

# Good: Growth rate as Excel formula
sheet['C5'] = '=(C4-C2)/C2'

# Good: Average using Excel function
sheet['D20'] = '=AVERAGE(D2:D19)'
```

This applies to ALL calculations - totals, percentages, ratios, differences, etc. The spreadsheet should be able to recalculate when source data changes.



### Complete Formula Workflow (MANDATORY)

When creating/modifying Excel files with formulas, follow this exact sequence:

1. **Create/Modify**: Use openpyxl to add formulas as strings
2. **Save**: Write workbook to disk
3. **Verify File Created**: Confirm file exists and has reasonable size (>1KB for non-empty files)
4. **Recalculate**: Run `python recalc.py <filename>` to evaluate formulas
5. **Check Errors**: Review recalc.py JSON output for #REF!, #DIV/0!, etc.
6. **Final Verification**: Open file and spot-check key cells match expected values

**DO NOT skip any step** - especially skipping recalc.py or verification.

### Answer Position Interpretation
Respect answer_position boundaries while filling logically linked adjacent columns. Example: If answer_position='Output!B11:B17' but task requires copying 'columns A and B', fill entire relevant area (A11:B17) to maintain row alignment. Check if multiple columns are logically linked versus strictly adhering to named boundaries.

### Data Consolidation Pattern
When consolidating filtered/skipped data:
1. First write None/empty values to ALL destination cells to clear old content
2. Then write consolidated dataset consecutively
3. This prevents orphaned data when non-matching rows are skipped

### Clearing Stale Data Before Repopulation
When updating existing sheets (especially summary/list sheets), always delete old data rows before inserting new entries:

```python
# Delete old rows first (e.g., rows 3+) before writing new data
delete_rows = list(range(3, sheet.max_row + 1))  # Adjust range as needed
for row_num in sorted(delete_rows, reverse=True):
    sheet.delete_rows(row_num)

# Now insert fresh data starting from clean slate
sheet['A3'] = 'New Header'
sheet.append(['Row', 'of', 'new', 'data'])
```

This prevents orphaned records from previous iterations and ensures the sheet accurately reflects current source data.## Common Workflow
1. **Choose tool**: pandas for data, openpyxl for formulas/formatting
2. **Create/Load**: Create new workbook or load existing file
3. **Modify**: Add/edit data, formulas, and formatting
4. **Save**: Write to file
5. **Recalculate formulas (MANDATORY IF USING FORMULAS)**: Use the recalc.py script
   ```bash
   python recalc.py output.xlsx
   ```
6. **Verify and fix any errors**: 
   - The script returns JSON with error details
   - If `status` is `errors_found`, check `error_summary` for specific error types and locations
   - Fix the identified errors and recalculate again
   - Common errors to fix:
     - `#REF!`: Invalid cell references
     - `#DIV/0!`: Division by zero
     - `#VALUE!`: Wrong data type in formula
     - `#NAME?`: Unrecognized formula name

### Creating new Excel files

```python
# Using openpyxl for formulas and formatting
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

wb = Workbook()
sheet = wb.active

# Add data
sheet['A1'] = 'Hello'
sheet['B1'] = 'World'
sheet.append(['Row', 'of', 'data'])

# Add formula
sheet['B2'] = '=SUM(A1:A10)'

# Formatting
sheet['A1'].font = Font(bold=True, color='FF0000')
sheet['A1'].fill = PatternFill('solid', start_color='FFFF00')
sheet['A1'].alignment = Alignment(horizontal='center')

# Column width
sheet.column_dimensions['A'].width = 20

wb.save('output.xlsx')
```



### Row Deletion Safety
When deleting multiple rows programmatically:
- **ALWAYS delete rows in descending index order** (highest row number first)
- Deleting top-to-bottom causes index-shifting that invalidates remaining target indices
- Example: If deleting rows 5, 12, 20, delete row 20 first, then 12, then 5
- Sort deletion targets in reverse before executing deletions

Example:
```python
# WRONG - Top to bottom (misses rows)
for i in range(10, 20):
    sheet.delete_rows(i)  # Fails - indices shift

# CORRECT - Bottom to top (preserves indices)
for i in range(20, 10, -1):  # 20 down to 11
    sheet.delete_rows(i)
```

Alternatively, sort descending and delete in reverse:
```python
rows_to_delete = [10, 5, 2]  # Will delete in order: 10, 5, 2
for row in reversed(rows_to_delete):
    sheet.delete_rows(row)
```

This applies to any row-based filtering or deletion operation.

### Clearing Cell Values
To remove cell values without leaving artifacts, assign `None` to `cell.value`.
- Do NOT use empty string `""` which may behave differently depending on downstream systems
- Example: `cell.value = None` removes the cell content while preserving row/column structure

### Selective Cell Operations
When selectively keeping or clearing specific cells based on conditions:
- **Use openpyxl iteration**, not bulk find/replace operations
- Find/replace struggles with operators like '<>' and complex conditions
- Loop through rows and conditionally set `cell.value = None` for true cell clearing
- Example:
  ```python
  for row in sheet.iter_rows():
      for cell in row:
          if cell.value == 'Comments':  # case-sensitive check
              cell.value = None  # proper cell clearing
  ```### Editing existing Excel files

```python
# Using openpyxl to preserve formulas and formatting
from openpyxl import load_workbook

# Load existing file
wb = load_workbook('existing.xlsx')
sheet = wb.active  # or wb['SheetName'] for specific sheet

# Working with multiple sheets
for sheet_name in wb.sheetnames:
    sheet = wb[sheet_name]
    print(f"Sheet: {sheet_name}")

# Modify cells
sheet['A1'] = 'New Value'
sheet.insert_rows(2)  # Insert row at position 2
sheet.delete_cols(3)  # Delete column 3

# Add new sheet
new_sheet = wb.create_sheet('NewSheet')
new_sheet['A1'] = 'Data'

wb.save('modified.xlsx')
```

## Recalculating formulas

Excel files created or modified by openpyxl contain formulas as strings but not calculated values. Use the provided `recalc.py` script to recalculate formulas:

```bash
python recalc.py <excel_file> [timeout_seconds]
```

Example:
```bash
python recalc.py output.xlsx 30
```

The script:
- Automatically sets up LibreOffice macro on first run
- Recalculates all formulas in all sheets
- Scans ALL cells for Excel errors (#REF!, #DIV/0!, etc.)
- Returns JSON with detailed error locations and counts
- Works on both Linux and macOS

## Formula Verification Checklist

Quick checks to ensure formulas work correctly:

### Essential Verification
- [ ] **Test 2-3 sample references**: Verify they pull correct values before building full model
- [ ] **Column mapping**: Confirm Excel columns match (e.g., column 64 = BL, not BK)
- [ ] **Row offset**: Remember Excel rows are 1-indexed (DataFrame row 5 = Excel row 6)

- [ ] **Test tool execution**: Run a simple `echo "test"` command first to confirm bash tool works before complex operations
- [ ] **File created successfully**: Verify file exists and has content before closing task
- [ ] **recalc.py executed**: Ran calculator script after saving formulas
- [ ] **Column mapping**: Always convert letter columns to numbers (A=1, B=2, F=6, I=9). Use `openpyxl.utils.get_column_letter()` and `openpyxl.utils.column_index_from_string()` for conversion. Verify with `cell.row`/`cell.column` attributes.

### Common Pitfalls
- [ ] **NaN handling**: Check for null values with `pd.notna()`
- [ ] **Far-right columns**: FY data often in columns 50+ 
- [ ] **Multiple matches**: Search all occurrences, not just first
- [ ] **Division by zero**: Check denominators before using `/` in formulas (#DIV/0!)
- [ ] **Wrong references**: Verify all cell references point to intended cells (#REF!)
- [ ] **Cross-sheet references**: Use correct format (Sheet1!A1) for linking sheets

- [ ] **Tied maximums**: Use equality comparison (`value == max_value`) not greater-than to classify all tied entries identically
- [ ] **Duplicate keys**: When merging/lookup tables have duplicate keys, collect ALL matches in lists before spreading across columns. Maintain count dictionary tracking removals - only skip on first encounter (count == 0).
- [ ] **Sign verification**: For running balances, manually calculate first row with known values before applying formula to entire column
- [ ] **Business semantics check**: Confirm whether each input increases or decreases the target value based on domain context
- [ ] **Search direction for "closest match before"**: When finding values "above" or "before" a reference row expecting the most recent match, scan backward from the reference point (e.g., `range(target_row-1, 0, -1)` not `range(1, target_row)`). Forward scanning returns the first match; backward scanning returns the closest one.
- [ ] **Case-sensitive matching**: SUMIF/SUMIFS are case-insensitive by default. If task requires case-sensitive matching, use: `=SUMPRODUCT(--EXACT(range,criteria),values)`
- [ ] **Deletion vs Extraction**: Distinguish between removing unwanted data versus extracting specific records - some tasks require keeping only matching entries after filtering operations, not just what remains after deletions
- [ ] **Empty/NaN rows**: After filtering, check if intermediate empty/NaN rows should be preserved or removed based on final expected structure
- [ ] **Column detection**: Verify detected column index matches expected position before transformation (use assert or print statement)
- [ ] **Remove non-numeric characters**: Use regex `re.sub(r'[^\d.]', '', val_str)` to extract digits and decimals from mixed content (currency symbols, letters, etc.)
- [ ] **String escaping**: For double quotes in formulas, use `chr(34)` instead of `\"` to avoid escaping complications (e.g., `sheet['A1'] = 'Replace' + chr(34) + 'with' + chr(34) + 'quotes')`

### Formula Testing Strategy
- [ ] **Start small**: Test formulas on 2-3 cells before applying broadly
- [ ] **Verify dependencies**: Check all cells referenced in formulas exist
- [ ] **Test edge cases**: Include zero, negative, and very large values

### Interpreting recalc.py Output
The script returns JSON with error details:

```json
{
  "status": "success",           // or "errors_found"
  "total_errors": 0,              // Total error count
  "total_formulas": 42,           // Number of formulas in file
  "error_summary": {              // Only present if errors found
    "#REF!": {
      "count": 2,
      "locations": ["Sheet1!B5", "Sheet1!C10"]
    }
  }
}
```

### Recalc.py Error Resolution Workflow
When errors are found:
1. Read the JSON output to identify error type and locations
2. Fix the root cause (wrong reference, division by zero, wrong data type)
3. Save the file
4. Run `python recalc.py <filename>` again until status is `success`
5. Verify total_errors = 0 before delivering the file


### Pre-Processing Pattern
- [ ] **Create lookup dictionaries first**: Build `{key: row}` dict from reference sheet before iterating target sheets for O(1) matching
- [ ] **Verify cell updates programmatically**: Extract and display final column values to confirm modifications persisted before completing task

### Manual Verification Step
After implementing logic, perform at least one complete manual spot-check:
- [ ] **Manual spot-check**: Calculate at least one complete result by hand using the original data
- [ ] **Compare to computed**: Verify your code output matches manual calculation exactly
- [ ] **Edge cases tested**: Confirm behavior with zero, negative, NaN, and text values
- [ ] **Row/column mapping**: Double-check all indices against actual spreadsheet layout
- Pick 1-2 rows with known values and calculate by hand
- Compare your computed result against the cell output
- This catches subtle bugs in range slicing, boundary logic, or index offsets
- Especially important for segment-counting, cumulative totals, and conditional logic

### Near-Zero Detection
- [ ] **Floating-point precision handling**: Use absolute value thresholds instead of exact zero checks
  - Pattern: `abs(value) <= tolerance` (e.g., `abs(value) <= 0.011` for ~0.01 values)
  - Avoid: `value == 0` which fails due to floating-point precision artifacts

### Data Comparison Verification
- [ ] **Case sensitivity**: Verify exact case matching requirements before applying logic
- [ ] **Observe sample data first**: Check actual data format to determine if case-sensitive or case-insensitive comparison is appropriate
- Don't assume lower() or case-insensitive matching is always correct

### Post-Transformation Verification
- [ ] **Verify regions intended to be preserved remain intact and unshifted**
- [ ] **Cross-check original vs output layouts for unintended changes**
- [ ] **Spot-check 2-3 sample cells in both modified and preserved areas**
- [ ] **Validate against observations**: Compare expected vs. actual cell values
- [ ] **After DELETE/INSERT operations, reload file and confirm row/column counts match expectations**
- For destructive operations (row deletion, column removal), verify output before marking complete
## Best Practices

### Library Selection
### Library Selection
- **openpyxl (PREFERRED)**: Use for ALL tasks involving formulas, formatting, or cell-level manipulation. It preserves existing formulas and structure.
- **pandas**: Only for pure data analysis where formulas don't matter. Never use for editing spreadsheets with formulas—converting to DataFrame loses them permanently.

#### Fallback Strategy
If pandas throws `ModuleNotFoundError` or C-extension errors, immediately fall back to openpyxl. Openpyxl works reliably for cell-level operations without compilation requirements.

### Working with openpyxl
- Cell indices are 1-based (row=1, column=1 refers to cell A1)
- Use `data_only=True` to read calculated values: `load_workbook('file.xlsx', data_only=True)`
- **Warning**: If opened with `data_only=True` and saved, formulas are replaced with values and permanently lost
- For large files: Use `read_only=True` for reading or `write_only=True` for writing
- Formulas are preserved but not evaluated - use recalc.py to update values


#### Reading: Formula Strings vs. Calculated Values
When opening workbooks, always distinguish between raw formula strings and their calculated results:
- `data_only=False` (default): Cells show formula strings like `=1+A9`
- `data_only=True`: Cells show calculated values (e.g., integer result)

Before any transformation, examine both modes to understand source data. Use `data_only=True` when migrating data based on conditions to get final values needed for output.

#### Loading Mode Matters
Use `data_only=False` (default) when you need to preserve workbook features like autofilters, named ranges, or conditional formatting while modifying cell values. Using `data_only=True` replaces formulas AND structural elements with calculated values - avoid unless you only need static data.

#### Color Fill Syntax
When using `PatternFill`, RGB hex codes must be prefixed with 'FF' for alpha channel (e.g., `'F2F2F2'` becomes `'FFF2F2F2'`).

#### Critical API Warning
When using `.cell()` method, use `column=` not `col=`: `sheet.cell(row=1, column=2)` ✅ | `sheet.cell(row=1, col=2)` ❌

#### Merged Cell Handling Warning
Never iterate over `merged_cells.ranges` directly - it's a mutable set that changes size during iteration:
```python
# WRONG - causes RuntimeError
for range in ws.merged_cells.ranges:
    ws.unmerge_cells(range)

# CORRECT - convert to static list first
for range in list(ws.merged_cells.ranges):
    try:
        ws.unmerge_cells(range)
    except Exception:
        pass  # Handle edge cases gracefully
```
Always wrap unmerge calls in try-except blocks.

### Working with pandas
- Specify data types to avoid inference issues: `pd.read_excel('file.xlsx', dtype={'id': str})`
- For large files, read specific columns: `pd.read_excel('file.xlsx', usecols=['A', 'C', 'E'])`
- Handle dates properly: `pd.read_excel('file.xlsx', parse_dates=['date_column'])`

## Code Style Guidelines
**IMPORTANT**: When generating Python code for Excel operations:
- Write minimal, concise Python code without unnecessary comments
- Avoid verbose variable names and redundant operations
- Avoid unnecessary print statements

**For Excel files themselves**:
- Add comments to cells with complex formulas or important assumptions
- Document data sources for hardcoded values
- Include notes for key calculations and model sections