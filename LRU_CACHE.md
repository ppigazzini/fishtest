# LRUCache (server/fishtest/lru_cache.py)

This repository uses a small, custom `LRUCache` implementation instead of `functools.lru_cache()`.
The goals are:

- **Bounded memory** via a configurable `size`
- **Optional expiry** via `expiration` (seconds)
- **Thread-safety** for concurrent server threads
- **Stable iteration** (no `RuntimeError` from concurrent mutation)

## Data model

Internally the cache stores:

- An `OrderedDict` mapping `key -> (value, atime_ns)`
- `atime_ns` is the *last access time* (nanoseconds)

The `OrderedDict` order is **least recently used (oldest) → most recently used (newest)**.

Operations update recency:

- `__setitem__` inserts/updates and moves the key to the end.
- `__getitem__` returns the value and also moves the key to the end.

This means expiry is **sliding** for `cache[key]`: an entry’s lifetime is measured from its most recent `__getitem__`/`__setitem__`, not from its initial insertion.

Note: `key in cache` checks membership without “touching” the entry (it does not refresh recency/expiry), but it may delete the key if it has expired.

## Why monotonic time

Expiry is checked using `time.monotonic_ns()` rather than `time.time()`.

- `time.time()` can jump due to NTP/clock adjustments.
- `time.monotonic_ns()` is monotonic and safe for “elapsed time since” checks.

The `expiration` field remains in seconds for readability/config compatibility.

## Thread-safety

All state changes are protected by an internal `threading.RLock`.

The lock is also exported via the `.lock` property for callers that need to perform multi-step operations atomically (example: “check then set”).

## Snapshot iteration

A key requirement in the server is that iterating over the cache must not crash if other threads modify it.

Python’s `OrderedDict` (like `dict`) raises at runtime if mutated during iteration.

To avoid this, `LRUCache` implements iteration helpers that iterate over **snapshots**:

- `__iter__()` returns an iterator over a snapshot list of keys.
- `items()` returns an iterator over a snapshot list of `(key, value)` pairs.
- `values()` returns an iterator over a snapshot list of values.

The snapshot is built while holding the lock; the returned iterable is safe to use after the lock is released.

## Input validation

- `size` must be `None` or a non-negative integer.
- `expiration` must be `None` or a finite number (seconds). Values `<= 0` mean “expire immediately”.

## Expiration edge cases

- `expiration is None`: entries never expire.
- `expiration <= 0`: entries are treated as immediately expired (purge clears the cache).

This matches the existing test expectations where setting `expiration = 0` empties the cache.

## Interaction with schema validation

Some server schemas expect a plain `dict`.
When validating cache contents, validate a snapshot like:

- `dict(cache.items())`

rather than passing the `LRUCache` instance directly.

## Architecture and how it works (all processes)

### Components

- **Storage**: an `OrderedDict` named `__data` storing `key -> (value, atime_ns)`.
- **Clock**: `time.monotonic_ns()` for computing elapsed-time expiry.
- **Lock**: a `threading.RLock` (`.lock`) guarding all internal state.

### Invariants

- `__data` is ordered from **least recently used** (left) to **most recently used** (right).
- `atime_ns` is the last-touch time used for expiry checks.
- All reads/writes that inspect or mutate `__data` run under the lock.

### Processes

#### Insert / update (`cache[key] = value`)

1. Acquire the lock.
2. Store `(value, now_ns)` for `key`.
3. Move `key` to the end (most recently used).
4. Run purge:
	- Enforce `size` by popping from the left while oversized.
	- Enforce `expiration` by removing expired keys from the left until the first non-expired entry.
5. Release the lock.

#### Lookup (`cache[key]`)

1. Acquire the lock.
2. Read `(value, atime_ns)`.
3. If `expiration` is set:
	- If `expiration <= 0`, delete and raise `KeyError`.
	- If expired, delete and raise `KeyError`.
4. Touch the entry (LRU + sliding TTL): move `key` to the end and update `atime_ns` to `now_ns`.
5. Return `value` and release the lock.

#### Membership (`key in cache`)

1. Acquire the lock.
2. If missing, return `False`.
3. If `expiration is None`, return `True`.
4. If `expiration <= 0` or expired, delete the key and return `False`.
5. Return `True`.

Note: membership does **not** touch/move-to-end the entry.

#### Purge (`cache.purge()` and internal purge calls)

Purge enforces both constraints:

- **Size**: evict least-recently-used entries first (`popitem(last=False)`).
- **Expiration**:
  - `None`: no expiry.
  - `<= 0`: clear cache.
  - Otherwise: compute a cutoff time and delete expired entries from the left until the first non-expired entry.

#### Iteration (`iter(cache)`, `cache.items()`, `cache.values()`)

To avoid `RuntimeError` from concurrent mutation, iteration is snapshot-based:

1. Acquire the lock.
2. Purge.
3. Build a snapshot list of keys/items/values.
4. Release the lock.
5. Return an iterator over the snapshot.

This ensures iteration is stable even if other threads mutate the cache after the iterator is created.

## Open issues / nitpicks

- **Cold-miss stampede in callers**: if a caller releases the cache lock to do slow work (e.g., network I/O), multiple threads can still do the same slow work concurrently on a cold miss. Fixing this requires a per-key “in-flight” mechanism (condition/future) rather than a simple cache.
- **Time-based expiry tests can be flaky**: any test that relies on `sleep()` and wall-clock timing can fail under extreme scheduler/CI load. If this becomes a problem, inject a clock or mock `monotonic_ns()`.
- **`key in cache` has side effects**: membership checks may delete expired keys. This is intentional (keeps the structure clean) but can surprise users during debugging.
- **Snapshot iteration allocates**: `items()`/`values()`/`__iter__()` allocate snapshot lists to guarantee iteration safety. This is usually fine for small caches, but it is an O(n) allocation per call.
