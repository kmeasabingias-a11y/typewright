# Pilot sweep — candidate report

Ran 9 self-contained functions · 2 flagged candidates · 7 clean · 0 errored · ~$0.3796 LLM cost · 17 skipped (not self-contained)

## Flagged candidates (need manual verification)

### boltons.mathutils.clamp  (1 finding(s))
- _property_ `clamp(clamp(x, lower, upper), lower, upper) == clamp(x, lower, upper)`  [idempotence, conf 0.98]
- _property_ `lower <= clamp(x, lower, upper) <= upper`  [value_postcondition, conf 0.99]
- _property_ `clamp(x, lower, upper) <= clamp(y, lower, upper) when x <= y (monotonicity)`  [metamorphic, conf 0.92]
- _property_ `clamp(x, lower, upper) == clamp(x, lower, upper2) when upper2 >= upper and clamp(x, lower, upper) <= upper (widening the upper bound does not change the result when x is already within range)`  [metamorphic, conf 0.8]
- _property_ `clamp(x, lower, upper) does not raise for any x, lower, upper where lower <= upper`  [totality, conf 0.85]
- **property_violation** · violates `metamorphic_widening_upper` · input `x=0, lower=-1, upper=-1.0, upper2=0` · AssertionError (test_metamorphic_widening_upper)

```python
def clamp(x, lower=float('-inf'), upper=float('inf')):
    """Limit a value to a given range.

    Args:
        x (int or float): Number to be clamped.
        lower (int or float): Minimum value for x.
        upper (int or float): Maximum value for x.

    The returned value is guaranteed to be between *lower* and
    *upper*. Integers, floats, and other comparable types can be
    mixed.

    >>> clamp(1.0, 0, 5)
    1.0
    >>> clamp(-1.0, 0, 5)
    0
    >>> clamp(101.0, 0, 5)
    5
    >>> clamp(123, upper=5)
    5

    Similar to `numpy's clip`_ function.

    .. _numpy's clip: http://docs.scipy.org/doc/numpy/reference/generated/numpy.clip.html

    """
    if upper < lower:
        raise ValueError('expected upper bound (%r) >= lower bound (%r)' % (upper, lower))
    return min(max(x, lower), upper)
```

### inflection.humanize  (1 finding(s))
- _property_ `humanize(humanize(word)) == humanize(word)`  [idempotence, conf 0.82]
- _property_ `humanize(word.upper()) == humanize(word) and humanize(word.lower()) == humanize(word)`  [metamorphic, conf 0.85]
- _property_ `humanize(word)[0] == humanize(word)[0].upper() if humanize(word) else True`  [value_postcondition, conf 0.9]
- _property_ `'_' not in humanize(word)`  [value_postcondition, conf 0.95]
- _property_ `not humanize(word).endswith('_id') and not humanize(word).endswith(' id')`  [value_postcondition, conf 0.88]
- _property_ `isinstance(humanize(word), str)`  [type_postcondition, conf 0.99]
- **crash** · violates `humanize(word.upper()) == humanize(word) and humanize(word.lower()) == humanize(word)` · input `| word='_id', |` · ExceptionGroup (test_metamorphic)

```python
def humanize(word: str) -> str:
    """
    Capitalize the first word and turn underscores into spaces and strip a
    trailing ``"_id"``, if any. Like :func:`titleize`, this is meant for
    creating pretty output.

    Examples::

        >>> humanize("employee_salary")
        'Employee salary'
        >>> humanize("author_id")
        'Author'

    """
    import re
    word = re.sub('_id$', '', word)
    word = word.replace('_', ' ')
    word = re.sub('(?i)([a-z\\d]*)', lambda m: m.group(1).lower(), word)
    word = re.sub('^\\w', lambda m: m.group(0).upper(), word)
    return word
```

## Clean (no findings)
boltons.iterutils.first, boltons.strutils.under2camel, inflection.camelize, inflection.dasherize, inflection.ordinal, inflection.transliterate, inflection.underscore

## Errored

## Skipped (not self-contained — companion fn or module constant)
- inflection.titleize — needs humanize, underscore
- inflection.ordinalize — needs ordinal
- inflection.pluralize — needs PLURALS, UNCOUNTABLES
- inflection.singularize — needs SINGULARS, UNCOUNTABLES
- inflection.parameterize — needs transliterate
- inflection.tableize — needs pluralize, underscore
- boltons.strutils.slugify — needs asciify, split_punct_ws
- boltons.strutils.camel2under — needs _camel2under_re
- boltons.strutils.ordinalize — needs _ORDINAL_MAP
- boltons.strutils.cardinalize — needs pluralize
- boltons.strutils.bytes2human — needs _SIZE_RANGES
- boltons.strutils.strip_ansi — needs ANSI_SEQUENCES
- boltons.mathutils.ceil — needs _ceil
- boltons.mathutils.floor — needs _floor
- humanize.ordinal — needs P_, _format_not_finite
- humanize.intcomma — needs _format_not_finite, decimal_separator, thousands_separator
- humanize.apnumber — needs _, _format_not_finite
