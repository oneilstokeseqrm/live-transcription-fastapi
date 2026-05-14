from workers.advisory_lock import lock_key_for_queue_id


def test_lock_key_is_deterministic():
    a = lock_key_for_queue_id("queue-id-1")
    b = lock_key_for_queue_id("queue-id-1")
    assert a == b


def test_lock_key_fits_int8():
    key = lock_key_for_queue_id("queue-id-1")
    assert -(2**63) <= key < 2**63
