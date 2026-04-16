---
name: filter-parsing-patterns
description: Patterns for parsing complex filter strings with mixed syntax
---

# Parsing Complex Filter Strings

Filter expressions often contain mixed syntax formats that require flexible parsing.

## Problem
Filter strings may combine different formats:
- Single value: `"orgId" = "LIM"`
- Multiple values: `"contextId" IN LIST 98` or `"specialtyId" IN LIST 66,77`
- Mixed order and combinations

## Solution Pattern

```python
import re
from typing import Dict, List, Any

def parse_filter_string(filter_str: str) -> Dict[str, Any]:
    """
    Parse filter string with mixed syntax into structured dict.
    Handles single equals and IN LIST formats.
    """
    result = {}
    
    # Pattern for single value: "field" = "value"
    single_pattern = r'"([^"]+)"\s*=\s*"([^"]+)"'
    for match in re.finditer(single_pattern, filter_str):
        field, value = match.groups()
        result[field] = value
    
    # Pattern for IN LIST: "field" IN LIST val1,val2,...
    in_list_pattern = r'"([^"]+)"\s+IN\s+LIST\s+([\d,]+)'
    for match in re.finditer(in_list_pattern, filter_str):
        field, values_str = match.groups()
        result[field] = [int(v.strip()) for v in values_str.split(',')]
    
    return result
```

## Key Principles

1. **Separate regex per field type** - Different patterns for different syntaxes
2. **Independent searching** - Each regex finds all matches regardless of order
3. **Type-aware parsing** - Convert to appropriate types (int vs string)

## Success Memory
*This pattern has been validated: separate regex searches for each field type allowed flexible extraction of filter conditions containing mixed formats (single equals vs IN LIST) regardless of their order in the filter string.*

## Testing Checklist

- [ ] Single value filters: `"field" = "value"`
- [ ] Multi-value filters: `"field" IN LIST 1,2,3`
- [ ] Mixed filters: combination of both formats
- [ ] Field ordering variations: filters in different orders
- [ ] Whitespace variations: extra spaces between tokens