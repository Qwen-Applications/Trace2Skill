---
name: xlsx-122B
description: Use when handling spreadsheet tasks in .xlsx files with the released 122B created-from-scratch skill
---

# XLSX Task Solving

## Approach

## New Section

## Critical Warnings

### Follow Explicit Instructions Over Assumptions
When task instructions specify an operation (e.g., "delete rows where column C ≠ #N/A"), implement the instruction LITERALLY. Do NOT assume the instruction is backwards or misworded just because reference data appears to contradict it.

### Verify Deletion/Filtering Logic Direction
Before executing any row/column deletion or filtering:
1. State explicitly: "DELETE rows where [condition]" vs "KEEP rows where [condition]"
2. Print test samples: show 2-3 rows that WILL be deleted AND 2-3 rows that will remain
3. Confirm the output matches your stated criteria before writing changes

### Method Constraints Are Explicit Requirements
Implement ALL task requirements unconditionally — Every specification (formulas, display rules, formatting) must appear in output regardless of current data triggering conditions. Display rules define how the file should behave for future data, not just current observations.

### Formula Verification
Formulas don't auto-calculate in openpyxl — `cell.value` returns formula strings, not evaluated results. After writing formulas, verify actual output by manually computing expected results for 3+ diverse sample rows. **DO NOT** declare task complete based on file existence alone.

### Structural Changes Break Formulas
openpyxl does NOT auto-update formula references when calling `ws.delete_rows()` or `ws.insert_rows()`. After any row/column insertion or deletion, audit cells containing formulas (`=`, `+`) to ensure references are still valid.

### Data Type Preservation
- **DO NOT convert string values to floats** even if they contain only digits. Material codes, product IDs may be stored as strings like "50000003" — converting produces `50000003.00` which corrupts data.
- Check cell value type before aggregation: `isinstance(val, str)` means preserve as-is; `isinstance(val, (int, float))` means safe to aggregate.

### Preserve Existing Cell Values
**NEVER overwrite a cell without first checking its current value.**
```python

## New Section

## Pre-Completion Verification Checklist
Before marking any task complete, verify:
- [ ] Read the output file back to confirm all expected cells contain correct values (not just that write operations succeeded)
- [ ] Scan input spreadsheet for headers, comments, or pre-filled cells indicating expected outputs
- [ ] Result count exactly matches target range size (e.g., C2:C13 = 12 cells, not 11 or 13)
- [ ] Verify no silent serialization issues (encoding, path problems, corrupted data)
- [ ] Sheet names, headers, and data types remain intact
- [ ] No unintended data corruption or stale references exist
- [ ] Every task specification appears in your final output

**DO NOT** assume success based on intermediate prints or API responses alone.

## New Section

## Tool Action Formatting
- **CRITICAL**: When writing multi-line Python code in JSON actions, escape all newlines as `\\n` and quotes as `\\"`
- Test with minimal commands first (e.g., single-line print statements) before complex scripts
- If you receive 3+ consecutive 'parse failed' errors, STOP and try a fundamentally different approach
- Consider writing complex scripts to temporary .py files and executing them, rather than embedding in JSON strings

See [references/command-patterns.md](references/command-patterns.md) for working bash command examples.

## New Section

## Formula Handling

### Writing Excel Formulas in openpyxl
- Formulas must be assigned as `.value` with leading `=` character:
  ```python
  ws['A1'].value = "=IF(B1>0,\"Yes\",\"No\")"
  ```
- **DO NOT** use `ws['A1'] = "==IF(...)"` (double equals creates text)
- Test on 1-2 cells first before applying to entire column

### Avoid Array Comparisons
- **DO NOT** write complex array formulas (CSE formulas) like nested `IF` inside `MIN`, `TEXTJOIN` with arrays
- **PREFERRED APPROACH**: For complex formulas, compute results in Python and write calculated values instead

### Verify Computed Values, Not Just Syntax
- **DO NOT** assume formulas work correctly just because they exist with valid syntax
- **MUST** verify actual computed outputs by reading cell values after recalculation
- Use `load_workbook(..., data_only=True)` when reading back to get calculated values

