# Evaluation sweep — report

**49 functions** run across **13 modules** · **16 flagged** · 31 clean · 2 errored · ~$2.4691 LLM cost.

Discovered 146 public functions; 49 were self-contained (imports-only); ran 49 (cap 80). 97 skipped (companion fn / module constant).

## Per-module
| module | run | flagged |
|---|---|---|
| b.dictutils | 1 | 0 |
| b.formatutils | 1 | 0 |
| b.funcutils | 8 | 2 |
| b.iterutils | 7 | 1 |
| b.mathutils | 3 | 1 |
| b.statsutils | 1 | 1 |
| b.strutils | 12 | 4 |
| b.tableutils | 1 | 1 |
| b.timeutils | 2 | 1 |
| b.typeutils | 2 | 0 |
| b.urlutils | 1 | 0 |
| inflection | 6 | 2 |
| stringcase | 4 | 3 |

## Flagged candidates (manual verification needed)

### b.funcutils.copy_function  (7 finding(s))
- _prop_ `import types; isinstance(copy_function(orig), types.FunctionType)`  [type_postcondition, conf 0.95]
- _prop_ `copy_function(orig) is not orig`  [value_postcondition, conf 0.99]
- _prop_ `copy_function(orig).__name__ == orig.__name__`  [invariant_preservation, conf 0.95]
- _prop_ `copy_function(orig).__code__ is orig.__code__`  [invariant_preservation, conf 0.92]
- _prop_ `copy_function(orig).__globals__ is orig.__globals__`  [invariant_preservation, conf 0.92]
- _prop_ `copy_function(orig, copy_dict=True).__dict__ == orig.__dict__`  [invariant_preservation, conf 0.88]
- _prop_ `copy_function(orig) does not raise for any valid function orig`  [totality, conf 0.75]
- **crash** · violates `import types; isinstance(copy_function(orig), types.FunctionType)` · input `orig=lambda: None` · ImportError
- **crash** · violates `copy_function(orig) is not orig` · input `orig=lambda: None` · ImportError
- **crash** · violates `invariant_preservation_name` · input `orig=lambda: None` · ImportError
- **crash** · violates `invariant_preservation_code` · input `orig=lambda: None` · ImportError
- **crash** · violates `invariant_preservation_globals` · input `orig=lambda: None` · ImportError
- **crash** · violates `invariant_preservation_dict` · input `orig=lambda: None` · ImportError
- **crash** · violates `copy_function(orig) does not raise for any valid function orig` · input `orig=lambda: None, # or any other generated value copy_dict=False, # or any other generated value` · ImportError

```python
def copy_function(orig, copy_dict=True):
    """Returns a shallow copy of the function, including code object,
    globals, closure, etc.

    >>> func = lambda: func
    >>> func() is func
    True
    >>> func_copy = copy_function(func)
    >>> func_copy() is func
    True
    >>> func_copy is not func
    True

    Args:
        orig (function): The function to be copied. Must be a
            function, not just any method or callable.
        copy_dict (bool): Also copy any attributes set on the function
            instance. Defaults to ``True``.
    """
    from builtins import function as FunctionType
    ret = FunctionType(orig.__code__, orig.__globals__, name=orig.__name__, argdefs=getattr(orig, '__defaults__', None), closure=getattr(orig, '__closure__', None))
    if hasattr(orig, '__kwdefaults__'):
        ret.__kwdefaults__ = orig.__kwdefaults__
    if copy_dict:
        ret.__dict__.update(orig.__dict__)
    return ret
```

