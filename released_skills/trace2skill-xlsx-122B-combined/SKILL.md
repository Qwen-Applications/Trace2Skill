---
name: xlsx
description: "Comprehensive spreadsheet creation, editing, and analysis with support for formulas, formatting, data analysis, and visualization. When Qwen-Agent needs to work with spreadsheets (.xlsx, .xlsm, .csv, .tsv, etc) for: (1) Creating new spreadsheets with formulas and formatting, (2) Reading or analyzing data, (3) Modify existing spreadsheets while preserving formulas, (4) Data analysis and visualization in spreadsheets, or (5) Recalculating formulas"
license: Proprietary. LICENSE.txt has complete terms
---



## Important Automation Guidelines

**Prefer Python over VBA for Automation**: When tasks request VBA macros or spreadsheet automation, implement the logic in Python using openpyxl/pandas instead. This provides better error handling, easier debugging, cross-platform compatibility, and avoids macro security issues.

**When users request VBA/macros**: Implement the logic directly in Python using openpyxl or pandas instead of generating actual VBA code.# Requirements for Outputs

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



### ⚠️ When to Compute Directly in Python
If LibreOffice/recalc.py cannot evaluate certain formula patterns (complex array formulas, advanced INDEX/MATCH combinations), compute the logic directly in Python and write results as values:

```python


## Multi-Range Data Processing

For spreadsheets containing multiple independent data blocks separated by blank rows, see: **[references/multi-range-data-processing.md](references/multi-range-data-processing.md)**

This covers:
- Identifying range boundaries based on data presence
- Preserving blank row separators between blocks
- Anchoring headers when sorting each independent range
- String-based sorting for mixed alphanumeric data

## New Section


## Data Transformation Patterns

### Expanding Rows with Optional Dimensions
When generating rows from multiple ID columns, handle cases where some dimensions may be missing:
- Check each dimension independently before requiring all to exist
- Generate single-dimensional output when only one field exists
- Generate full cartesian product when multiple dimensions exist

See `references/data-expansion-patterns.md` for detailed implementation.

### Parsing Complex Filter Strings
Filter expressions may contain mixed syntax formats (single equals, IN LIST, etc.)
- Use targeted regex patterns for each field type
- Search independently for each condition regardless of order
- Handle both single values and comma-separated lists

See `references/filter-parsing-patterns.md` for detailed implementation.

## Formula Patterns Reference
For detailed examples of common formula patterns used in financial modeling, see:
- [references/formula-patterns.md](references/formula-patterns.md) - Conditional logic, lookups, and aggregation patterns

## Data Processing Edge Cases

### Handle Whitespace Variations in Headers
When locating header rows, normalize strings to handle inconsistent formatting:
```python
# Good: Strip whitespace before comparison
if str(row[0]).strip() == 'Teacher ID':
    header_row_idx = i
```

### Preserve Pre-Header Structure
If sheets contain rows before headers (title blocks, empty rows), preserve them separately:
```python
before_header = data[:header_row_idx]  # Save title/spacing rows
final_data = before_header + [header] + cleaned_rows  # Reconstruct output
```

### Column-Specific Deduplication
When deduplicating by specific columns only, track those values separately:
```python
seen_values = set()
col_a_values = []
for row in data:
    col_a_value = row[0]
    if col_a_value not in seen_values:
        seen_values.add(col_a_value)
        col_a_values.append(row)  # Keep full row
```

### Advanced Patterns
For specialized scenarios like dynamic cross-sheet references, see [references/dynamic-sheet-references.md](references/dynamic-sheet-references.md).
# Good: Direct computation when formula engine fails
df['result'] = df.apply(lambda row: calculate_value(row), axis=1)
sheet['B2'] = df['result'].values[0]  # Write computed value
```

Use this fallback when:
- Formula returns errors after recalculation despite correct syntax
- Array formulas or complex nested lookups don't work in LibreOffice
- Performance issues with large datasets requiring formula evaluation## Important Requirements

**LibreOffice Required for Formula Recalculation**: You can assume LibreOffice is installed for recalculating formula values using the `recalc.py` script. The script automatically configures LibreOffice on first run

## Reading and analyzing data


**Warning**: pandas truncates trailing empty rows and does not support row shifting operations (insert/delete rows). For any task involving structural changes like row/column insertion or deletion, use **openpyxl** instead.