### Openpyxl Formula Storage
- **openpyxl does NOT evaluate Excel formulas** when reading/writing files
- If you write `=MID(A1,...)` to a cell, openpyxl stores the formula string but the cell value remains empty until Excel recalculates
- **PREFER Python string operations** (`split()`, `find()`, slicing, regex) to compute values, then write static results

### Lookup Best Practices
- **Validate lookup targets first**: Before exact-match lookups, verify lookup value exists in source range
- **Add error handling**: Wrap lookups with `IFERROR()` or `IFNA()` to return fallback values
- For lookup/matching tasks: clarify whether rows can match themselves or must find external matches

### Version Compatibility
- Prefer INDEX/MATCH over FILTER/XLOOKUP unless you confirm Excel 365+/2021+ support
- Document assumptions about required versions

### Edge Case Testing
Before marking complete, verify:
- [ ] First and last data row formulas compute correctly
- [ ] Rows with duplicate input values produce distinct sequential outputs (for ranking)
- [ ] No empty/null values appear where numbers expected
- [ ] Formula range covers all intended rows (check boundaries)

## New Section

## Critical Constraints

### Range Constraints (answer_position)
- **DO** extract the exact cell range from task specifications (e.g., `G2:G5` means rows 2-5 only)
- **DO** implement conditional logic to write ONLY to cells within the specified range
- **DON'T** process entire columns or dataframes when only specific cells are targeted

Example:
```

## New Section

## Critical openpyxl Patterns

### Formula Access (openpyxl 3.x)
- Use `cell._value` or `isinstance(cell.value, str) and cell.value.startswith('=')` to detect formulas
- DO NOT use `cell.formula` — deprecated/removed in 3.x

### Row Iteration
- `ws[row_num]` returns a **tuple** of Cell objects, NOT a Row wrapper
- For programmatic cell access in loops, ALWAYS use: `ws.cell(row=row_num, column=col)`
- DO NOT call methods like `.cell()` on `ws[row_num]` — it's a tuple, not an object with methods

### Safe Feature Detection
| Feature | Safe Check |
|---------|------------|
| Comment | `cell.comment is not None` |
| Merged cell | `cell.is_merged` |
| Formula | `isinstance(cell.value, str) and cell.value.startswith('=')` |
| Hyperlink | `cell.hyperlink is not None` |

### Example
```python

## New Section

## Critical Validation Requirements
**MANDATORY before saving any modified workbook:**

1. **Validate min/max constraints**: After all value modifications, loop through every changed cell and verify `min_value <= cell.value <= max_value`. If violations exist, correct them or flag the issue.

2. **Verify computed vs. persisted values**: Log both the calculated value AND the value actually written to disk. Discrepancies indicate save/write bugs.

3. **Handle None constraint values safely**: When reading constraints from cells, explicitly check for None/blank:
   ```python
   max_val = cell.value if cell.value is not None else float('inf')
   min_val = cell.value if cell.value is not None else 0
   ```
   Never rely on implicit type coercion.

## New Section

## Data Integrity Best Practices
- **Use sets for uniqueness**: When collecting unique items (IDs, pairs, values), use Python `set.add()` rather than list membership checks (`if item not in list`) to guarantee no duplicates
- **Verify counts match**: After writing results, compare `len(unique_items)` against entries actually written—mismatches indicate duplicate writes or missing data
- **Check for duplicates before finalizing**: Run a quick deduplication check on output data before marking the task complete
# Correct formula detection
if isinstance(cell.value, str) and cell.value.startswith('='):
    formula = cell._value

# Correct cell access in loops
for col in range(1, max_col + 1):
    cell = ws.cell(row=row_num, column=col)
```
# WRONG: Processes entire column
df['Found ID'] = df.apply(find_matching_id, axis=1)

# RIGHT: Limits to specified range
for row_idx in range(2, 6):  # rows 2-5
    ws[f'G{row_idx}'] = find_matching_id(df.iloc[row_idx])
```