### b.timeutils.strpdate  (5 finding(s))
- _prop_ `strpdate(date.strftime(d, format), format) == d for a datetime.date object d`  [round_trip, conf 0.82]
- _prop_ `isinstance(strpdate(string, format), datetime.date)`  [type_postcondition, conf 0.99]
- _prop_ `datetime.date.min <= strpdate(string, format) <= datetime.date.max`  [value_postcondition, conf 0.75]
- _prop_ `strpdate(string, format).year == int(year_part) and strpdate(string, format).month == int(month_part) and strpdate(string, format).day == int(day_part) when the format unambiguously encodes year/month/day`  [metamorphic, conf 0.7]
- _prop_ `strpdate(string, format) does not raise for any valid date string matching the given format`  [totality, conf 0.72]
- **crash** · violates `strpdate(date.strftime(d, format), format) == d for a datetime.date object d` · input `| format='%Y%m%d', # or any other generated value | string=d, |` · ExceptionGroup
- **crash** · violates `isinstance(strpdate(string, format), datetime.date)` · input `format='%Y-%m-%d', string=d` · ValueError
- **crash** · violates `datetime.date.min <= strpdate(string, format) <= datetime.date.max` · input `format='%Y-%m-%d', string=d` · ValueError
- **crash** · violates `strpdate(string, format).year == int(year_part) and strpdate(string, format).month == int(month_part) and strpdate(string, format).day == int(day_part) when the format unambiguously encodes year/month/day` · input `| format='%Y%m%d', # or any other generated value | d=datetime.date(999, 1, 1), |` · ExceptionGroup
- **crash** · violates `strpdate(string, format) does not raise for any valid date string matching the given format` · input `format='%Y-%m-%d', d=datetime.date(999, 1, 1)` · ValueError

```python
def strpdate(string, format):
    """Parse the date string according to the format in `format`.  Returns a
    :class:`date` object.  Internally, :meth:`datetime.strptime` is used to
    parse the string and thus conversion specifiers for time fields (e.g. `%H`)
    may be provided;  these will be parsed but ignored.

    Args:
        string (str): The date string to be parsed.
        format (str): The `strptime`_-style date format string.
    Returns:
        datetime.date

    .. _`strptime`: https://docs.python.org/2/library/datetime.html#strftime-strptime-behavior

    >>> strpdate('2016-02-14', '%Y-%m-%d')
    datetime.date(2016, 2, 14)
    >>> strpdate('26/12 (2015)', '%d/%m (%Y)')
    datetime.date(2015, 12, 26)
    >>> strpdate('20151231 23:59:59', '%Y%m%d %H:%M:%S')
    datetime.date(2015, 12, 31)
    >>> strpdate('20160101 00:00:00.001', '%Y%m%d %H:%M:%S.%f')
    datetime.date(2016, 1, 1)
    """
    from datetime import datetime
    whence = datetime.strptime(string, format)
    return whence.date()
```

### b.iterutils.flatten_iter  (4 finding(s))
- _prop_ `list(flatten_iter(flatten_iter(nested))) == list(flatten_iter(nested))`  [idempotence, conf 0.92]
- _prop_ `sum(1 for _ in flatten_iter(nested)) == sum(1 for x in nested_flat_elements(nested)) where nested_flat_elements counts all non-iterable leaf elements — i.e. the total count of leaf elements is preserved`  [invariant_preservation, conf 0.85]
- _prop_ `list(flatten_iter([a, b])) == list(flatten_iter(a)) + list(flatten_iter(b)) for any sub-iterables a and b`  [metamorphic, conf 0.88]
- _prop_ `isinstance(flatten_iter(iterable), types.GeneratorType)`  [type_postcondition, conf 0.95]
- _prop_ `list(flatten_iter(nested)) does not raise for any finitely nested iterable of non-iterable leaves (excluding strings/bytes as containers)`  [totality, conf 0.75]
- **crash** · violates `list(flatten_iter(flatten_iter(nested))) == list(flatten_iter(nested))` · input `iterable=0` · TypeError
- **crash** · violates `sum(1 for _ in flatten_iter(nested)) == sum(1 for x in nested_flat_elements(nested)) where nested_flat_elements counts all non-iterable leaf elements — i.e. the total count of leaf elements is preserved` · input `iterable=0` · TypeError
- **crash** · violates `list(flatten_iter([a, b])) == list(flatten_iter(a)) + list(flatten_iter(b)) for any sub-iterables a and b` · input `a=0, b=[], # or any other generated value` · TypeError
- **crash** · violates `list(flatten_iter(nested)) does not raise for any finitely nested iterable of non-iterable leaves (excluding strings/bytes as containers)` · input `iterable=0` · TypeError

```python
def flatten_iter(iterable):
    """``flatten_iter()`` yields all the elements from *iterable* while
    collapsing any nested iterables.

    >>> nested = [[1, 2], [[3], [4, 5]]]
    >>> list(flatten_iter(nested))
    [1, 2, 3, 4, 5]
    """
    from collections.abc import Iterable
    for item in iterable:
        if isinstance(item, Iterable) and (not isinstance(item, (str, bytes))):
            yield from flatten_iter(item)
        else:
            yield item
```

