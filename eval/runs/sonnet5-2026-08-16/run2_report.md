# Evaluation sweep — report

**49 functions** run across **13 modules** · **15 flagged** · 34 clean · 0 errored · ~$1.8492 LLM cost.

Discovered 146 public functions; 49 were self-contained (imports-only); ran 49 (cap 80). 97 skipped (companion fn / module constant).

## Per-module
| module | run | flagged |
|---|---|---|
| b.dictutils | 1 | 0 |
| b.formatutils | 1 | 0 |
| b.funcutils | 8 | 3 |
| b.iterutils | 7 | 2 |
| b.mathutils | 3 | 1 |
| b.statsutils | 1 | 0 |
| b.strutils | 12 | 2 |
| b.tableutils | 1 | 1 |
| b.timeutils | 2 | 1 |
| b.typeutils | 2 | 1 |
| b.urlutils | 1 | 0 |
| inflection | 6 | 1 |
| stringcase | 4 | 3 |

## Flagged candidates (manual verification needed)

### b.funcutils.copy_function  (5 finding(s))
- _prop_ `copy_function(orig).__code__ == orig.__code__ and copy_function(orig).__name__ == orig.__name__ and copy_function(orig).__defaults__ == orig.__defaults__`  [invariant_preservation, conf 0.85]
- _prop_ `copy_function(orig, copy_dict=True).__dict__ == orig.__dict__`  [invariant_preservation, conf 0.8]
- _prop_ `copy_function(orig) is not orig`  [value_postcondition, conf 0.9]
- _prop_ `isinstance(copy_function(orig), type(orig))`  [type_postcondition, conf 0.8]
- _prop_ `copy_function(f) does not raise for any well-formed function f`  [totality, conf 0.4]
- **property_violation** · violates `copy_function(orig).__code__ == orig.__code__ and copy_function(orig).__name__ == orig.__name__ and copy_function(orig).__defaults__ == orig.__defaults__` · input `orig=lambda: 1` · AssertionError
- **property_violation** · violates `copy_function(orig, copy_dict=True).__dict__ == orig.__dict__` · input `orig=lambda: 1` · AssertionError
- **property_violation** · violates `copy_function(orig) is not orig` · input `orig=lambda: 1` · AssertionError
- **property_violation** · violates `isinstance(copy_function(orig), type(orig))` · input `orig=lambda: 1` · AssertionError
- **property_violation** · violates `copy_function(f) does not raise for any well-formed function f` · input `orig=lambda: 1, copy_dict=False` · AssertionError

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
- _prop_ `all(issubclass(t, cls) for t in get_all_subclasses(cls))`  [value_postcondition, conf 0.85]
- _prop_ `len(get_all_subclasses(cls)) == len(set(get_all_subclasses(cls)))`  [invariant_preservation, conf 0.6]
- _prop_ `sorted(get_all_subclasses(cls), key=id) == sorted(get_all_subclasses(cls), key=id) across repeated calls with no class changes`  [idempotence, conf 0.4]
- _prop_ `get_all_subclasses(cls) does not raise for any cls that is a type object (raises only TypeError for non-type inputs, which is documented)`  [totality, conf 0.5]
- **crash** · violates `isinstance(get_all_subclasses(cls), list) and all(isinstance(t, type) for t in get_all_subclasses(cls))` · input `cls=object` · TypeError
- **crash** · violates `all(issubclass(t, cls) for t in get_all_subclasses(cls))` · input `cls=object` · TypeError
- **crash** · violates `len(get_all_subclasses(cls)) == len(set(get_all_subclasses(cls)))` · input `cls=object` · TypeError
- **crash** · violates `sorted(get_all_subclasses(cls), key=id) == sorted(get_all_subclasses(cls), key=id) across repeated calls with no class changes` · input `cls=object` · TypeError
- **crash** · violates `get_all_subclasses(cls) does not raise for any cls that is a type object (raises only TypeError for non-type inputs, which is documented)` · input `cls=object` · TypeError

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

