---
name: xlsx-35B
description: Use when handling spreadsheet tasks in .xlsx files with the released 35B created-from-scratch skill
---

# XLSX Task Solving

1. Read the task carefully and identify the target workbook, sheet, cells, or columns
2. Use Python to inspect the workbook structure BEFORE editing — map exact locations of source data (columns, rows) and verify your assumptions match the actual layout
3. Determine if the task requires formulas or pre-computed values based on evaluation criteria
4. Make the smallest change that satisfies the task
5. Re-open or recalculate the workbook and verify the expected cells changed correctly
6. State clearly what changed and where

### Critical: Interpreting answer_position
- When task specifies `answer_position` (e.g., `Sheet1'!A1:F14`), ALL modifications MUST occur within that exact sheet/range
- DO NOT write results to a different sheet or create duplicates elsewhere
- If Sheet1 contains source data and Sheet2 has reference samples, modify Sheet1 in-place unless explicitly told otherwise
- answer_position like `'工作表1'!I2:J7` defines a RANGE that includes ALL cells from start to end
- Headers and data must align within this range, even if some cells remain empty
- Verify final workbook matches the complete range specification, not just populated cells

## Useful Tools
- `openpyxl` - read and edit `.xlsx` files, sheets, cells, formulas, and styles
- `pandas` - inspect tables, filter rows, compare values, and summarize data
- `zipfile` and XML tools - inspect workbook internals when workbook structure looks suspicious


### References
- See [Formula Patterns](references/formula-patterns.md) for detailed formula implementation guide
- See [Date Handling](references/date-handling.md) for safe date parsing patterns
- See [Formula Evaluation](references/formula-evaluation.md) for VLOOKUP/wildcard patterns
- See [Lookup Patterns](references/lookup-patterns.md) for cross-reference and iteration strategies
- See [Data Manipulation Patterns](references/data-manipulation-patterns.md) for tested transformation patterns
- See [In-List Filters](references/in-list-filters.md) for efficient filtering approaches

## Tips
- Check exact sheet names before reading or writing cells
- Verify row and column indexing carefully when converting between table views and Excel coordinates
- Preserve untouched sheets, formulas, and formatting unless the task requires changing them
- Read the workbook back after editing to confirm it contains the expected values


### Lookup Patterns
- **Same-row lookups**: Search parameters and target data exist in the same row
- **Cross-row lookups**: Search parameters exist outside the target row; must scan ALL rows to find matches
- **DO NOT** assume `row[i]` contains both search params AND target value — verify if the task requires iterating through the entire dataset
- When parameters include None values but expected results exist elsewhere, suspect cross-row pattern requiring full iteration

### Working with Data Structure Validation
- When dealing with structured text (dates, IDs, codes), ALWAYS inspect sample values first using pandas head() or openpyxl cell access
- Verify your parsing assumptions match actual data format before applying formulas or transformations
- Test your parsing logic on 2-3 known values before processing the full dataset

### Critical: Cell Object Handling
- **NEVER compare Cell objects directly to strings** — always extract `.value` first
- Use `iter_rows(values_only=True)` for raw Python values, or access `.value` on each Cell
- Example: `if row[0].value == "TOTAL":` not `if row[0] == "TOTAL":`
- When assigning values, use `cell.value = new_value`, not `cell = new_value`

### Action Validation
- Always validate your action JSON follows exact schema: `{"name": "<tool>", "arguments": {...}}`
- Check for extra braces, markdown formatting, or incorrect nesting before submitting
- Invalid JSON causes immediate rejection - no code will execute


## JSON & Script Formatting

#### JSON Action Structure
- Always use **single braces** `{}` in JSON actions, never double braces `{{}}`
- Correct format: `{"name": "python", "arguments": {"code": "..."}}`
- Bash tool calls MUST use exactly TWO closing braces: `{{"name": "bash", "arguments": {{...}}}}`
- For multi-line code payloads, properly escape: `"` → `\"`, `
` → `\n`, `	` → `\t`
- Validate JSON locally with `python -c "import json; json.loads(your_json)"` before submitting

#### Code Execution Best Practices
- **DO NOT** use bash heredoc syntax (`cat > file << 'EOF'`) within action blocks — it causes JSON parsing failures
- Use inline execution instead: `python3 -c "..."` with properly escaped quotes
- Prefer single-line commands for simple operations; break complex scripts into multiple smaller commands when needed
- For longer scripts: `echo 'code' > file.py && python3 file.py`

#### Debugging Protocol
- After seeing "Failed to parse your action" twice, STOP generating identical commands immediately
- Examine the error message pattern — is it consistent?
- Check your JSON structure: single braces? Valid JSON syntax?
- Try a minimal diagnostic command to confirm parsing works
- Break out of repetition cycles immediately — try alternative approach
- Monitor turn count; adapt strategy early to avoid exhausting limits