### b.funcutils.format_invocation  (3 finding(s))
- _prop_ `isinstance(format_invocation(name, args, kwargs), str)`  [type_postcondition, conf 0.98]
- _prop_ `format_invocation(name, args, kwargs).startswith(name + '(') and format_invocation(name, args, kwargs).endswith(')')`  [value_postcondition, conf 0.95]
- _prop_ `format_invocation('', args=(), kwargs={}) == '()'`  [value_postcondition, conf 0.85]
- _prop_ `len(format_invocation(name, args=(a, b), kwargs={})) >= len(format_invocation(name, args=(a,), kwargs={}))  — adding more positional args produces an output at least as long`  [metamorphic, conf 0.75]
- _prop_ `format_invocation(name, args, kwargs) does not raise for any str name, tuple args, and dict/list kwargs`  [totality, conf 0.8]
- **crash** · violates `isinstance(format_invocation(name, args, kwargs), str)` · input `` · hypothesis.errors.Hypoth...
- **crash** · violates `value_postcondition_starts_ends` · input `` · hypothesis....
- **crash** · violates `format_invocation(name, args, kwargs) does not raise for any str name, tuple args, and dict/list kwargs` · input `` · hypothesis.errors.HypothesisExcept...

```python
def format_invocation(name='', args=(), kwargs=None, **kw):
    """Given a name, positional arguments, and keyword arguments, format
    a basic Python-style function call.

    >>> print(format_invocation('func', args=(1, 2), kwargs={'c': 3}))
    func(1, 2, c=3)
    >>> print(format_invocation('a_func', args=(1,)))
    a_func(1)
    >>> print(format_invocation('kw_func', kwargs=[('a', 1), ('b', 2)]))
    kw_func(a=1, b=2)

    """
    _repr = kw.pop('repr', repr)
    if kw:
        raise TypeError('unexpected keyword args: %r' % ', '.join(kw.keys()))
    kwargs = kwargs or {}
    a_text = ', '.join([_repr(a) for a in args])
    if isinstance(kwargs, dict):
        kwarg_items = [(k, kwargs[k]) for k in sorted(kwargs)]
    else:
        kwarg_items = kwargs
    kw_text = ', '.join([f'{k}={_repr(v)}' for k, v in kwarg_items])
    all_args_text = a_text
    if all_args_text and kw_text:
        all_args_text += ', '
    all_args_text += kw_text
    return f'{name}({all_args_text})'
```

### b.strutils.under2camel  (3 finding(s))
- _prop_ `isinstance(under2camel(s), str)`  [type_postcondition, conf 0.97]
- _prop_ `under2camel(s)[0].isupper() for any non-empty s that doesn't start with '_'`  [value_postcondition, conf 0.85]
- _prop_ `'_' not in under2camel(s) for any s that contains no consecutive underscores`  [value_postcondition, conf 0.75]
- _prop_ `under2camel(s.lower()) == under2camel(s) for any already-lowercased underscored string s`  [metamorphic, conf 0.8]
- _prop_ `len(under2camel(s).replace('_', '')) == len(s.replace('_', ''))`  [invariant_preservation, conf 0.85]
- _prop_ `under2camel(s) does not raise for any str input s`  [totality, conf 0.9]
- **crash** · violates `value_postcondition_first_char_upper` · input `under_string='ꟓ'` · Assert...
- **crash** · violates `value_postcondition_no_underscore` · input `under_string=''` · Assertion...
- **crash** · violates `invariant_preservation_length` · input `under_string='ß'` · AssertionErro...

```python
def under2camel(under_string):
    """Converts an underscored string to camelcased. Useful for turning a
    function name into a class name.

    >>> under2camel('complex_tokenizer')
    'ComplexTokenizer'
    """
    return ''.join((w.capitalize() or '_' for w in under_string.split('_')))
```

### b.mathutils.floor  (1 finding(s))
- _prop_ `floor(floor(x)) == floor(x)`  [idempotence, conf 0.85]
- _prop_ `floor(x) <= x`  [value_postcondition, conf 0.97]
- _prop_ `floor(x) > x - 1  (i.e., x - 1 < floor(x) <= x) when options=None`  [value_postcondition, conf 0.9]
- _prop_ `When options is provided: floor(x, options=opts) is an element of opts AND floor(x, options=opts) <= x AND all(v <= floor(x, options=opts) for v in opts if v <= x)`  [value_postcondition, conf 0.93]
- _prop_ `If x1 <= x2 then floor(x1) <= floor(x2)  (monotonicity)`  [metamorphic, conf 0.92]
- _prop_ `isinstance(floor(x), (int, float))`  [type_postcondition, conf 0.9]
- **crash** · violates `value_postcondition_floor_gt_x_minus` · input `x=9007199254740996.0` · asse...