### b.timeutils.strpdate  (4 finding(s))
- _prop_ `strpdate(date.strftime(fmt), fmt) == date  (for a date object formatted then parsed)`  [round_trip, conf 0.7]
- _prop_ `isinstance(strpdate(string, format), datetime.date) and not isinstance(strpdate(string, format), datetime.datetime)`  [type_postcondition, conf 0.9]
- _prop_ `strpdate(s, fmt).year/month/day derived only from date-related specifiers; varying time-only fields (e.g. %H, %M, %S, %f) in the input string while keeping date fields fixed should not change the result`  [metamorphic, conf 0.75]
- _prop_ `strpdate(string, format) does not raise for any string/format pair where string matches format's specifiers validly`  [totality, conf 0.3]
- **crash** · violates `strpdate(date.strftime(fmt), fmt) == date  (for a date object formatted then parsed)` · input `d=datetime.date(999, 1, 1)` · ValueError
- **crash** · violates `isinstance(strpdate(string, format), datetime.date) and not isinstance(strpdate(string, format), datetime.datetime)` · input `d=datetime.date(999, 1, 1)` · ValueError
- **crash** · violates `strpdate(s, fmt).year/month/day derived only from date-related specifiers; varying time-only fields (e.g. %H, %M, %S, %f) in the input string while keeping date fields fixed should not change the result` · input `# The test always failed when commented parts were varied together. d=datetime.date(999, 1, 1), h=0, # or any other gene` · ValueError
- **crash** · violates `strpdate(string, format) does not raise for any string/format pair where string matches format's specifiers validly` · input `d=datetime.date(999, 1, 1)` · ValueError

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

### b.iterutils.flatten_iter  (3 finding(s))
- _prop_ `list(flatten_iter(list(flatten_iter(nested)))) == list(flatten_iter(nested))`  [idempotence, conf 0.85]
- _prop_ `sorted(list(flatten_iter(nested))) == sorted of all leaf (non-iterable/str/bytes) elements collected via manual recursive walk of nested`  [invariant_preservation, conf 0.75]
- _prop_ `list(flatten_iter([a, b])) == list(flatten_iter([a])) + list(flatten_iter([b]))`  [metamorphic, conf 0.7]
- _prop_ `hasattr(flatten_iter(nested), '__iter__') and hasattr(flatten_iter(nested), '__next__')`  [type_postcondition, conf 0.8]
- _prop_ `list(flatten_iter(nested)) does not raise for any reasonably nested iterable input (finite depth, no cycles)`  [totality, conf 0.4]
- **crash** · violates `list(flatten_iter(list(flatten_iter(nested)))) == list(flatten_iter(nested))` · input `iterable=0` · TypeError
- **crash** · violates `sorted(list(flatten_iter(nested))) == sorted of all leaf (non-iterable/str/bytes) elements collected via manual recursive walk of nested` · input `iterable=0` · TypeError
- **crash** · violates `list(flatten_iter(nested)) does not raise for any reasonably nested iterable input (finite depth, no cycles)` · input `iterable=0` · TypeError

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

### b.funcutils.partial_ordering  (2 finding(s))
- _prop_ `for instances a,b of the decorated class: (a < b) == (a <= b and not a >= b); (a > b) == (a >= b and not a <= b); (a == b) == (a <= b and a >= b)`  [invariant_preservation, conf 0.9]
- _prop_ `partial_ordering(cls) is cls and cls retains any pre-existing __lt__/__gt__/__eq__ methods unchanged (decorator does not override existing comparisons)`  [invariant_preservation, conf 0.85]
- _prop_ `for instances a,b: a < b implies not (a > b), and a == b implies not (a < b) and not (a > b) (antisymmetry/mutual exclusivity of partial order relations)`  [metamorphic, conf 0.6]
- _prop_ `partial_ordering(cls) does not raise for any class cls that defines __le__ and __ge__`  [totality, conf 0.4]
- **crash** · violates `for instances a,b of the decorated class: (a < b) == (a <= b and not a >= b); (a > b) == (a >= b and not a <= b); (a == b) == (a <= b and a >= b)` · input `# The test always failed when commented parts were varied together. cls=main.A0, va=0, # or any other generated value vb` · TypeError
- **crash** · violates `for instances a,b: a < b implies not (a > b), and a == b implies not (a < b) and not (a > b) (antisymmetry/mutual exclusivity of partial order relations)` · input `# The test always failed when commented parts were varied together. cls=main.A0, va=0, # or any other generated value vb` · TypeError

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

