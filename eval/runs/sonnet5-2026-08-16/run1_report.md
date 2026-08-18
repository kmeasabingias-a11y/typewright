# Evaluation sweep — report

**49 functions** run across **13 modules** · **15 flagged** · 33 clean · 1 errored · ~$1.8039 LLM cost.

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
| b.strutils | 12 | 2 |
| b.tableutils | 1 | 1 |
| b.timeutils | 2 | 1 |
| b.typeutils | 2 | 1 |
| b.urlutils | 1 | 0 |
| inflection | 6 | 2 |
| stringcase | 4 | 3 |

## Flagged candidates (manual verification needed)

### b.funcutils.copy_function  (6 finding(s))
- _prop_ `copy_function(f).__name__ == f.__name__ and copy_function(f).__code__ == f.__code__ and copy_function(f).__defaults__ == f.__defaults__`  [invariant_preservation, conf 0.8]
- _prop_ `copy_function(f, copy_dict=True).__dict__ == f.__dict__`  [invariant_preservation, conf 0.75]
- _prop_ `copy_function(f) is not f`  [value_postcondition, conf 0.9]
- _prop_ `isinstance(copy_function(f), type(f)) or callable(copy_function(f))`  [type_postcondition, conf 0.85]
- _prop_ `copy_function(f)() == f() for a callable f whose behavior is deterministic`  [metamorphic, conf 0.7]
- _prop_ `copy_function(f) does not raise for any valid function f`  [totality, conf 0.4]
- **property_violation** · violates `copy_function(f).__name__ == f.__name__ and copy_function(f).__code__ == f.__code__ and copy_function(f).__defaults__ == f.__defaults__` · input `orig=lambda: None` · AssertionError
- **property_violation** · violates `copy_function(f, copy_dict=True).__dict__ == f.__dict__` · input `orig=lambda: None` · AssertionError
- **property_violation** · violates `copy_function(f) is not f` · input `orig=lambda: None` · AssertionError
- **property_violation** · violates `isinstance(copy_function(f), type(f)) or callable(copy_function(f))` · input `orig=lambda: None` · AssertionError
- **property_violation** · violates `copy_function(f)() == f() for a callable f whose behavior is deterministic` · input `orig=lambda: None` · AssertionError
- **property_violation** · violates `copy_function(f) does not raise for any valid function f` · input `orig=lambda: None, copy_dict=False` · AssertionError

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

### b.typeutils.get_all_subclasses  (5 finding(s))
- _prop_ `isinstance(get_all_subclasses(cls), list) and all(isinstance(t, type) for t in get_all_subclasses(cls))`  [type_postcondition, conf 0.9]
- _prop_ `all(issubclass(t, cls) for t in get_all_subclasses(cls))`  [invariant_preservation, conf 0.85]
- _prop_ `len(set(get_all_subclasses(cls))) == len(get_all_subclasses(cls))`  [invariant_preservation, conf 0.8]
- _prop_ `set(get_all_subclasses(cls)) == set(get_all_subclasses(cls))`  [idempotence, conf 0.3]
- _prop_ `get_all_subclasses(cls) does not raise for any class object cls`  [totality, conf 0.4]
- **crash** · violates `isinstance(get_all_subclasses(cls), list) and all(isinstance(t, type) for t in get_all_subclasses(cls))` · input `cls=type` · TypeError
- **crash** · violates `all(issubclass(t, cls) for t in get_all_subclasses(cls))` · input `cls=type` · TypeError
- **crash** · violates `len(set(get_all_subclasses(cls))) == len(get_all_subclasses(cls))` · input `cls=type` · TypeError
- **crash** · violates `set(get_all_subclasses(cls)) == set(get_all_subclasses(cls))` · input `cls=type` · TypeError
- **crash** · violates `get_all_subclasses(cls) does not raise for any class object cls` · input `cls=type` · TypeError

```python
def get_all_subclasses(cls):
    """Recursively finds and returns a :class:`list` of all types
    inherited from *cls*.

    >>> class A(object):
    ...     pass
    ...
    >>> class B(A):
    ...     pass
    ...
    >>> class C(B):
    ...     pass
    ...
    >>> class D(A):
    ...     pass
    ...
    >>> [t.__name__ for t in get_all_subclasses(A)]
    ['B', 'D', 'C']
    >>> [t.__name__ for t in get_all_subclasses(B)]
    ['C']

    """
    from collections import deque
    try:
        to_check = deque(cls.__subclasses__())
    except (AttributeError, TypeError):
        raise TypeError('expected type object, not %r' % cls)
    seen, ret = (set(), [])
    while to_check:
        cur = to_check.popleft()
        if cur in seen:
            continue
        ret.append(cur)
        seen.add(cur)
        to_check.extend(cur.__subclasses__())
    return ret
```