### Transposition Rules
- **Transposition = horizontal output**: When instructions say "transposed" or "across the row", distribute values across columns (e.g., D8:E8:F8), not down rows (D8:D9:D10)

### Data Range Handling
- Never assume data ends at specific row number. Always query `ws.max_row` and iterate from first to last data row
- **Wrong:** `for row_num in range(2, 7)` # assumes exactly 5 rows
- **Right:** `for row_num in range(2, ws.max_row + 1)` # adapts to actual data

### Calculation Logic
- Determine if you need **per-row calculation** (each cell based on that row's data) or **aggregate calculation** (uniform total/count)

### Lookup Semantics
- **DO** clarify whether rows can match themselves in cross-row lookups
- **DO** document your decision on self-matches in reasoning
# CORRECT
if ws['B2'].value is None:
    ws['B2'].value = extracted_value
```

### Handle Header Rows Separately
Row 1 typically contains column headers, not data. **Skip or specially handle row 1** unless the task explicitly requires transforming headers.

### DO NOT Access Internal Object Attributes
- **Wrong**: `if 'comment' in cell.__dict__` → raises `AttributeError`
- **Correct**: `if cell.comment is not None` → uses public API

### Iterating Rows Correctly
- **Wrong**: `for cell in ws['C']:` processes infinite column including trailing empty cells
- **Right**: `for row in range(2, ws.max_row + 1): cell = ws.cell(row=row, column=3)`

### File Existence Verification
Always verify output file exists at the specified path after wb.save() — check file existence and readability before TASK_COMPLETE.

### Array Formula Compatibility
Formulas using `(condition1)*(condition2)` inside MATCH/INDEX require array entry (Ctrl+Shift+Enter) in Excel versions before Office 365. Document this requirement clearly.

### Answer Position Strictness
Only modify cells within the specified range. Do NOT expand modifications unless explicitly required. Off-by-one errors cause silent failures.
## Approach
1. **Parse ALL instruction clauses**: When an instruction contains multiple requests (separated by "and", ", also", etc.), enumerate each as a separate requirement. Distinguish between one-time data transformations and capability provisions.
2. Read the task carefully and identify the target workbook, sheet, cells, or columns
3. Use Python to inspect the workbook structure before editing anything
4. **Generate and execute code immediately after inspection** - do not remain in exploration mode
5. Make the smallest change that satisfies the task
6. **Validate sheet references**: If answer_position specifies a sheet name, verify it exists in wb.sheetnames. Document any mapping discrepancies before proceeding.
7. Re-open or recalculate the workbook and verify actual cell VALUES match expected outputs, not just that formulas exist
8. **Verify deliverable type matches request**: If task asks for "macro", embed VBA; if "formula", create cell formulas; if "script", use Python. Do not substitute one for another.
9. **Respect answer_position boundaries**: Only modify cells within the specified range. Treat this as a HARD BOUNDARY unless explicitly required otherwise.
10. State clearly what changed and where

[See [references/openpyxl-patterns.md](references/openpyxl-patterns.md)](references/openpyxl-patterns.md) for detailed API patterns and merged cell handling.
## Debugging Tips
- Print intermediate counts and results during multi-step data operations to spot discrepancies early
- Independently calculate expected totals (row counts, value sums) and compare against actual output before claiming completion
- When counting mismatches occur, re-count from source data rather than assuming which side is wrong
## Useful Tools
- `openpyxl` - read and edit `.xlsx` files, sheets, cells, formulas, and styles
- `pandas` - inspect tables, filter rows, compare values, and summarize data
- `zipfile` and XML tools - inspect workbook internals when workbook structure looks suspicious

- See [references/formulas.md](references/formulas.md) for detailed guidance on formula handling, especially array formulas spanning multiple cells.
- **openpyxl API**: Use `load_workbook()` (with underscore), NOT `loadWorkbook()`. All openpyxl methods use snake_case naming.

## Tips
- Check exact sheet names before reading or writing cells
- Query available sheet names with `wb.sheetnames` BEFORE assuming any default name like 'Sheet1'
- Verify row and column indexing carefully when converting between table views and Excel coordinates
- Preserve untouched sheets, formulas, and formatting unless the task requires changing them
- Read the workbook back after editing to confirm it contains the expected values
- **Always query `ws.max_row` and `ws.max_column` before looping** — never assume row counts from sample previews or hints
- **Document case-sensitivity choices** when searching/replacing text; use `.lower()` for flexible matching unless exact case is required
- Validate output independently against task requirements using external criteria, not just your own transformation logic
- When filtering or transforming data, verify results are non-trivial before completing. Empty results warrant re-checking your interpretation
- DO NOT execute data transformations on programming/code questions — respond with code snippets or explanations instead
- **CRITICAL**: File existence ≠ task complete. You must verify computed values before marking TASK_COMPLETE
- Prefer simple formulas over deeply nested logic. Complex array formulas often fail silently
- When formulas are involved, add an explicit testing phase: load output, force recalculation, print/comparison test values
- **Handle blank rows carefully**: Check `cell_value is None` first, then `str(cell_value).strip() == ''`. Use `isinstance(cell.value, (int, float))` checks before string conversion for numeric/date cells
- **Use dynamic column selection**: Detect column names dynamically from headers or accept as parameters—do NOT hardcode column indices
- **Validate answer_position**: When task provides answer_position (cell range), read workbook first to confirm actual row/column count matches
- **Write precise ranges**: Use `SUMIFS(C3:C1000,A3:A1000,"value")` not `SUMIFS(C:C,A:A,"value")`. Full-column refs include headers and empty rows
- **Verify formula calculations**: After writing formulas, confirm they evaluate to expected values—not just that formula string is present
- **Formulas vs static values**: Write Excel formula strings (e.g., `'=A1+B1'`) NOT pre-calculated numbers
- **Use relative references**: Use relative cell references so formulas auto-adjust when applied across multiple rows
- **Header row handling**: Always exclude header rows before applying `.astype(float)` or numeric operations
- **Sample data strategically**: Sample from beginning, middle, and end of range—not just first few rows—before designing solutions
- **Color formats**: openpyxl requires 8-character ARGB hex codes (AARRGGBB). Use "FFFFFFFFF2CC" for opaque yellow, NOT "FFF2CC"
- **Lookup bounds**: Before VLOOKUP/XLOOKUP/Index-Match, explicitly document source/target row/column bounds. Verify no rows were miscounted
- **column_dimensions is dictionary-like**: do not access `.index`; use keys directly like `ws.column_dimensions['A']`
- **Preserve row order during aggregation**: When tasks specify "do not alter the order of original rows," iterate sequentially and track grouping keys by first appearance—DO NOT sort keys alphabetically
- **Double-check Excel function names**: COUNTIFS, SUMIFS, VLOOKUP, INDEX, MATCH, IFERROR are prone to typos. Python scripts may run fine even with invalid Excel formulas
- **Row reordering**: When moving rows containing formulas, UPDATE row references inside formula strings (e.g., `E23` → `E3` if row moved from 23→3). Use regex pattern `[A-Z]+[0-9]+` to find and adjust row numbers
- **Separate concerns**: complete core manipulation first, then verify separately to isolate issues faster
- When detecting duplicates, base logic on primary key fields (Particulars, Vch No., ID) not full-row equality
- Modify workbooks in-place using `load_workbook()` and `save()` — DO NOT create a fresh Workbook() unless you must rebuild the entire file
- See [references/common-pitfalls.md](references/common-pitfalls.md) for detailed guidance on escaping, formats, and validation
- See [references/formula-best-practices.md](references/formula-best-practices.md) for detailed patterns and verification techniques
- See [references/formula-construction.md](references/formula-construction.md) for comprehensive formula scope matching, volatile function alternatives, and validation patterns


## New Section

## Critical Warning: Formula Strings vs Calculated Values

**When a task asks to "output the answer" or specifies an expected numeric result**, write the **computed value** to the cell, NOT a formula string.

| Task Phrasing | What to Write |
|---------------|---------------|
| "output the answer", "expected result should be X", "calculate and put in cell" | The numeric value (e.g., `ws['D2'] = 270`) |
| "create a formula", "insert SUMIF function", "write a formula" | The formula string (e.g., `ws['D2'] = '=SUMIF(...)'`) |

**Why this matters**: openpyxl stores formulas as text strings without evaluating them. If you write `'=SUMIF(A:A,"111111",B:B)'`, the cell contains that formula text—not the calculated value 270.

**Correct approach for value output**:
1. Read the source data columns using pandas or openpyxl
2. Compute the result in Python (e.g., `result = df[df['A']==111111]['B'].sum()`)
3. Write the computed number directly: `ws['D2'] = result`

**Verify before completing**: After saving, re-open the workbook and confirm the target cell contains the expected numeric value, not a formula starting with `=`.

## New Section

## Formula Best Practices
- **Use one formula pattern for all cells**: When filling formulas across rows/columns, create a single formula string that uses relative functions (e.g., `ROW()`, `ROWS()`) rather than cell-specific hardcoded values
- **Validate formula logic before coding**: Substitute concrete row numbers into your formula expression and verify the arithmetic produces expected results. Document substitutions explicitly
- **Keep formulas clean**: Avoid unnecessary parentheses around constants
- **Avoid volatile functions**: DO NOT use OFFSET(), INDIRECT(), NOW(), TODAY() inside SUMPRODUCT, array formulas, or large calculations. Prefer INDEX(), direct references, or static values

## New Section

## Data Aggregation & Merging
When merging, grouping, or summarizing data:
1. **Read original headers first** - Extract header row from source before writing any output headers
2. **Preserve all grouping keys** - If grouping by N columns, output must include all N keys plus aggregated values
3. **Validate output width** - Count expected columns (grouping keys + aggregated values) before finalizing
4. **Never hardcode headers** - Use actual header values from the input file, not assumed names

### Example Pattern
```python

## New Section

## Structural Modifications
When deleting rows or columns:
- **Use native methods**: `sheet.delete_cols(col_num)` or `sheet.delete_rows(row_num)` with integer indices (1-based)
- **Verify immediately**: Check `sheet.max_column` and `sheet.max_row` after deletion to confirm structure changed
- **Rebuild pattern**: If native methods fail or leave residuals, create a new Workbook, copy only desired columns/rows, then save the new workbook

### DO NOT:
- ❌ `del sheet.dimensions[...]` — dimensions is a string property, not deletable
- ❌ Manual cell-shifting loops — they don't update internal tracking metadata
- ❌ Set cells to None expecting column removal — empty cells still occupy space

After deletion, **VERIFY** by checking `sheet.max_column` decreased as expected. If native deletion fails or leaves empty columns, **REBUILD** the workbook by copying only needed columns to a new Workbook object.

## New Section

## Task Type Awareness

Before implementing, determine the required deliverable type:

| Keywords | Deliverable |
|----------|-------------|
| "macro", "function", "tool", "automation" | Reusable component (VBA module, Python script, callable function) |
| "update", "modify", "change", "transform" | Direct file modification |

If the user requests a macro or similar automation tool, do NOT perform direct data transformation on the spreadsheet. Create an automatable component instead.

## New Section

## Implementation Method Selection
- **Always check** if the task specifies a particular technology (VBA, SQL, pandas, etc.)
- If the user states "X is preferred" or "use X", implement using that exact method
- Do not substitute your default tools (Python/openpyxl) unless no alternative exists
- When in doubt, ask clarifying questions before choosing an implementation approach
# Read source headers
source_headers = ws['A1':'D1']  # Adjust range based on your data
output_headers = [h.value for h in source_headers]
ws['G1:I1'] = output_headers[:3]  # Write correct subset if needed
```
