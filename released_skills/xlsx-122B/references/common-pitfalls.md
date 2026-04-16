---
name: common-pitfalls
description: Technical pitfalls when editing XLSX files
---

# Common XLSX Editing Pitfalls

Read this when working with:
- Complex formulas containing nested quotes
- Conditional formatting color matching
- Formula text substitution/modification

## String Escaping in Code Generation

When embedding Python code with complex strings (formulas, paths, etc.) in heredocs or shell commands:

**Problem**: Unterminated string literals cause SyntaxError

**Solution**:
```python
# Use raw strings for paths
path = r'/path/to/file.xlsx'

# Escape quotes in formulas
formula = '=IF(A1>"test", "pass", "fail")'

# Or use triple quotes for multi-line
script = '''
import openpyxl
wb = openpyxl.load_workbook("data.xlsx")
'''
```

**Best Practice**: Test code logic separately before embedding in shell commands.

## Color Format: ARGB vs RGBA

openpyxl uses **ARGB** format (Alpha + RGB), not RGBA:

| Format | Example | Meaning |
|--------|---------|----------|
| ARGB | `FFFFFF00` | Yellow (full alpha, R=FF G=FF B=00) |
| ARGB | `FF00FF00` | Green |
| ARGB | `80FF0000` | Semi-transparent red |

**Always document the color format interpretation** before applying color-matching logic.

## Formula Preservation After Modification

After substituting formula text, validate the result:

**Checklist**:
1. Verify formula syntax is valid Excel (matching parentheses, commas)
2. Confirm cell references weren't broken by substitution
3. Re-open workbook and check computed values (not just formula text)
4. Look for `#NAME?`, `#VALUE!`, or other error indicators

**Validation Step**:
```python
wb.save("output.xlsx")
wb_verify = openpyxl.load_workbook("output.xlsx")
cell_value = wb_verify['Sheet1']['A1'].value
if isinstance(cell_value, str) and cell_value.startswith('#'):
    raise ValueError(f"Formula produced error: {cell_value}")
```