### b.iterutils.flatten_iter  (4 finding(s))
- _prop_ `list(flatten_iter(list(flatten_iter(nested)))) == list(flatten_iter(nested))`  [idempotence, conf 0.85]
- _prop_ `sorted of all non-iterable leaf elements collected recursively from nested equals sorted(list(flatten_iter(nested)))`  [invariant_preservation, conf 0.75]
- _prop_ `list(flatten_iter([a, b])) == list(flatten_iter([a])) + list(flatten_iter([b]))`  [metamorphic, conf 0.6]
- _prop_ `all(not isinstance(x, Iterable) or isinstance(x, (str, bytes)) for x in flatten_iter(nested))`  [type_postcondition, conf 0.7]
- _prop_ `list(flatten_iter(iterable)) does not raise for any well-formed (non-circular) nested iterable input`  [totality, conf 0.4]
- **crash** · violates `list(flatten_iter(list(flatten_iter(nested)))) == list(flatten_iter(nested))` · input `nested=0` · TypeError
- **crash** · violates `sorted of all non-iterable leaf elements collected recursively from nested equals sorted(list(flatten_iter(nested)))` · input `| nested='', |` · ExceptionGroup
- **crash** · violates `all(not isinstance(x, Iterable) or isinstance(x, (str, bytes)) for x in flatten_iter(nested))` · input `nested=0` · TypeError
- **crash** · violates `list(flatten_iter(iterable)) does not raise for any well-formed (non-circular) nested iterable input` · input `iterable=0` · TypeError

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

### b.statsutils.format_histogram_counts  (3 finding(s))
- _prop_ `isinstance(format_histogram_counts(bin_counts), str)`  [type_postcondition, conf 0.9]
- _prop_ `format_histogram_counts(bin_counts).count(chr(10)) + 1 == len(bin_counts)`  [invariant_preservation, conf 0.7]
- _prop_ `format_histogram_counts(bin_counts, width=w1) and format_histogram_counts(bin_counts, width=w2) both have identical number of lines regardless of width, only bar length/padding differs`  [metamorphic, conf 0.5]
- _prop_ `format_histogram_counts(bin_counts) does not raise for well-formed non-empty bin_counts lists with positive counts`  [totality, conf 0.4]
- **crash** · violates `isinstance(format_histogram_counts(bin_counts), str)` · input `bin_counts=[( 0.0, # or any other generated value 0, )]` · ZeroDivisionError
- **crash** · violates `format_histogram_counts(bin_counts).count(chr(10)) + 1 == len(bin_counts)` · input `bin_counts=[( 0.0, # or any other generated value 0, )]` · ZeroDivisionError
- **crash** · violates `format_histogram_counts(bin_counts, width=w1) and format_histogram_counts(bin_counts, width=w2) both have identical number of lines regardless of width, only bar length/padding differs` · input `# The test always failed when commented parts were varied together. bin_counts=[( 0.0, # or any other generated value 0,` · ZeroDivisionError

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

### b.timeutils.strpdate  (3 finding(s))
- _prop_ `strpdate(date.strftime(format), format) == date  (for a date object formatted with a given format string)`  [round_trip, conf 0.6]
- _prop_ `isinstance(strpdate(string, format), date)`  [type_postcondition, conf 0.95]
- _prop_ `strpdate(string, format) does not raise for any string/format pair matching a valid strptime pattern`  [totality, conf 0.3]
- **crash** · violates `strpdate(date.strftime(format), format) == date  (for a date object formatted with a given format string)` · input `d=datetime.date(999, 1, 1)` · ValueError
- **crash** · violates `isinstance(strpdate(string, format), date)` · input `d=datetime.date(999, 1, 1)` · ValueError
- **crash** · violates `strpdate(string, format) does not raise for any string/format pair matching a valid strptime pattern` · input `d=datetime.date(999, 1, 1)` · ValueError

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