```python
def floor(x, options=None):
    """Return the floor of *x*. If *options* is set, return the largest
    integer or float from *options* that is less than or equal to
    *x*.

    Args:
        x (int or float): Number to be tested.
        options (iterable): Optional iterable of arbitrary numbers
          (ints or floats).

    >>> VALID_CABLE_CSA = [1.5, 2.5, 4, 6, 10, 25, 35, 50]
    >>> floor(3.5, options=VALID_CABLE_CSA)
    2.5
    >>> floor(2.5, options=VALID_CABLE_CSA)
    2.5

    """
    from math import floor as _floor
    import bisect
    if options is None:
        return _floor(x)
    options = sorted(options)
    i = bisect.bisect_right(options, x)
    if not i:
        raise ValueError('no floor options less than or equal to: %r' % x)
    return options[i - 1]
```

### b.statsutils.format_histogram_counts  (1 finding(s))
- _prop_ `isinstance(format_histogram_counts(bin_counts, width, format_bin), str)`  [type_postcondition, conf 0.97]
- _prop_ `format_histogram_counts(bin_counts, width, format_bin).count('\n') == len(bin_counts) - 1`  [value_postcondition, conf 0.82]
- _prop_ `format_histogram_counts(bin_counts, width1) and format_histogram_counts(bin_counts, width2) produce the same number of lines (len(bin_counts)) regardless of width`  [metamorphic, conf 0.75]
- _prop_ `format_histogram_counts(bin_counts) does not raise for any non-empty list of (bin, count) pairs with positive counts`  [totality, conf 0.65]
- **property_violation** · violates `format_histogram_counts(bin_counts, width1) and format_histogram_counts(bin_counts, width2) produce the same number of lines (len(bin_counts)) regardless of width` · input `# The test always failed when commented parts were varied together. bin_counts=[( '\r', 1, # or any other generated valu` · AssertionError

```python
def format_histogram_counts(bin_counts, width=None, format_bin=None):
    """The formatting logic behind :meth:`Stats.format_histogram`, which
    takes the output of :meth:`Stats.get_histogram_counts`, and passes
    them to this function.

    Args:
        bin_counts (list): A list of bin values to counts.
        width (int): Number of character columns in the text output,
            defaults to 80 or console width in Python 3.3+.
        format_bin (callable): Used to convert bin values into string
            labels.
    """
    lines = []
    if not format_bin:
        format_bin = lambda v: v
    if not width:
        try:
            import shutil
            width = shutil.get_terminal_size()[0]
        except Exception:
            width = 80
    bins = [b for b, _ in bin_counts]
    count_max = max([count for _, count in bin_counts])
    count_cols = len(str(count_max))
    labels = ['%s' % format_bin(b) for b in bins]
    label_cols = max([len(l) for l in labels])
    tmp_line = '{}: {} #'.format('x' * label_cols, count_max)
    bar_cols = max(width - len(tmp_line), 3)
    line_k = float(bar_cols) / count_max
    tmpl = '{label:>{label_cols}}: {count:>{count_cols}} {bar}'
    for label, (bin_val, count) in zip(labels, bin_counts):
        bar_len = int(round(count * line_k))
        bar = '#' * bar_len or '|'
        line = tmpl.format(label=label, label_cols=label_cols, count=count, count_cols=count_cols, bar=bar)
        lines.append(line)
    return '\n'.join(lines)
```

### b.strutils.gzip_bytes  (1 finding(s))
- _prop_ `gunzip_bytes(gzip_bytes(b)) == b`  [round_trip, conf 0.95]
- _prop_ `len(gzip_bytes(bytestring, level)) <= len(bytestring) + C for some small constant C (or more precisely, for highly repetitive input like b'a'*10000, len(gzip_bytes(bytestring)) << len(bytestring))`  [value_postcondition, conf 0.55]
- _prop_ `len(gzip_bytes(bytestring, level=1)) >= len(gzip_bytes(bytestring, level=9)) for compressible inputs (higher level => more compression => smaller or equal output)`  [metamorphic, conf 0.85]
- _prop_ `isinstance(gzip_bytes(bytestring, level), bytes)`  [type_postcondition, conf 0.97]
- _prop_ `len(gzip_bytes(bytestring, level)) > 0`  [value_postcondition, conf 0.95]
- _prop_ `gzip_bytes(bytestring, level) does not raise for any bytestring: bytes and level in 1..9`  [totality, conf 0.85]
- **crash** · violates `value_postcondition_compression` · input `` · hypothesis....