### Step 1: Explore Data Structure First
Before implementing any changes:
- Read the spreadsheet to identify available sheets, column names, and data types
- Compare raw input structure with expected output to understand required transformations
- Note differences in row counts, column arrangements, or data consolidation needs
- This prevents implementing incorrect logic and validates your solution approach### Data analysis with pandas
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



### ✅ When Python Calculation + Hardcoding Is Appropriate
Use Python to calculate and hardcode values when:
- Logic involves **stateful dependencies across rows** (e.g., tracking boundaries, cumulative counts, "count since last occurrence")
- Range endpoints depend on **dynamic patterns** in the data itself
- Ranges are determined by dynamic boundary markers in the data
- Excel formulas would require complex nested conditions or helper columns beyond practical complexity
- The computation requires **iterative processing** that Excel cannot express concisely
- Segment-based computations where boundaries vary per row
- Complex dynamic array formulas (TEXTJOIN, FILTER, AGGREGATE) repeatedly fail across spreadsheet engines
- LibreOffice Calc compatibility issues persist after multiple attempts
- Target engine lacks Excel 365 function support
- Formula complexity outweighs benefit of recalculation capability

Example scenario: Counting values between successive occurrences of a marker value in another column requires identifying boundary rows first, then processing each segment—best done in Python.

**Decision Rule**: If you need to track prior rows or find pattern-based boundaries before computing, use Python. Otherwise, prefer Excel formulas.## CRITICAL: Use Formulas, Not Hardcoded Values

## Dynamic vs Static Output Decision

**Default**: Use Excel formulas instead of calculating values in Python and hardcoding them. This ensures the spreadsheet remains dynamic and updateable.

### Choose Approach Based on Task Type

#### Dynamic Models → Use Excel Formulas
For spreadsheets users will modify or update, embed formulas so calculations auto-recalculate.

#### Automation Tasks → Prefer Hardcoded Values
When LibreOffice recalculation has been unreliable, compute values programmatically and write static outputs. This is more dependable for one-time data processing where spreadsheet interactivity isn't needed.

### ⚠️ When Hardcoded Values Are More Reliable
For complex nested formulas (AGGREGATE, INDEX/MATCH with SEARCH) that LibreOffice may fail to evaluate:
- Compute results programmatically using pandas/openpyxl
- Write static calculated values instead of formulas
- This is acceptable when formula evaluation reliability is uncertain

**Exception**: When user explicitly requests "hardcode", "static output", or "finalized values", write calculated integers directly to cells. Follow user intent over general best practices.

This applies to ALL calculations - totals, percentages, ratios, differences, etc. The spreadsheet should be able to recalculate when source data changes.

**Cross-sheet aggregation**: Always use Excel formulas like `=SUM(Sheet1!A1:A10)` instead of computing sums in Python and hardcoding results.

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

### ⚠️ CRITICAL WARNING: Formula Recalculation Is Mandatory

**If you write ANY formulas to an Excel file using openpyxl, you MUST run recalc.py before considering the task complete.**

Formulas written via openpyxl exist only as text strings until recalculated. Without running recalc.py:
- Cells return None/empty when read with data_only=True
- Evaluation fails even if formulas are syntactically correct
- The output file is incomplete

This is non-negotiable. Do not proceed to verification or delivery until recalc.py confirms success.
### Tool Selection Warning

**CRITICAL**: When modifying spreadsheets that contain existing formulas you need to preserve:
- ✅ Use **openpyxl** (`load_workbook()` then `save()`) - formulas remain as strings
- ❌ Avoid **pandas** (`to_excel()`) - converts formulas to static values permanently

If a user mentions "formula errors" or needs dynamic relationships between cells, always choose openpyxl.
### Pre-Formula Checklist
Before writing any formulas:
- [ ] **Verify data types**: Load with pandas or openpyxl to confirm column content types match formula expectations (e.g., datetime vs integers)
- [ ] **Test sample values**: Check 2-3 source cells manually before building full formula set
- [ ] **Plan cell references**: Decide which parts need `$` locks for copyable formulas


**Step 0.5: Preserve Input File**
- When modifying an existing file, copy the input to output path FIRST using `shutil.copy()`
- This preserves all formulas, macros, charts, conditional formatting, and unchanged data
- Only modify the designated output region after copying

### Keyword and Text Pattern Matching
When searching for keywords or patterns in cell text:
- Always normalize text to lowercase and use partial substring matching (`'keyword'.lower() in cell_text.lower()`) rather than exact or whole-word matching
- This handles variations like "123-Core Design" matching "core design"