### b.funcutils.partial_ordering  (2 finding(s))
- _prop_ `for instances a,b of the decorated class: (a < b) == (a <= b and not a >= b); (a > b) == (a >= b and not a <= b); (a == b) == (a <= b and a >= b)`  [invariant_preservation, conf 0.85]
- _prop_ `for instances a,b: a < b implies not (b < a); a == b implies not (a < b) and not (b < a)`  [metamorphic, conf 0.6]
- _prop_ `partial_ordering(cls) returns a class object (isinstance(partial_ordering(cls), type)) with __lt__, __gt__, __eq__ attributes present`  [type_postcondition, conf 0.8]
- _prop_ `partial_ordering(partial_ordering(cls)) behaves the same as partial_ordering(cls) since existing methods aren't overridden`  [idempotence, conf 0.5]
- _prop_ `partial_ordering(cls) does not raise for any class cls defining __le__ and __ge__`  [totality, conf 0.4]
- **crash** · violates `for instances a,b of the decorated class: (a < b) == (a <= b and not a >= b); (a > b) == (a >= b and not a <= b); (a == b) == (a <= b and a >= b)` · input `cls=main.A` · TypeError
- **crash** · violates `for instances a,b: a < b implies not (b < a); a == b implies not (a < b) and not (b < a)` · input `cls=main.A` · TypeError

```python
def partial_ordering(cls):
    """Class decorator, similar to :func:`functools.total_ordering`,
    except it is used to define `partial orderings`_ (i.e., it is
    possible that *x* is neither greater than, equal to, or less than
    *y*). It assumes the presence of the ``__le__()`` and ``__ge__()``
    method, but nothing else. It will not override any existing
    additional comparison methods.

    .. _partial orderings: https://en.wikipedia.org/wiki/Partially_ordered_set

    >>> @partial_ordering
    ... class MySet(set):
    ...     def __le__(self, other):
    ...         return self.issubset(other)
    ...     def __ge__(self, other):
    ...         return self.issuperset(other)
    ...
    >>> a = MySet([1,2,3])
    >>> b = MySet([1,2])
    >>> c = MySet([1,2,4])
    >>> b < a
    True
    >>> b > a
    False
    >>> b < c
    True
    >>> a < c
    False
    >>> c > a
    False
    """

    def __lt__(self, other):
        return self <= other and (not self >= other)

    def __gt__(self, other):
        return self >= other and (not self <= other)

    def __eq__(self, other):
        return self >= other and self <= other
    if not hasattr(cls, '__lt__'):
        cls.__lt__ = __lt__
    if not hasattr(cls, '__gt__'):
        cls.__gt__ = __gt__
    if not hasattr(cls, '__eq__'):
        cls.__eq__ = __eq__
    return cls
```

### b.mathutils.ceil  (2 finding(s))
- _prop_ `ceil(x) >= x`  [value_postcondition, conf 0.9]
- _prop_ `ceil(x, options) in options and ceil(x, options) >= x`  [value_postcondition, conf 0.85]
- _prop_ `ceil(ceil(x)) == ceil(x)`  [idempotence, conf 0.8]
- _prop_ `ceil(x, options) == ceil(y, options) for any y with the same ceil-bucket, e.g. if x <= y <= ceil(x, options) then ceil(y, options) == ceil(x, options)`  [metamorphic, conf 0.6]
- _prop_ `ceil(x) does not raise for any finite int/float x`  [totality, conf 0.4]
- **crash** · violates `ceil(x, options) in options and ceil(x, options) >= x` · input `x=0, options=[-1]` · ValueError
- **crash** · violates `ceil(x, options) == ceil(y, options) for any y with the same ceil-bucket, e.g. if x <= y <= ceil(x, options) then ceil(y, options) == ceil(x, options)` · input `x=0, options=[-1]` · ValueError

```python
def ceil(x, options=None):
    """Return the ceiling of *x*. If *options* is set, return the smallest
    integer or float from *options* that is greater than or equal to
    *x*.

    Args:
        x (int or float): Number to be tested.
        options (iterable): Optional iterable of arbitrary numbers
          (ints or floats).

    >>> VALID_CABLE_CSA = [1.5, 2.5, 4, 6, 10, 25, 35, 50]
    >>> ceil(3.5, options=VALID_CABLE_CSA)
    4
    >>> ceil(4, options=VALID_CABLE_CSA)
    4
    """
    from math import ceil as _ceil
    import bisect
    if options is None:
        return _ceil(x)
    options = sorted(options)
    i = bisect.bisect_left(options, x)
    if i == len(options):
        raise ValueError('no ceil options greater than or equal to: %r' % x)
    return options[i]
```