```python
def gzip_bytes(bytestring, level=6):
    """Turn some bytes into some compressed bytes.

    >>> len(gzip_bytes(b'a' * 10000))
    46

    Args:
        bytestring (bytes): Bytes to be compressed
        level (int): An integer, 1-9, controlling the
          speed/compression. 1 is fastest, least compressed, 9 is
          slowest, but most compressed.

    Note that all levels of gzip are pretty fast these days, though
    it's not really a competitor in compression, at any level.
    """
    from gzip import GzipFile
    from _io import BytesIO as StringIO
    out = StringIO()
    f = GzipFile(fileobj=out, mode='wb', compresslevel=level)
    f.write(bytestring)
    f.close()
    return out.getvalue()
```

### b.strutils.human_readable_list  (1 finding(s))
- _prop_ `isinstance(human_readable_list(items), str)`  [type_postcondition, conf 0.99]
- _prop_ `all(item in human_readable_list(items) for item in items) when items is non-empty`  [value_postcondition, conf 0.92]
- _prop_ `human_readable_list([]) == ''`  [value_postcondition, conf 0.97]
- _prop_ `human_readable_list([s]) == s for any single-element list`  [value_postcondition, conf 0.95]
- _prop_ `human_readable_list(items, conjunction='and') contains 'and' when len(items) >= 2`  [metamorphic, conf 0.88]
- _prop_ `human_readable_list(items, oxford=True) != human_readable_list(items, oxford=False) when len(items) >= 3 (assuming non-empty delimiter)`  [metamorphic, conf 0.85]
- _prop_ `human_readable_list(items, delimiter, conjunction, oxford=oxford) does not raise for any Sequence[str] items and str delimiter/conjunction`  [totality, conf 0.75]
- **crash** · violates `value_postcondition_empty_list` · input `` · hypothesis.e...

```python
def human_readable_list(items: typing.Sequence[str], delimiter: str=',', conjunction: str='and', *, oxford: bool=True) -> str:
    """
    Given a list of strings, return a human readable string with
    appropriate delimiters and the conjunction word.

    Args:
        items: The list of strings to join.
        delimiter (optional): The delimiter to use between items.
        conjunction (optional): The word to use before the last item.
        oxford (optional): Whether to use the Oxford comma/delimiter before
            the conjunction in lists of 3+ items.

    Returns:
        str: The human readable string.
    """
    if not items:
        return ''
    delimiter = delimiter and delimiter.strip() + ' '
    conjunction = conjunction.strip()
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f'{items[0]} {conjunction} {items[1]}'
    return f'{delimiter.join(items[:-1])}{(delimiter if oxford else ' ')}{conjunction} {items[-1]}'
```

### b.strutils.unwrap_text  (1 finding(s))
- _prop_ `unwrap_text(unwrap_text(text)) == unwrap_text(text)`  [idempotence, conf 0.85]
- _prop_ `unwrap_text(text) == unwrap_text(text.replace('\n', ' \n ')) — extra surrounding whitespace on lines does not change the output, since each line is stripped before joining.`  [metamorphic, conf 0.8]
- _prop_ `The number of paragraphs (double-newline-separated blocks) in unwrap_text(text) equals the number of non-empty paragraph blocks in the original text.`  [invariant_preservation, conf 0.82]
- _prop_ `isinstance(unwrap_text(text, ending), str) when ending is not None, and isinstance(unwrap_text(text, None), list) when ending is None.`  [type_postcondition, conf 0.95]
- _prop_ `When ending is not None, unwrap_text(text, ending) does not contain any lone newlines (i.e., '\n' not in unwrap_text(text, ending).replace(ending, '')) — all intra-paragraph newlines are collapsed into spaces.`  [value_postcondition, conf 0.75]
- **property_violation** · violates `unwrap_text(unwrap_text(text)) == unwrap_text(text)` · input `text='\x1e0'` · AssertionError