### Read First: Examine Input File Structure
Before implementing any changes:
1. **List all sheets**: Check available worksheets in the input file
2. **Compare structures**: Identify columns, data types, and row counts between input and expected output
3. **Note transformations**: Document what changed (e.g., rows consolidated, columns added/removed)
This prevents implementing incorrect logic and validates your approach.## Common Workflow
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



#### Clean Output Pattern for Filtering Tasks
When filtering/deleting rows based on complex criteria:
- Create a new workbook with `Workbook()` instead of deleting rows in-place
- Write only desired rows to the new file via cell-by-cell iteration
- This avoids residual formatting artifacts, empty rows, and broken cell references
- Ensures predictable output structure regardless of final row count

**⚠️ CRITICAL: Multi-Row Deletion Safety**
When deleting multiple rows, ALWAYS collect all target row indices first, then sort them in DESCENDING order before deletion:

```python
# ✅ CORRECT - Delete in reverse order to prevent index shifting
to_delete = [11, 10, 9, 8, 7, 6]  # Highest first
to_delete.sort(reverse=True)
for row_idx in to_delete:
    sheet.delete_rows(row_idx)

# ❌ WRONG - Sequential deletion causes index shifting
to_delete = [6, 7, 8, 9, 10, 11]  # This will delete wrong rows
for row_idx in to_delete:
    sheet.delete_rows(row_idx)
```

Deleting sequentially from top-to-bottom causes subsequent indices to shift, skipping rows or deleting wrong data.

**⚠️ CRITICAL: Deleting Consecutive Rows from Top**
When deleting consecutive rows starting from row 1:
```python
# Always delete row 1 repeatedly in a loop, NOT as a range
for _ in range(num_rows_to_delete):
    sheet.delete_rows(1)  # Row 1 shifts down each iteration
```

### Preserve Template Structure
- **Copy the input file first** with `shutil.copy()` before making any modifications
- This preserves all formulas, macros, charts, conditional formatting, and unchanged elements automatically
- Only the designated output region needs explicit clearing and population

#### Preserving Other Sheets When Modifying Partially
When you only need to modify specific sheets:
```python
from openpyxl import load_workbook

wb = load_workbook('file.xlsx')
# Modify ONLY the target sheet
sheet = wb['TargetSheet']
sheet.delete_rows(1, sheet.max_row)
for row in new_data:
    sheet.append(row)
# Save without touching other sheets
wb.save('modified.xlsx')
```
This preserves formulas, formatting, and data in all other sheets.

### ⚠️ CRITICAL: Deleting Multiple Rows
When deleting multiple rows, ALWAYS delete from highest row number to lowest:
```python
rows_to_delete = [10, 15, 20]  # Collect all rows first
for row in sorted(rows_to_delete, reverse=True):
    sheet.delete_rows(row)
```
Row indices shift upward when rows are deleted; bottom-up deletion preserves remaining references.