### stringcase.alphanumcase  (2 finding(s))
- _prop_ `alphanumcase(alphanumcase(s)) == alphanumcase(s)`  [idempotence, conf 0.95]
- _prop_ `set(alphanumcase(s)) <= set(c for c in s if c.isalnum())`  [invariant_preservation, conf 0.85]
- _prop_ `all(c.isalnum() for c in alphanumcase(s))`  [value_postcondition, conf 0.9]
- _prop_ `isinstance(alphanumcase(s), str)`  [type_postcondition, conf 0.9]
- _prop_ `alphanumcase(s) does not raise for any str input s`  [totality, conf 0.6]
- **property_violation** · violates `set(alphanumcase(s)) <= set(c for c in s if c.isalnum())` · input `string='_'` · AssertionError
- **property_violation** · violates `all(c.isalnum() for c in alphanumcase(s))` · input `string='_'` · AssertionError

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

### b.strutils.under2camel  (1 finding(s))
- _prop_ `isinstance(under2camel(s), str)`  [type_postcondition, conf 0.9]
- _prop_ `under2camel(under2camel(s)) == under2camel(s) when s has no underscores left after first conversion`  [idempotence, conf 0.4]
- _prop_ `len(under2camel(s)) <= len(s)`  [invariant_preservation, conf 0.5]
- _prop_ `under2camel(s) == under2camel(s.lower())`  [metamorphic, conf 0.3]
- _prop_ `under2camel(s) does not raise for any string input s`  [totality, conf 0.6]
- **property_violation** · violates `len(under2camel(s)) <= len(s)` · input `under_string=''` · AssertionError

```python
def under2camel(under_string):
    """Converts an underscored string to camelcased. Useful for turning a
    function name into a class name.

    >>> under2camel('complex_tokenizer')
    'ComplexTokenizer'
    """
    return ''.join((w.capitalize() or '_' for w in under_string.split('_')))
```

### b.strutils.unwrap_text  (1 finding(s))
- _prop_ `unwrap_text(unwrap_text(text)) == unwrap_text(text)`  [idempotence, conf 0.6]
- _prop_ `unwrap_text(text, ending=None) == unwrap_text(text).split(ending) roughly; more precisely ending.join(unwrap_text(text, ending=None)) == unwrap_text(text, ending=ending)`  [metamorphic, conf 0.7]
- _prop_ `unwrap_text(text) contains no consecutive whitespace runs beyond single spaces within a paragraph, and no leading/trailing whitespace on lines`  [invariant_preservation, conf 0.5]
- _prop_ `isinstance(unwrap_text(text), str) when ending is not None, isinstance(unwrap_text(text, ending=None), list)`  [type_postcondition, conf 0.85]
- _prop_ `unwrap_text(text) does not raise for any string input text`  [totality, conf 0.4]
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
- _prop_ `isinstance(to_text(obj, maxlen), str)`  [type_postcondition, conf 0.9]
- _prop_ `maxlen is None or len(to_text(obj, maxlen)) <= maxlen`  [value_postcondition, conf 0.75]
- _prop_ `to_text(obj, maxlen) does not raise for any obj (including ones whose __str__/__repr__ raise) and any maxlen value`  [totality, conf 0.85]
- **property_violation** · violates `maxlen is None or len(to_text(obj, maxlen)) <= maxlen` · input `obj=None, maxlen=0` · AssertionError

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

### inflection.camelize  (1 finding(s))
- _prop_ `camelize(underscore(s)) == s (approximately, for typical snake_case-derived strings)`  [round_trip, conf 0.55]
- _prop_ `camelize(camelize(s)) == camelize(s)`  [idempotence, conf 0.5]
- _prop_ `camelize(s, True)[0].islower() == False and camelize(s, False)[0].islower() == True (first letter case depends only on the flag)`  [metamorphic, conf 0.7]
- _prop_ `isinstance(camelize(string, uppercase_first_letter), str)`  [type_postcondition, conf 0.9]
- _prop_ `camelize(string, uppercase_first_letter) does not raise for any non-empty str input`  [totality, conf 0.4]
- **crash** · violates `camelize(s, True)[0].islower() == False and camelize(s, False)[0].islower() == True (first letter case depends only on the flag)` · input `| string='ʟ', |` · ExceptionGroup