### b.strutils.under2camel  (2 finding(s))
- _prop_ `isinstance(under2camel(s), str)`  [type_postcondition, conf 0.9]
- _prop_ `under2camel(under2camel(s)) == under2camel(s) is NOT generally guaranteed since camelcased string has no underscores, so applying again just capitalizes the whole word again — actually under2camel(under2camel(s)) == under2camel(s) holds because no more '_' remain to split on.`  [idempotence, conf 0.5]
- _prop_ `under2camel(s) == '' or under2camel(s)[0].isupper()`  [value_postcondition, conf 0.6]
- _prop_ `under2camel(s) does not raise for any string input s`  [totality, conf 0.5]
- **property_violation** · violates `under2camel(under2camel(s)) == under2camel(s) is NOT generally guaranteed since camelcased string has no underscores, so applying again just capitalizes the whole word again — actually under2camel(under2camel(s)) == under2camel(s) holds because no more '_' remain to split on.` · input `under_string=''` · AssertionError
- **property_violation** · violates `under2camel(s) == '' or under2camel(s)[0].isupper()` · input `under_string=''` · AssertionError

```python
def under2camel(under_string):
    """Converts an underscored string to camelcased. Useful for turning a
    function name into a class name.

    >>> under2camel('complex_tokenizer')
    'ComplexTokenizer'
    """
    return ''.join((w.capitalize() or '_' for w in under_string.split('_')))
```

### inflection.camelize  (2 finding(s))
- _prop_ `camelize(underscore(s)) == s (for typical snake_case-derived strings)`  [round_trip, conf 0.5]
- _prop_ `camelize(camelize(s)) == camelize(s)`  [idempotence, conf 0.5]
- _prop_ `isinstance(camelize(s, upper), str)`  [type_postcondition, conf 0.9]
- _prop_ `camelize(s, True)[0].isupper() and camelize(s, False)[0].islower() (for non-empty alphabetic-starting strings)`  [metamorphic, conf 0.7]
- _prop_ `camelize(s) does not raise for any non-empty string s`  [totality, conf 0.3]
- **property_violation** · violates `camelize(camelize(s)) == camelize(s)` · input `string='____'` · AssertionError
- **property_violation** · violates `camelize(s, True)[0].isupper() and camelize(s, False)[0].islower() (for non-empty alphabetic-starting strings)` · input `string='ĸ'` · AssertionError

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

### stringcase.lowercase  (2 finding(s))
- _prop_ `lowercase(lowercase(s)) == lowercase(s)`  [idempotence, conf 0.95]
- _prop_ `lowercase(s) == lowercase(s).lower() and lowercase(s).islower() or not any(c.isalpha() for c in lowercase(s))`  [value_postcondition, conf 0.7]
- _prop_ `lowercase(s.upper()) == lowercase(s)`  [metamorphic, conf 0.85]
- _prop_ `isinstance(lowercase(s), str)`  [type_postcondition, conf 0.9]
- _prop_ `lowercase(x) does not raise for any x convertible via str()`  [totality, conf 0.5]
- **property_violation** · violates `lowercase(s) == lowercase(s).lower() and lowercase(s).islower() or not any(c.isalpha() for c in lowercase(s))` · input `string='ก'` · AssertionError
- **property_violation** · violates `lowercase(s.upper()) == lowercase(s)` · input `string='µ'` · AssertionError

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

