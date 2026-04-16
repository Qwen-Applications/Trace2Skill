---
name: pattern-extraction
description: Advanced techniques for extracting structured data from semi-structured text fields
---

# Pattern Extraction for Semi-Structured Text

## Overview

When Excel cells contain composite strings with embedded identifiers (form names, codes, labels), use regex pattern matching to extract specific components.

## Common Patterns

### Extract Form Names from Composite Strings

**Problem**: Cells contain concatenated values like:
```
ZPAM_FLEXI+U4_CompPropertyRelation+U4_ComponentPropertyForm+...
```

**Solution**: Use regex capture groups to extract matching components:

```python
import re

entry = "ZPAM_FLEXI+U4_CompPropertyRelation+U4_ComponentPropertyForm+..."
pattern = r'(U4_[A-Za-z]+Form)'  # Capture group for form names
matches = re.findall(pattern, entry)
# Result: ['U4_CompPropertyRelation', 'U4_ComponentPropertyForm']
```

### General Pattern Template

```python
import re

# Define your pattern with capture groups
pattern = r'PREFIX_(\w+)SUFFIX'  # Captures content between PREFIX_ and SUFFIX

# Find all matches
extracted = re.findall(pattern, text_field)

# Use extracted values for lookups or filtering
for value in extracted:
    if value in reference_data:
        # Process matched value
        pass
```

## Cross-Sheet Joining Without Foreign Keys

When sheets lack explicit join keys, use this two-step pattern:

```python
import pandas as pd
from openpyxl import load_workbook

# Step 1: Load both sheets
wb = load_workbook('data.xlsx')
sheet1_df = pd.read_excel('data.xlsx', sheet_name='Sheet1')
sheet2_df = pd.read_excel('data.xlsx', sheet_name='Sheet2')

# Step 2: Parse identifiers from Sheet1 entries
def extract_form_names(entry):
    pattern = r'(U4_[A-Za-z]+Form)'
    return re.findall(pattern, str(entry))

sheet1_df['parsed_forms'] = sheet1_df['Entries'].apply(extract_form_names)

# Step 3: Match against Sheet2 column headers
sheet2_columns = list(sheet2_df.columns)
matching_forms = [f for f in sheet1_df['parsed_forms'].explode().unique() if f in sheet2_columns]

# Step 4: Use matched forms to select corresponding columns
result = sheet2_df[matching_forms]
```

## Key Principles

1. **Capture Groups**: Use parentheses `()` in regex to isolate the part you need
2. **Test Patterns First**: Verify regex works on sample data before applying broadly
3. **Handle Missing Matches**: Always check if extracted values exist before using them
4. **Explode Lists**: When extraction returns multiple values per cell, use `.explode()` to flatten

## Common Regex Patterns

| Pattern | Example Input | Extracted |
|---------|---------------|----------|
| `(\d+)` | "ID: 12345" | "12345" |
| `([A-Z]{3})` | "ABC-123" | "ABC" |
| `(\w+_\w+)` | "form_type_001" | "form_type_001" |
| `(U4_[A-Za-z]+)` | "U4_PropertyForm" | "U4_PropertyForm" |

## Error Prevention

- Wrap regex operations in try/except for malformed data
- Check for empty results before using extracted values
- Validate extracted values against expected formats
- Log unmatched entries for manual review