### Clean Trailing Blank Rows
After filtering or row deletion, remove empty rows beyond the last data row to match expected output range:
```python
last_row = sheet.max_row
while last_row > 1 and all(sheet.cell(row=last_row, column=c).value is None for c in range(1, sheet.max_column + 1)):
    sheet.delete_rows(last_row)
    last_row -= 1
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



### Modification Validation Checklist
Before marking tasks complete:
- [ ] **Read back the output file** to confirm expected changes occurred
- [ ] **Verify row/column counts** match expectations across all sheets
- [ ] **Spot-check modified cells** contain correct values/formulas
- [ ] **Confirm unchanged data** was not accidentally modified
- [ ] **Check all sheets** if working with multi-sheet workbooks
- [ ] **Compare output shape to expected shape** (rows/columns)
- [ ] **Verify all original valid data records remain intact**
- [ ] **Check that blank/gap rows were handled correctly per requirements**
- [ ] **Confirm structural properties match expectations** (contiguous data, correct ranges)

This catches off-by-one errors, incomplete deletions, and unintended side effects before submission.

### Multi-Range Data Processing
When working with spreadsheets containing multiple independent data sections:
- [ ] Identify range boundaries by detecting blank separator rows
- [ ] Keep headers anchored at top of each range before sorting
- [ ] Use lexicographic (string) sorting for alphanumeric data unless numeric extraction is required
- See [Multi-Range Data Processing Patterns](references/multi-range-data-processing.md) for detailed guidance

### Post-Transformation Verification
- [ ] **Read back target range**: After any data transformation, explicitly read the exact cell range specified (e.g., C1:K6) to verify all expected data appears correctly before marking complete
- [ ] **Confirm dimensions**: Check that transformed data has expected row/column counts with no extra/missing entries
- [ ] **Validate content**: Ensure all expected data appears in correct positions
- [ ] **Check boundaries**: Verify no extra/missing data outside intended range

This catches off-by-one errors, incorrect ranges, or partial transformations.## Formula Verification Checklist

Quick checks to ensure formulas work correctly:


- [ ] **Dual verification**: Read cells twice - once with `data_only=False` to confirm formulas exist, then with `data_only=True` to verify computed values match expectations
- [ ] **Immediate recalculation**: Run `recalc.py` right after writing formulas, before proceeding to other tasks. Early error detection saves debugging time.
- [ ] **Complex lookups**: For multi-condition counts with cross-references, use SUMPRODUCT+INDEX/MATCH instead of COUNTIFS alone
- [ ] **Type consistency for comparisons**: Normalize key columns before equality checks
  - ID/text columns: `.astype(str).str.strip()`
  - Numeric columns: `.astype(float)`
  - Prevents false negatives from type mismatches (e.g., "9963547" vs 9963547)
- [ ] **Inspect full structure first**: Read ALL sheets and their shapes/indexes before any modifications
- [ ] **Analyze multi-column dependencies first**: Before implementing formulas, trace how each column relates to others and identify whether operations are local (current row) or global (across all rows)
- [ ] **Distinguish scope clearly**: Note phrases like "entire column" or "across all rows" that indicate global operations vs. row-local operations
- [ ] **Validate against sample data**: When example input/output pairs exist, reverse-engineer the exact calculation method by testing your formula on known cases
- [ ] **Check methodology assumptions**: Verify you're using the correct approach (e.g., FIFO vs LIFO, straight-line vs declining balance) by comparing intermediate results
- [ ] **Multi-cell formula check**: After bulk formula assignment, verify 2-3 cells at different positions contain DIFFERENT formula strings (not identical copies)
- [ ] **Search all sheets**: When aggregating data by pattern, enumerate ALL sheets and search each one—don't assume patterns exist only in seemingly relevant sheets
- [ ] **Validate item counts**: Before placing summary rows, verify discovered item count matches expectations to prevent off-by-one placement errors
- [ ] **Verify full data range**: Before writing range-based formulas, programmatically identify the last non-empty row/column to avoid excluding valid data
- [ ] **No hardcoded range ends**: Never assume range boundaries; scan data to find actual extents
- [ ] **Range transformation consistency**: When modifying a specified range, apply transformations to ALL cells unless explicitly instructed otherwise. Do not filter cells based on content patterns
- [ ] **Edge case testing**: Verify transformation behavior on headers, empty strings, and unexpected values before applying broadly### Essential Verification
- [ ] **Test 2-3 sample references**: Verify they pull correct values before building full model
- [ ] **Column mapping**: Confirm Excel columns match (e.g., column 64 = BL, not BK)
- [ ] **Row offset**: Remember Excel rows are 1-indexed (DataFrame row 5 = Excel row 6)


- [ ] **Task requirement parsing**: Re-read task descriptions for dependency indicators (e.g., "count from entire column" vs. "for this row")
- [ ] **openpyxl formula anchoring**: Remember openpyxl writes formulas literally—no auto-adjustment of relative references when assigning to multiple cells
- [ ] **Missing sheets**: Data patterns may appear in unexpected sheets (EXPRO, CMSN, CSTR)—search all sheets systematically
- [ ] **Formula vs value confusion**: Verify whether output requires `=SUM(...)` formulas or computed numeric values before writing totals
- [ ] **Data mapping validation**: Count filtered/source items BEFORE writing; ensure destination range matches expected count
- [ ] **Task requirement verification**: Confirm whether sequential filling or ID-based matching is required before implementing
- [ ] **LibreOffice compatibility**: Avoid SUMPRODUCT with boolean arrays (e.g., `--(A:A>0)`), complex nested arrays, dynamic array functions (INDEX/SMALL/IF combinations), or Excel-specific functions that may fail in LibreOffice
- [ ] **Matrix axis orientation**: When populating matrices by numeric dimensions (Impact/Likelihood scales), verify whether values increase or decrease along each axis before mapping. Check existing labels, headers, or partial data to confirm direction.
- [ ] **Cross-validate with partial data**: Use any pre-existing template structure or partially filled cells as validation hints for expected cell-to-value mappings
- [ ] **Persistent formula errors**: If recalc.py shows repeated errors despite fixes, consider computing values in Python instead
- [ ] **Algorithmic reset logic**: Ensure you track what resets (counter, reference value, etc.) and what persists across restart conditions
- [ ] **Multi-condition filtering**: When filtering by multiple possible values in one dimension (e.g., matching any of several users), use SUMPRODUCT with boolean multiplication instead of trying to combine SUMIFS calls
- [ ] **Range boundary validation**: Print first/last cell of computed range to confirm correct dimensions before processing
- [ ] **Numeric type variations**: Check for all zero representations - use `isinstance(val, (int, float)) and val == 0` for numeric zeros AND `'0'` for string zeros. Excel may store the same conceptual value as different Python types.### Common Pitfalls
- [ ] **NaN handling**: Check for null values with `pd.notna()`
- [ ] **Far-right columns**: FY data often in columns 50+ 
- [ ] **Multiple matches**: Search all occurrences, not just first
- [ ] **Division by zero**: Check denominators before using `/` in formulas (#DIV/0!)
- [ ] **Wrong references**: Verify all cell references point to intended cells (#REF!)
- [ ] **Cross-sheet references**: Use correct format (Sheet1!A1) for linking sheets



- [ ] **Target range validation**: After transformations, verify results specifically within the required answer_position range rather than assuming global correctness
- [ ] **Boundary checks**: Edge cases may behave differently at range boundaries; explicitly validate those cells
- [ ] **Segment-based verification**: For boundary-dependent calculations, sample across different segment types (first segment, short segments, long segments, edge cases)
- [ ] **Boundary marker checks**: Verify all boundary positions were correctly identified before processing segments
- [ ] **Case-insensitive matching**: Use `ISNUMBER(SEARCH("pattern",cell))` instead of UPPER()/LOWER() for substring checks
- [ ] **Verify calculated values**: Load with `data_only=True` to confirm formulas produce expected results, not just formula strings
- [ ] **OR logic pattern**: For multiple criteria in one field, sum separate SUMIFS calls rather than array formulas
- [ ] **Dynamic formula patterns**: For row-level operations, use f-strings to embed current cell references (e.g., `f'=COUNTIF($A$2:A{i}, A{i})'`) so each row references its own position
- [ ] **Verify with same library**: After writing with openpyxl, verify using openpyxl cell-by-cell, not pandas (different parsing behavior)
- [ ] **Complete row validation**: For batch operations, test first few rows THEN validate every remaining row to catch off-by-one errors or late-row misses### Formula Testing Strategy
- [ ] **Start small**: Test formulas on 2-3 cells before applying broadly
- [ ] **Verify dependencies**: Check all cells referenced in formulas exist
- [ ] **Test edge cases**: Include zero, negative, and very large values



### Post-Recalculation Verification (REQUIRED)
After running recalc.py, ALWAYS verify the file contains calculated values:

```python
from openpyxl import load_workbook