### stringcase.uppercase  (2 finding(s))
- _prop_ `uppercase(uppercase(s)) == uppercase(s)`  [idempotence, conf 0.95]
- _prop_ `uppercase(s) == uppercase(s).upper() and uppercase(s) has no lowercase alphabetic characters`  [value_postcondition, conf 0.85]
- _prop_ `uppercase(s) == uppercase(s.lower())`  [metamorphic, conf 0.8]
- _prop_ `isinstance(uppercase(s), str)`  [type_postcondition, conf 0.9]
- _prop_ `len(uppercase(s)) == len(str(s))`  [invariant_preservation, conf 0.6]
- _prop_ `uppercase(s) does not raise for any input coercible via str()`  [totality, conf 0.5]
- **property_violation** · violates `uppercase(s) == uppercase(s).upper() and uppercase(s) has no lowercase alphabetic characters` · input `string='º'` · AssertionError
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

### b.funcutils.format_nonexp_repr  (1 finding(s))
- _prop_ `isinstance(format_nonexp_repr(obj, req_names, opt_names, opt_key), str)`  [type_postcondition, conf 0.9]
- _prop_ `format_nonexp_repr(obj).startswith('<' + obj.__class__.__name__) and format_nonexp_repr(obj).endswith('>')`  [value_postcondition, conf 0.85]
- _prop_ `format_nonexp_repr(obj) does not raise for any obj with default req_names/opt_names/opt_key`  [totality, conf 0.5]
- _prop_ `format_nonexp_repr(obj, req_names, opt_names) produces the same output regardless of duplicate names in req_names+opt_names (since duplicates are deduplicated)`  [metamorphic, conf 0.4]
- **crash** · violates `isinstance(format_nonexp_repr(obj, req_names, opt_names, opt_key), str)` · input `obj=None, req_names=None, opt_names=['length'], opt_key=lambda k, v: True` · TypeError

```python
def format_nonexp_repr(obj, req_names=None, opt_names=None, opt_key=None):
    """Format a non-expression-style repr

    Some object reprs look like object instantiation, e.g., App(r=[], mw=[]).

    This makes sense for smaller, lower-level objects whose state
    roundtrips. But a lot of objects contain values that don't
    roundtrip, like types and functions.

    For those objects, there is the non-expression style repr, which
    mimic's Python's default style to make a repr like so:

    >>> class Flag(object):
    ...    def __init__(self, length, width, depth=None):
    ...        self.length = length
    ...        self.width = width
    ...        self.depth = depth
    ...
    >>> flag = Flag(5, 10)
    >>> print(format_nonexp_repr(flag, ['length', 'width'], ['depth']))
    <Flag length=5 width=10>

    If no attributes are specified or set, utilizes the id, not unlike Python's
    built-in behavior.

    >>> print(format_nonexp_repr(flag))
    <Flag id=...>
    """
    cn = obj.__class__.__name__
    req_names = req_names or []
    opt_names = opt_names or []
    uniq_names, all_names = (set(), [])
    for name in req_names + opt_names:
        if name in uniq_names:
            continue
        uniq_names.add(name)
        all_names.append(name)
    if opt_key is None:
        opt_key = lambda v: v is None
    assert callable(opt_key)
    items = [(name, getattr(obj, name, None)) for name in all_names]
    labels = [f'{name}={val!r}' for name, val in items if not (name in opt_names and opt_key(val))]
    if not labels:
        labels = ['id=%s' % id(obj)]
    ret = '<{} {}>'.format(cn, ' '.join(labels))
    return ret
```