```python
def unwrap_text(text, ending='\n\n'):
    """
    Unwrap text, the natural complement to :func:`textwrap.wrap`.

    >>> text = "Short \\n lines  \\nwrapped\\nsmall.\\n\\nAnother\\nparagraph."
    >>> unwrap_text(text)
    'Short lines wrapped small.\\n\\nAnother paragraph.'

    Args:
       text: A string to unwrap.
       ending (str): The string to join all unwrapped paragraphs
          by. Pass ``None`` to get the list. Defaults to '\\n\\n' for
          compatibility with Markdown and RST.

    """
    all_grafs = []
    cur_graf = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            cur_graf.append(line)
        else:
            all_grafs.append(' '.join(cur_graf))
            cur_graf = []
    if cur_graf:
        all_grafs.append(' '.join(cur_graf))
    if ending is None:
        return all_grafs
    return ending.join(all_grafs)
```

### b.tableutils.to_text  (1 finding(s))
- _prop_ `isinstance(to_text(obj, maxlen), str)`  [type_postcondition, conf 0.98]
- _prop_ `maxlen is not None and maxlen > 3 implies len(to_text(obj, maxlen)) <= maxlen`  [value_postcondition, conf 0.92]
- _prop_ `to_text(to_text(obj, maxlen), maxlen) == to_text(obj, maxlen)`  [idempotence, conf 0.82]
- _prop_ `to_text(obj, maxlen) does not raise for any obj and any maxlen`  [totality, conf 0.97]
- **property_violation** · violates `to_text(to_text(obj, maxlen), maxlen) == to_text(obj, maxlen)` · input `obj=None, maxlen=1` · AssertionError

```python
def to_text(obj, maxlen=None):
    try:
        text = str(obj)
    except Exception:
        try:
            text = str(repr(obj))
        except Exception:
            text = str(object.__repr__(obj))
    if maxlen and len(text) > maxlen:
        text = text[:maxlen - 3] + '...'
    return text
```

### inflection.humanize  (1 finding(s))
- _prop_ `humanize(humanize(word)) == humanize(word)`  [idempotence, conf 0.75]
- _prop_ `humanize(word.upper()) == humanize(word) and humanize(word.lower()) == humanize(word)`  [metamorphic, conf 0.9]
- _prop_ `humanize(word)[0] == humanize(word)[0].upper() if humanize(word) else True`  [value_postcondition, conf 0.92]
- _prop_ `'_' not in humanize(word)`  [value_postcondition, conf 0.97]
- _prop_ `not humanize(word).endswith('_id') and not humanize(word).endswith(' id')`  [value_postcondition, conf 0.85]
- _prop_ `isinstance(humanize(word), str)`  [type_postcondition, conf 0.99]
- _prop_ `humanize(word) does not raise for any str input word`  [totality, conf 0.95]
- **crash** · violates `humanize(word.upper()) == humanize(word) and humanize(word.lower()) == humanize(word)` · input `| word='0À', |` · ExceptionGroup

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

### inflection.ordinal  (1 finding(s))
- _prop_ `ordinal(number) in ('st', 'nd', 'rd', 'th')`  [value_postcondition, conf 0.97]
- _prop_ `isinstance(ordinal(number), str)`  [type_postcondition, conf 0.99]
- _prop_ `ordinal(number) == ordinal(abs(number))`  [invariant_preservation, conf 0.95]
- _prop_ `ordinal(number) == ordinal(number + 1000) for all number not crossing a 11/12/13 boundary in the last two digits`  [metamorphic, conf 0.75]
- _prop_ `ordinal(number) does not raise for any int input`  [totality, conf 0.85]
- **property_violation** · violates `ordinal(number) == ordinal(number + 1000) for all number not crossing a 11/12/13 boundary in the last two digits` · input `number=-1` · AssertionError

```python
def ordinal(number: int) -> str:
    """
    Return the suffix that should be added to a number to denote the position
    in an ordered sequence such as 1st, 2nd, 3rd, 4th.

    Examples::

        >>> ordinal(1)
        'st'
        >>> ordinal(2)
        'nd'
        >>> ordinal(1002)
        'nd'
        >>> ordinal(1003)
        'rd'
        >>> ordinal(-11)
        'th'
        >>> ordinal(-1021)
        'st'

    """
    number = abs(int(number))
    if number % 100 in (11, 12, 13):
        return 'th'
    else:
        return {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
```