# Load with data_only=True to read calculated values
wb = load_workbook('output.xlsx', data_only=True)
sheet = wb.active

# Check key cells have numbers, not formula strings
print(sheet['B10'].value)  # Should show 5000, not '=SUM(B2:B9)'
```

If cell values appear as formula text (e.g., `"=SUM(...)"`) instead of numbers, you skipped the recalculation step.

### Verification After Row Removal
After removing rows based on duplicate detection or filtering:
- Load the original file's reference column separately
- Scan the output file's target column for any remaining matches
- Zero matches confirms correct deletion

This verification applies to any deduplication, exclusion, or filtering task where certain values must not persist in output.

### When Errors Persist After Fixes
If `recalc.py` returns errors that cannot be corrected through formula adjustments:
1. Identify which formulas cause repeated failures
2. For those specific cells, compute values in Python instead
3. Write the computed result as a value (not a formula string)
4. Add a comment noting: "Computed externally due to formula engine limitation"

This preserves task completion while respecting spreadsheet engine constraints.### Interpreting recalc.py Output
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


### Advanced Formula Checks
- [ ] **Modern function prefixes**: All AGGREGATE/FILTER/UNIQUE/XLOOKUP use `_xlfn.` prefix
- [ ] **Extended ranges**: Dynamic formulas use ranges exceeding current data size (10x buffer)
- [ ] **ArrayFormula objects**: Array formulas created via `ArrayFormula()` not string assignment
- [ ] **Edge case testing**: Formulas tested with empty cells, duplicates, boundary conditions

### Logical Validation Before Implementation
- [ ] **Verify against task examples**: If task provides numerical examples, manually calculate through at least 2 iterations to confirm formula logic matches expected output
- [ ] **Distinguish accumulation types**: Confirm whether column requires simple running sum (`=SUM($A$1:A1)`) or compound calculation (`=(prev_total+new_input)*(1+rate)`) - these produce different results
- [ ] **Test recurrence relations**: For period-to-period dependent calculations, trace through 2-3 periods manually before applying formula across entire column
- [ ] **Check edge cases**: Verify first row (no previous value), zero inputs, and negative values handle correctly

### Post-Construction Validation
- [ ] **Spot-check formulas**: After writing formulas to cells, read back 2-3 cells and verify the formula string matches intent
- [ ] **Recalculate immediately**: Run `recalc.py` right after formula insertion to catch errors before continuing
- [ ] **Cross-reference values**: Pick one row, manually compute what the result should be, confirm formula produces same value

### Dependency Compatibility Checks
- [ ] **Verify lookup sources**: Ensure columns referenced by formulas don't contain _xlfn.* or other unsupported functions
- [ ] **Test in target environment**: Run recalc.py early to catch #NAME? errors from incompatible functions
- [ ] **Check array function dependencies**: UNIQUE, FILTER, SEQUENCE may fail in LibreOffice - compute values directly if needed
- [ ] **Confirm output type**: Verify whether task requires formulas ('=SUM(...)') or calculated values (plain numbers/text)

### Output Location Verification
- [ ] **Verify answer_position**: Parse the full sheet name and range (e.g., 'MyResult'!A1:O20 means sheet='MyResult', range='A1:O20')
- [ ] **Check sheet existence**: If specified sheet doesn't exist, CREATE it before writing
- [ ] **Never substitute sheets**: Do not write to similarly-named sheets (e.g., 'Result' ≠ 'MyResult')

### Data Ordering Requirements
- [ ] **Check for merge/join operations**: When combining datasets from multiple sources, verify if output must be sorted
- [ ] **Identify primary key**: Look for ID columns or logical sort keys in the expected output
- [ ] **Sort before saving**: Apply sort by primary key (ascending numerical order typically expected)

### Aggregation and Grouping
- [ ] **Track original row order**: Add helper column with indices before `groupby()`
- [ ] **Sort by index after aggregation**: Restore expected output sequence
- [ ] **Beware of reordering**: Simple `drop_duplicates()` or `groupby().first()` may change row sequence arbitrarily

### Post-Save Verification
- After completing modifications, reload the saved file and verify:
  - Key cells contain expected values/formulas
  - Formatting was preserved correctly
  - No unintended changes to unmodified sheets
  - Off-by-one errors in row/column positions

### LibreOffice-Specific Limitations
- [ ] **Avoid unsupported functions**: Some Excel 365 dynamic array functions don't work in LibreOffice command-line mode
- [ ] **Simplify complex lookups**: Use INDEX/MATCH instead of complex nested array formulas when possible
- [ ] **Test before scaling**: Verify 2-3 cells calculate correctly with recalc.py before applying formulas broadly
- [ ] **Check for empty results**: If recalc.py returns no errors but cells are empty, the formula may not be supported by LibreOffice

### Fallback Strategy for Persistent Errors
If recalc.py reports errors after 2+ fix attempts:
1. Stop trying to fix the formula
2. Calculate the value programmatically in Python
3. Write the result as a direct cell value (not a formula)
4. Document why you switched to hardcoded calculation

This ensures task completion when cross-platform formula incompatibilities prevent ideal approaches.

## Best Practices

### Library Selection
### Library Selection
- **pandas**: Data analysis, bulk filtering/deletion by condition, statistical analysis, large datasets (10k+ rows), complex transformations (groupby, aggregate, sort)
- **openpyxl**: Complex formatting, formula preservation, cell-by-cell modifications, visual styling, row/column deletions
- **Combine both**: Use pandas for data manipulation then openpyxl for writing back to preserve sheets, formatting, and formulas
- **Critical**: Use openpyxl (not pandas) when cell formatting (fonts, colors, borders, number formats) must be preserved across transformations
- **openpyxl mandatory for cell-level styling**: When modifying specific cells AND applying formatting (alignment, fonts, fills), always use openpyxl. Pandas cannot modify individual cell properties
- **Rule of thumb**: Use pandas for "what data" questions, openpyxl for "how it looks" questions

### Library Selection Decision Tree
- **New file creation with formulas/formatting**: openpyxl
- **Data analysis/visualization**: pandas
- **Modifying existing file **(preserve formatting): openpyxl with load_workbook()
- **Bulk data export without formatting concerns**: pandas
- **User requests VBA**: Implement in Python/openpyxl instead


- **Error value detection**: Excel errors like '#N/A', '#REF!' are stored as plain strings. Check with `isinstance(cell.value, str) and cell.value.startswith('#')` rather than relying on type checks
- **Delete rows bottom-up**: When deleting multiple rows scattered through the sheet, always iterate from max_row downward (`range(max_row, 1, -1)`) to avoid index shifting
- **Preserve special rows**: Explicitly exclude protected rows (headers, blank rows) in your loop range rather than filtering afterwards
- **Data type validation**: Before filtering or comparing values, validate data types match expected format. Check sample cell values with `cell.value` and verify type (datetime vs string). Type mismatches cause silent filter failures where no rows match criteria
- For row-based operations (e.g., identifying groups separated by blank rows), iterate cells directly and detect transitions between populated and empty rows
- When marking maximum values in groups, use equality comparison against the max value to correctly handle ties (assign 'Y' to all members equal to max, not just first occurrence)### Working with openpyxl
- Cell indices are 1-based (row=1, column=1 refers to cell A1)
- Use `data_only=True` to read calculated values: `load_workbook('file.xlsx', data_only=True)`
- **Warning**: If opened with `data_only=True` and saved, formulas are replaced with values and permanently lost
- For large files: Use `read_only=True` for reading or `write_only=True` for writing
- Formulas are preserved but not evaluated - use recalc.py to update values



**Data Transformation Pattern** — For columns with mixed types (strings, dates, numbers):
- Check types before transforming: `isinstance(value, (datetime, date, int, float))`
- Apply string transformations only to string values, preserve others unchanged
- Verify before/after samples for representative cases
- See [references/data-transformation-patterns.md](references/data-transformation-patterns.md) for detailed examples

### Batch Transformation Guidelines
For tasks requiring N items per output row:
- **Check divisibility**: Count total source items before planning output rows
- **Partial rows allowed**: Fill remaining items in final row without padding
- **No empty gaps**: Complete each row with available data, don't skip cells
- Example: 22 items with 4-per-row → rows get [4,4,4,4,4,2] not [4,4,4,4,4,4,4]### Working with pandas
- Specify data types to avoid inference issues: `pd.read_excel('file.xlsx', dtype={'id': str})`
- For large files, read specific columns: `pd.read_excel('file.xlsx', usecols=['A', 'C', 'E'])`
- Handle dates properly: `pd.read_excel('file.xlsx', parse_dates=['date_column'])`


## New Section

## Advanced Patterns Reference

For detailed guidance on complex scenarios:
- **Multi-section parsing**: See `references/multi-section-parsing.md` for handling sheets with multiple data sections separated by marker rows
- **Advanced formula patterns**: See `references/advanced-formula-patterns.md` for complex lookup formulas
- **Multi-range data processing**: See `references/multi-range-data-processing.md` for processing spreadsheets with multiple independent data blocks
## Verification and Validation

### Post-transformation Verification
After implementing spreadsheet changes:
- **Create verification script**: Re-read output file independently to validate results
- **Display key outputs**: Show values, formats, and conditional results from target cells
- **Compare against expectations**: Check format classifications, cell references, calculated values
- **Run recalc.py**: Verify no formula errors introduced by your changes

This catches subtle bugs like format misclassification, off-by-one errors, or unintended side effects.

### Error Recovery Pattern
If recalc.py finds errors:
1. Parse JSON output to identify error type and locations
2. Fix root cause (wrong references, division by zero, type mismatches)
3. Recalculate again until status shows success
4. Document what was fixed for future reference

### Output Verification Checklist

After completing spreadsheet modifications:
- [ ] **Match answer_position range**: Verify output sheet has correct row count and column range per task specification
- [ ] **Spot-check transformations**: Compare 2-3 sample rows between input and output to confirm logic applied correctly
- [ ] **Sheet integrity**: Confirm all original sheets preserved (if required) and new sheets created properly

### Edge Case Validation Requirements
Before finalizing any numerical operation:
- [ ] Test with zero values in denominator/range
- [ ] Test with negative values if applicable
- [ ] Test with tied values (multiple cells with same result)
- [ ] Test with completely empty/null ranges
- [ ] Verify output format handles all cases gracefully (no crashes or exceptions)

### Script Naming Rules
**CRITICAL**: Never name your Python scripts after standard library modules (e.g., `inspect.py`, `os.py`, `sys.py`, `pandas.py`).
When Python imports libraries, it searches the current directory first. A local file named `inspect.py` shadows the built-in module, causing circular import errors that break all subsequent imports.
Use unique names like `process_data.py`, `spreadsheet_tool.py`, etc.

### Efficient Row Processing Patterns
For operations spanning multiple rows with conditional dependencies:
- Maintain state variables that persist across iterations (`current_a_value`, `group_active`)
- Update state when encountering new group markers, reset when hitting boundaries
- Avoid nested loops—single-pass iteration handles multi-row dependencies cleanly

### Validation Scope
After applying transformations, verify results **specifically within the required answer_position range** rather than assuming global correctness.
Focused verification catches boundary-related bugs that full-scope checks might miss.

### Verification After Structural Changes
After any row/column insertion or deletion:
- [ ] Verify final row count matches expected result
- [ ] Print/log remaining data and confirm retention criteria
- [ ] Check formulas still reference correct cells after shifts
- [ ] Recalculate with `recalc.py` to catch formula errors from shifted references


### Value Transformation Pattern
When replacing multiple specific values:
- **Use dictionary mapping with `.map()`** instead of if-elif chains
- Example:
  ```python
  mapping = {'USA': 'United States', 'UK': 'United Kingdom'}
  df['country'] = df['country'].map(lambda x: mapping.get(x, x))
  ```
- Handles unmatched values gracefully (keeps original)
- More maintainable and scalable than conditional logic

### Value-Based Number Formatting
For complex formatting requirements that Excel's conditional formatting cannot express:

```python
# After recalc.py has computed values
for row in range(2, max_row + 1):
    cell = sheet[f'C{row}']
    value = cell.value  # Read calculated value
    
    # Determine format based on actual value characteristics
    if value == int(value):
        cell.number_format = '0'
    elif len(str(value).split('.')[1]) <= 1:
        cell.number_format = '0.0'
    else:
        cell.number_format = '0.00'