### b.iterutils.backoff_iter  (1 finding(s))
- _prop_ `all(start <= x <= stop for x in backoff_iter(start, stop, count=n, jitter=False)) for start<=stop, start>=0`  [value_postcondition, conf 0.75]
- _prop_ `len(list(backoff_iter(start, stop, count=n))) == n for integer n >= 0`  [value_postcondition, conf 0.8]
- _prop_ `list(backoff_iter(start, stop, count=n))[-1] == stop when n is large enough to reach the cap`  [metamorphic, conf 0.7]
- _prop_ `sequence values are non-decreasing: list(backoff_iter(start, stop))[i] <= list(backoff_iter(start, stop))[i+1] when jitter=False`  [metamorphic, conf 0.75]
- _prop_ `backoff_iter(start, stop, count, factor, jitter) does not raise for valid domain (start>=0, stop>=start>0, factor>=1, -1<=jitter<=1, count>=0 or 'repeat')`  [totality, conf 0.5]
- _prop_ `all(isinstance(x, float) for x in backoff_iter(start, stop, count=n))`  [type_postcondition, conf 0.7]
- **crash** · violates `backoff_iter(start, stop, count, factor, jitter) does not raise for valid domain (start>=0, stop>=start>0, factor>=1, -1<=jitter<=1, count>=0 or 'repeat')` · input `# The test always failed when commented parts were varied together. start=1.0, # or any other generated value stop=1.0, ` · ZeroDivisionError

```python
def backoff_iter(start, stop, count=None, factor=2.0, jitter=False):
    """Generates a sequence of geometrically-increasing floats, suitable
    for usage with `exponential backoff`_. Starts with *start*,
    increasing by *factor* until *stop* is reached, optionally
    stopping iteration once *count* numbers are yielded. *factor*
    defaults to 2. In general retrying with properly-configured
    backoff creates a better-behaved component for a larger service
    ecosystem.

    .. _exponential backoff: https://en.wikipedia.org/wiki/Exponential_backoff

    >>> list(backoff_iter(1.0, 10.0, count=5))
    [1.0, 2.0, 4.0, 8.0, 10.0]
    >>> list(backoff_iter(1.0, 10.0, count=8))
    [1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0, 10.0]
    >>> list(backoff_iter(0.25, 100.0, factor=10))
    [0.25, 2.5, 25.0, 100.0]

    A simplified usage example:

    .. code-block:: python

      for timeout in backoff_iter(0.25, 5.0):
          try:
              res = network_call()
              break
          except Exception as e:
              log(e)
              time.sleep(timeout)

    An enhancement for large-scale systems would be to add variation,
    or *jitter*, to timeout values. This is done to avoid a thundering
    herd on the receiving end of the network call.

    Finally, for *count*, the special value ``'repeat'`` can be passed to
    continue yielding indefinitely.

    Args:

        start (float): Positive number for baseline.
        stop (float): Positive number for maximum.
        count (int): Number of steps before stopping
            iteration. Defaults to the number of steps between *start* and
            *stop*. Pass the string, `'repeat'`, to continue iteration
            indefinitely.
        factor (float): Rate of exponential increase. Defaults to `2.0`,
            e.g., `[1, 2, 4, 8, 16]`.
        jitter (float): A factor between `-1.0` and `1.0`, used to
            uniformly randomize and thus spread out timeouts in a distributed
            system, avoiding rhythm effects. Positive values use the base
            backoff curve as a maximum, negative values use the curve as a
            minimum. Set to 1.0 or `True` for a jitter approximating
            Ethernet's time-tested backoff solution. Defaults to `False`.

    """
    import math
    import random
    start = float(start)
    stop = float(stop)
    factor = float(factor)
    if start < 0.0:
        raise ValueError('expected start >= 0, not %r' % start)
    if factor < 1.0:
        raise ValueError('expected factor >= 1.0, not %r' % factor)
    if stop == 0.0:
        raise ValueError('expected stop >= 0')
    if stop < start:
        raise ValueError('expected stop >= start, not %r' % stop)
    if count is None:
        denom = start if start else 1
        count = 1 + math.ceil(math.log(stop / denom, factor))
        count = count if start else count + 1
    if count != 'repeat' and count < 0:
        raise ValueError('count must be positive or "repeat", not %r' % count)
    if jitter:
        jitter = float(jitter)
        if not -1.0 <= jitter <= 1.0:
            raise ValueError('expected jitter -1 <= j <= 1, not: %r' % jitter)
    cur, i = (start, 0)
    while count == 'repeat' or i < count:
        if not jitter:
            cur_ret = cur
        elif jitter:
            cur_ret = cur - cur * jitter * random.random()
        yield cur_ret
        i += 1
        if cur == 0:
            cur = 1
        elif cur < stop:
            cur *= factor
        if cur > stop:
            cur = stop
    return
```

