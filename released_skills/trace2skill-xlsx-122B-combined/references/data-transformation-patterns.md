# Data Transformation Patterns

Common patterns for normalizing and transforming spreadsheet data.

## Split Multi-Line Cell Values into Rows

When cells contain newline-delimited values (e.g., `'100\n200\n300'`), split them into separate rows:

```python
# Normalize compact format to tall format
df['values'] = df['values'].str.split('\n')
df_exploded = df.explode('values')
```

This transforms wide/compact data into a normalized structure suitable for analysis.

## Preserve Empty Entries During Transformation

When normalizing, explicitly handle missing values by adding placeholder rows rather than skipping:

```python
# Check for empty/None values before filtering
if values_str is None or str(values_str).strip() == '':
    result.append([category, ''])  # Add placeholder row
else:
    # Process actual values
    pass
```

This ensures completeness when downstream processes expect all categories represented.

## Verify Row Counts Before Implementation

Before coding transformations, calculate expected output dimensions:

```python
# Pre-calculate expected row count per category
category_counts = df.groupby('category').agg({
    'values': lambda x: sum(len(str(v).split('\n')) if pd.notna(v) else 1 
                            for v in x)
})
total_expected = category_counts.sum().iloc[0]
```

Use this as both a correctness check during development and validation criteria after execution.

## Common Pitfalls

- **NaN handling**: Use `pd.notna()` to check for null values before splitting
- **Whitespace**: Strip whitespace from split values: `.str.strip()`
- **Data types**: Convert numeric strings after splitting: `pd.to_numeric(..., errors='coerce')`
- **Index reset**: After explode/transformation, call `df.reset_index(drop=True)`