```

This pattern generalizes to any scenario requiring format differentiation based on cell content.## Code Style Guidelines
**IMPORTANT**: When generating Python code for Excel operations:
- Write minimal, concise Python code without unnecessary comments
- Avoid verbose variable names and redundant operations
- Avoid unnecessary print statements

**For Excel files themselves**:
- Add comments to cells with complex formulas or important assumptions
- Document data sources for hardcoded values
- Include notes for key calculations and model sections


### Advanced Data Patterns

#### Detecting Group Boundaries
When processing data organized in blocks separated by blank rows:
- Iterate through rows and detect transitions from populated to empty cells
- Break at empty cells to identify independent groups
- Common in financial reports where categories are visually separated

Example pattern:
```python
# Detect group boundary by checking for empty cell
group_start = current_row
while sheet.cell(row=current_row, column=col).value is not None:
    current_row += 1
group_end = current_row - 1
```

#### Handling Tied Maximums
When marking highest values in a group (e.g., tournament scoring):
- Compute the maximum value for the group first
- Assign marker to ALL members whose value equals the maximum
- Do NOT use index-based selection which only captures the first occurrence

Example pattern:
```python
max_val = max(group_values)
for idx, val in enumerate(group_values):
    if val == max_val:  # Equality handles ties correctly
        sheet.cell(row=row_idx + idx, column=mark_col).value = 'Y'
```

## New Section

## Cross-Sheet Data Matching

When matching or joining data across multiple sheets:
- Use composite keys (multiple columns) when single identifiers may have duplicates
- Pre-build lookup dictionaries/indexes for O(1) access instead of repeated searches
- Leave cells empty when source data legitimately has no match; do not force incorrect values

See [references/cross-sheet-matching-patterns.md](references/cross-sheet-matching-patterns.md) for detailed examples.