### b.mathutils.floor  (1 finding(s))
- _prop_ `floor(x) <= x and floor(x) > x - 1  (when options is None)`  [value_postcondition, conf 0.85]
- _prop_ `when options is provided and a valid floor exists, floor(x, options) <= x and floor(x, options) in options`  [value_postcondition, conf 0.85]
- _prop_ `floor(floor(x)) == floor(x)`  [idempotence, conf 0.6]
- _prop_ `for x1 <= x2, floor(x1) <= floor(x2) (monotonicity), and same for options-based floor`  [metamorphic, conf 0.7]
- _prop_ `floor(x) does not raise for any numeric x when options is None; raises ValueError only when options given and no valid floor exists`  [totality, conf 0.4]
- **property_violation** · violates `value_postcondition_no_options` · input `x=-2.774559395707103e-19` · AssertionError

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

### b.strutils.unwrap_text  (1 finding(s))
- _prop_ `unwrap_text(unwrap_text(text)) == unwrap_text(text)`  [idempotence, conf 0.6]
- _prop_ `unwrap_text(text with extra spaces/blank lines inserted within a paragraph) == unwrap_text(text)`  [metamorphic, conf 0.6]
- _prop_ `isinstance(unwrap_text(text), str) if ending is not None else isinstance(unwrap_text(text, ending=None), list)`  [type_postcondition, conf 0.85]
- _prop_ `'\n' not in each paragraph produced (no unstripped internal newlines within a single paragraph segment)`  [value_postcondition, conf 0.5]
- _prop_ `unwrap_text(text) does not raise for any string input`  [totality, conf 0.4]
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
- _prop_ `to_text(obj, maxlen) does not raise for any obj (including ones whose __str__/__repr__ raise) and any maxlen`  [totality, conf 0.85]
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

### stringcase.alphanumcase  (1 finding(s))
- _prop_ `alphanumcase(alphanumcase(s)) == alphanumcase(s)`  [idempotence, conf 0.95]
- _prop_ `set(alphanumcase(s)) <= set(c for c in s if c.isalnum() or c == '_')`  [invariant_preservation, conf 0.8]
- _prop_ `all(c.isalnum() for c in alphanumcase(s)) or len(alphanumcase(s)) == 0`  [value_postcondition, conf 0.6]
- _prop_ `isinstance(alphanumcase(s), str)`  [type_postcondition, conf 0.9]
- _prop_ `alphanumcase(s) does not raise for any string input s`  [totality, conf 0.5]
- **property_violation** · violates `all(c.isalnum() for c in alphanumcase(s)) or len(alphanumcase(s)) == 0` · input `string='_'` · AssertionError

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

## Clean (no findings)
b.dictutils.subdict, b.formatutils.construct_format_field_str, b.funcutils.dir_dict, b.funcutils.format_invocation, b.funcutils.get_module_callables, b.funcutils.inspect_formatargspec, b.funcutils.mro_items, b.iterutils.frange, b.iterutils.is_iterable, b.iterutils.lstrip_iter, b.iterutils.rstrip_iter, b.iterutils.xfrange, b.mathutils.ceil, b.mathutils.clamp, b.statsutils.format_histogram_counts, b.strutils.a10n, b.strutils.args2cmd, b.strutils.format_int_list, b.strutils.gunzip_bytes, b.strutils.gzip_bytes, b.strutils.human_readable_list, b.strutils.is_ascii, b.strutils.is_uuid, b.strutils.parse_int_list, b.strutils.removeprefix, b.timeutils.daterange, b.typeutils.issubclass, b.urlutils.resolve_path_parts, inflection.dasherize, inflection.humanize, inflection.ordinal, inflection.transliterate, inflection.underscore, stringcase.trimcase

## Errored