### stringcase.alphanumcase  (1 finding(s))
- _prop_ `alphanumcase(alphanumcase(s)) == alphanumcase(s)`  [idempotence, conf 0.97]
- _prop_ `re.fullmatch(r'[0-9a-zA-Z]*', alphanumcase(s)) is not None`  [value_postcondition, conf 0.97]
- _prop_ `len(alphanumcase(s)) <= len(s)`  [invariant_preservation, conf 0.95]
- _prop_ `set(alphanumcase(s)).issubset(set(s))`  [invariant_preservation, conf 0.93]
- _prop_ `isinstance(alphanumcase(s), str)`  [type_postcondition, conf 0.99]
- _prop_ `alphanumcase(s1 + s2) == alphanumcase(s1) + alphanumcase(s2)`  [metamorphic, conf 0.9]
- **property_violation** · violates `re.fullmatch(r'[0-9a-zA-Z]*', alphanumcase(s)) is not None` · input `string='²'` · AssertionError

```python
def alphanumcase(string):
    """Cuts all non-alphanumeric symbols,
    i.e. cuts all expect except 0-9, a-z and A-Z.

    Args:
        string: String to convert.

    Returns:
        string: String with cutted non-alphanumeric symbols.

    """
    import re
    return re.sub('\\W+', '', string)
```

### stringcase.lowercase  (1 finding(s))
- _prop_ `lowercase(lowercase(s)) == lowercase(s)`  [idempotence, conf 0.99]
- _prop_ `lowercase(s) == lowercase(s.upper()) == lowercase(s.lower())`  [metamorphic, conf 0.95]
- _prop_ `lowercase(s) == lowercase(s).lower()`  [value_postcondition, conf 0.97]
- _prop_ `isinstance(lowercase(s), str)`  [type_postcondition, conf 0.99]
- **property_violation** · violates `lowercase(s) == lowercase(s.upper()) == lowercase(s.lower())` · input `string='ß'` · AssertionError

```python
def lowercase(string):
    """Convert string into lower case.

    Args:
        string: String to convert.

    Returns:
        string: Lowercase case string.

    """
    return str(string).lower()
```

### stringcase.uppercase  (1 finding(s))
- _prop_ `uppercase(uppercase(s)) == uppercase(s)`  [idempotence, conf 0.98]
- _prop_ `uppercase(s) == uppercase(s.upper())`  [metamorphic, conf 0.95]
- _prop_ `uppercase(s) == uppercase(s.lower())`  [metamorphic, conf 0.92]
- _prop_ `len(uppercase(s)) == len(s)`  [invariant_preservation, conf 0.97]
- _prop_ `uppercase(s) == uppercase(s).upper()`  [value_postcondition, conf 0.97]
- _prop_ `isinstance(uppercase(s), str)`  [type_postcondition, conf 0.99]
- **property_violation** · violates `len(uppercase(s)) == len(s)` · input `string='ß'` · AssertionError

```python
def uppercase(string):
    """Convert string into upper case.

    Args:
        string: String to convert.

    Returns:
        string: Uppercase case string.

    """
    return str(string).upper()
```

## Clean (no findings)
b.dictutils.subdict, b.formatutils.construct_format_field_str, b.funcutils.dir_dict, b.funcutils.format_nonexp_repr, b.funcutils.get_module_callables, b.funcutils.inspect_formatargspec, b.funcutils.mro_items, b.funcutils.partial_ordering, b.iterutils.backoff_iter, b.iterutils.frange, b.iterutils.is_iterable, b.iterutils.lstrip_iter, b.iterutils.rstrip_iter, b.mathutils.ceil, b.mathutils.clamp, b.strutils.a10n, b.strutils.args2cmd, b.strutils.format_int_list, b.strutils.gunzip_bytes, b.strutils.is_ascii, b.strutils.is_uuid, b.strutils.parse_int_list, b.strutils.removeprefix, b.typeutils.get_all_subclasses, b.typeutils.issubclass, b.urlutils.resolve_path_parts, inflection.camelize, inflection.dasherize, inflection.transliterate, inflection.underscore, stringcase.trimcase

## Errored
- [504] b.iterutils.xfrange — {'detail': 'Test execution exceeded the 30.0s time budget.'}
- [504] b.timeutils.daterange — {'detail': 'Test execution exceeded the 30.0s time budget.'}