```python
def camelize(string: str, uppercase_first_letter: bool=True) -> str:
    """
    Convert strings to CamelCase.

    Examples::

        >>> camelize("device_type")
        'DeviceType'
        >>> camelize("device_type", False)
        'deviceType'

    :func:`camelize` can be thought of as a inverse of :func:`underscore`,
    although there are some cases where that does not hold::

        >>> camelize(underscore("IOError"))
        'IoError'

    :param uppercase_first_letter: if set to `True` :func:`camelize` converts
        strings to UpperCamelCase. If set to `False` :func:`camelize` produces
        lowerCamelCase. Defaults to `True`.
    """
    import re
    if uppercase_first_letter:
        return re.sub('(?:^|_)(.)', lambda m: m.group(1).upper(), string)
    else:
        return string[0].lower() + camelize(string)[1:]
```

### inflection.ordinal  (1 finding(s))
- _prop_ `ordinal(number) in {'st', 'nd', 'rd', 'th'}`  [value_postcondition, conf 0.9]
- _prop_ `ordinal(number) == ordinal(-number)`  [metamorphic, conf 0.85]
- _prop_ `ordinal(number) == ordinal(number + 100)`  [metamorphic, conf 0.8]
- _prop_ `isinstance(ordinal(number), str)`  [type_postcondition, conf 0.9]
- _prop_ `ordinal(number) does not raise for any integer input, including negative, zero, and large values`  [totality, conf 0.6]
- **property_violation** · violates `ordinal(number) == ordinal(number + 100)` · input `number=-1` · AssertionError

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

### stringcase.lowercase  (1 finding(s))
- _prop_ `lowercase(lowercase(s)) == lowercase(s)`  [idempotence, conf 0.95]
- _prop_ `lowercase(s) == lowercase(s).lower() and lowercase(s) has no uppercase characters`  [value_postcondition, conf 0.85]
- _prop_ `lowercase(s) == lowercase(s.upper())`  [metamorphic, conf 0.75]
- _prop_ `len(lowercase(s)) == len(str(s))`  [invariant_preservation, conf 0.5]
- _prop_ `isinstance(lowercase(s), str)`  [type_postcondition, conf 0.9]
- _prop_ `lowercase(s) does not raise for any input coercible via str()`  [totality, conf 0.4]
- **property_violation** · violates `lowercase(s) == lowercase(s.upper())` · input `string='ß'` · AssertionError

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
- _prop_ `uppercase(uppercase(s)) == uppercase(s)`  [idempotence, conf 0.95]
- _prop_ `len(uppercase(s)) == len(str(s))`  [invariant_preservation, conf 0.6]
- _prop_ `uppercase(s) == uppercase(s.lower())`  [metamorphic, conf 0.8]
- _prop_ `uppercase(s) == uppercase(s).upper()  (i.e., no lowercase alphabetic characters remain in output)`  [value_postcondition, conf 0.85]
- _prop_ `isinstance(uppercase(s), str)`  [type_postcondition, conf 0.9]
- _prop_ `uppercase(s) does not raise for any input coercible via str()`  [totality, conf 0.5]
- **property_violation** · violates `len(uppercase(s)) == len(str(s))` · input `string='ß'` · AssertionError

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
b.dictutils.subdict, b.formatutils.construct_format_field_str, b.funcutils.dir_dict, b.funcutils.format_invocation, b.funcutils.format_nonexp_repr, b.funcutils.get_module_callables, b.funcutils.inspect_formatargspec, b.funcutils.mro_items, b.iterutils.backoff_iter, b.iterutils.frange, b.iterutils.is_iterable, b.iterutils.lstrip_iter, b.iterutils.rstrip_iter, b.iterutils.xfrange, b.mathutils.clamp, b.mathutils.floor, b.strutils.a10n, b.strutils.args2cmd, b.strutils.format_int_list, b.strutils.gunzip_bytes, b.strutils.gzip_bytes, b.strutils.human_readable_list, b.strutils.is_ascii, b.strutils.is_uuid, b.strutils.parse_int_list, b.strutils.removeprefix, b.typeutils.issubclass, b.urlutils.resolve_path_parts, inflection.dasherize, inflection.humanize, inflection.transliterate, inflection.underscore, stringcase.trimcase

## Errored
- [504] b.timeutils.daterange — {'detail': 'Test execution exceeded the 30.0s time budget.'}
